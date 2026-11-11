"""In-memory pipeline: idea -> SpecPackage without DB (M3.5 CLI path).

The DB-backed orchestrator (Phase 4) reuses the same stage functions with
persistence and resume; the stage sequence and validation are identical.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.gateway.base import CompletionResult, Gateway
from app.schemas.package import SpecPackage, ValidationReport
from app.stages.analyzer import IdeaAnalysis, run_analyzer
from app.stages.classifier import ClassifierOutput, run_classifier
from app.stages.contract import run_contract
from app.stages.decomposer import run_decomposer
from app.stages.question_gate import GateOutcome, run_question_gate
from app.stages.spec_writer import run_spec_writer
from app.validators.consistency import check_consistency
from app.validators.openapi_lint import lint_openapi


class NeedsAnswers(RuntimeError):
    """Raised when blocking questions must be answered before drafting."""

    def __init__(self, gate: GateOutcome) -> None:
        self.gate = gate
        super().__init__(f"{len(gate.questions)} blocking question(s) need answers")


@dataclass
class PipelineTrace:
    analysis: IdeaAnalysis
    classifier: ClassifierOutput
    gate: GateOutcome
    results: dict[str, CompletionResult]


def run_pipeline(
    gateway: Gateway,
    session_id: str,
    idea: str,
    answers: list[str] | None = None,
    max_questions: int = 3,
    version: int = 1,
) -> tuple[SpecPackage, PipelineTrace]:
    analysis, res_analyzer = run_analyzer(gateway, idea)
    classifier_out, res_classifier = run_classifier(gateway, analysis)
    gate = run_question_gate(analysis, classifier_out.labels, max_questions=max_questions)

    if gate.questions and answers is None:
        raise NeedsAnswers(gate)

    master_spec, res_spec_writer = run_spec_writer(
        gateway, idea, answers or [], [a.model_dump() for a in gate.assumptions]
    )
    contract, res_contract = run_contract(gateway, master_spec.model_dump(), version=version)
    sub_specs, res_decomposer = run_decomposer(gateway, master_spec, contract)

    draft = SpecPackage(
        session_id=session_id,
        version=version,
        master_spec=master_spec,
        assumptions=gate.assumptions,
        open_questions=gate.questions,
        sub_specs=sub_specs,
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
    trace = PipelineTrace(
        analysis=analysis,
        classifier=classifier_out,
        gate=gate,
        results={
            "analyzer": res_analyzer,
            "classifier": res_classifier,
            "spec_writer": res_spec_writer,
            "contract": res_contract,
            "decomposer": res_decomposer,
        },
    )
    return draft, trace
