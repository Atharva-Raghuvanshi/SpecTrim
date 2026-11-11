"""DB-backed pipeline runner: state machine + stage persistence + resume (M4.3/M4.4).

Every state change goes through transition(); stages are skipped on resume
only when status='done' AND input_hash matches (PRD 5.8). Stage hashes chain:
each stage's hash covers its upstream stages' computed hashes, so the chain is
reproducible across processes without extra reads.
"""
from __future__ import annotations

from typing import Any, Callable

from app.gateway.base import CompletionResult, Gateway
from app.pipeline.hashing import input_hash
from app.pipeline.prompts import render
from app.pipeline.states import State, transition
from app.schemas.package import Contract, MasterSpec, SpecPackage, ValidationReport
from app.stages.analyzer import IdeaAnalysis, run_analyzer
from app.stages.classifier import ClassifierOutput, run_classifier
from app.stages.contract import run_contract
from app.stages.decomposer import run_decomposer
from app.stages.question_gate import GateOutcome, run_question_gate
from app.stages.spec_writer import run_spec_writer
from app.storage.repos import (
    EventsRepo,
    QuestionsRepo,
    SessionsRepo,
    StagesRepo,
    VersionsRepo,
)
from app.validators.consistency import check_consistency
from app.validators.openapi_lint import lint_openapi

CONFIG_VERSION = "single_model"


class PipelineContext:
    def __init__(self, pool, gateway: Gateway, max_questions: int = 3) -> None:
        self.gateway = gateway
        self.max_questions = max_questions
        self.sessions = SessionsRepo(pool)
        self.stages = StagesRepo(pool)
        self.versions = VersionsRepo(pool)
        self.questions = QuestionsRepo(pool)
        self.events = EventsRepo(pool)

    def model_for(self, stage_key: str) -> str:
        from app.gateway.config import load_models_config

        return load_models_config().for_stage(stage_key).model


def _ih(ctx: PipelineContext, stage_key: str, upstream: dict[str, str], payload: dict | None) -> str:
    prompt_version, _ = render(stage_key)
    model = ctx.model_for(stage_key)
    return input_hash(stage_key, prompt_version, model, CONFIG_VERSION, upstream, payload)


async def run_stage(
    ctx: PipelineContext,
    session_id: str,
    stage_key: str,
    ih: str,
    fn: Callable[[], tuple[Any, CompletionResult]],
) -> dict[str, Any]:
    """Resume-aware stage execution; returns the stage output dict.

    Stage functions are synchronous (the gateway protocol is sync); DB I/O
    around them is async.
    """
    existing = await ctx.stages.get_done(session_id, stage_key)
    if existing is not None and existing["input_hash"] == ih:
        return dict(existing["output"])
    model_obj, res = fn()
    await ctx.stages.save(
        session_id,
        stage_key,
        ih,
        model_obj.model_dump(),
        res.model,
        res.usage.prompt_tokens,
        res.usage.completion_tokens,
        res.cost_usd,
        res.latency_ms,
    )
    await ctx.sessions.add_spend(session_id, res.cost_usd or 0.0)
    return model_obj.model_dump()


async def _move(ctx: PipelineContext, session_id: str, target: State) -> None:
    session = await ctx.sessions.get(session_id)
    assert session is not None, f"session {session_id} vanished"
    current = State(session["status"])
    transition(current, target)
    await ctx.sessions.set_status(session_id, target.value)
    await ctx.events.append(
        session_id, "state_changed", {"from": current.value, "to": target.value}
    )


async def run_pipeline_job(ctx: PipelineContext, session_id: str) -> str:
    """Drive one session from its current state toward ready/awaiting_user.

    Returns the state it ended in. Safe to re-run: done stages with matching
    input_hash are skipped.
    """
    idea = await ctx.sessions.idea(session_id)
    session = await ctx.sessions.get(session_id)
    if session is None:
        raise KeyError(session_id)
    status = State(session["status"])
    if status in (State.ready, State.failed, State.cancelled):
        return status.value  # nothing to do without a revision request (Phase 5)
    if status is State.queued:
        await _move(ctx, session_id, State.analyzing)

    ih_analyzer = _ih(ctx, "analyzer", {}, {"idea": idea})
    analyzer_out = await run_stage(
        ctx, session_id, "analyzer", ih_analyzer, lambda: run_analyzer(ctx.gateway, idea)
    )
    analysis = IdeaAnalysis.model_validate(analyzer_out)

    ih_classifier = _ih(ctx, "classifier", {"analyzer": ih_analyzer}, None)
    classifier_out = await run_stage(
        ctx, session_id, "classifier", ih_classifier, lambda: run_classifier(ctx.gateway, analysis)
    )
    labels = ClassifierOutput.model_validate(classifier_out)
    gate: GateOutcome = run_question_gate(analysis, labels.labels, max_questions=ctx.max_questions)

    if gate.questions:
        await ctx.questions.upsert(session_id, [q.model_dump() for q in gate.questions])
    unanswered = await ctx.questions.list_unanswered(session_id)
    if unanswered:
        session = await ctx.sessions.get(session_id)
        assert session is not None
        if State(session["status"]) is State.analyzing:
            await _move(ctx, session_id, State.awaiting_user)
        return State.awaiting_user.value

    answers = await ctx.questions.answers(session_id)
    session = await ctx.sessions.get(session_id)
    assert session is not None
    if State(session["status"]) is State.awaiting_user:
        await _move(ctx, session_id, State.analyzing)

    await _move(ctx, session_id, State.drafting)
    ih_spec = _ih(
        ctx, "spec_writer",
        {"analyzer": ih_analyzer, "classifier": ih_classifier}, {"answers": answers},
    )
    spec_out = await run_stage(
        ctx, session_id, "spec_writer", ih_spec,
        lambda: run_spec_writer(ctx.gateway, idea, answers, [a.model_dump() for a in gate.assumptions]),
    )
    master = MasterSpec.model_validate(spec_out)

    ih_contract = _ih(ctx, "contract", {"spec_writer": ih_spec}, None)
    contract_out = await run_stage(
        ctx, session_id, "contract", ih_contract, lambda: run_contract(ctx.gateway, master.model_dump())
    )
    contract = Contract.model_validate(contract_out)

    ih_decomp = _ih(ctx, "decomposer", {"spec_writer": ih_spec, "contract": ih_contract}, None)
    subs_out = await run_stage(
        ctx, session_id, "decomposer", ih_decomp, lambda: run_decomposer(ctx.gateway, master, contract)
    )

    await _move(ctx, session_id, State.validating)
    existing_version = await ctx.versions.latest_version(session_id)
    version = (existing_version or 0) + 1
    draft = SpecPackage(
        session_id=session_id,
        version=version,
        master_spec=master,
        assumptions=gate.assumptions,
        open_questions=[],
        sub_specs=subs_out.get("sub_specs", {}),
        contract=contract,
        validation=ValidationReport(warnings=[]),
    )
    lint_errors = lint_openapi(contract.openapi)
    consistency_warnings = check_consistency(draft)
    draft.validation = ValidationReport(
        schema_ok=True,
        openapi_lint_ok=not lint_errors,
        consistency_ok=not consistency_warnings,
        warnings=lint_errors + consistency_warnings,
    )
    inserted = await ctx.versions.insert(session_id, version, draft.model_dump(), "initial")
    if inserted:
        await ctx.events.append(session_id, "version_created", {"version": version})
    await _move(ctx, session_id, State.ready)
    await ctx.sessions.set_status(session_id, State.ready.value, current_version=version)
    return State.ready.value


async def cancel_session(ctx: PipelineContext, session_id: str) -> None:
    session = await ctx.sessions.get(session_id)
    if session is None:
        raise KeyError(session_id)
    await _move(ctx, session_id, State.cancelled)
