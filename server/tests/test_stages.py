import pytest
from app.gateway.fake import FakeGateway
from app.pipeline.inmemory import NeedsAnswers, run_pipeline
from app.stages.analyzer import run_analyzer
from app.stages.classifier import run_classifier
from app.stages.contract import build_openapi, run_contract
from app.stages.decomposer import run_decomposer
from app.stages.spec_writer import run_spec_writer
from app.validators.openapi_lint import lint_openapi

ANALYSIS_JSON = """
{"goals": ["track habits"], "entities": ["habit"], "features": ["add habit"],
 "constraints": [],
 "ambiguities": [{"id": "AMB1", "text": "single or multi user?",
                  "category": "data_ownership"}]}
"""

LABELS_JSON = """
{"labels": [{"id": "AMB1", "class": "blocking", "category": "data_ownership",
             "reason": "multi-user changes data model", "confidence": 0.9}]}
"""

MASTER_JSON = """
{"summary": "habit tracker", "user_flows": ["add habit"],
 "features": [{"id": "F1", "title": "Add habit", "acceptance": ["POST /habits works"]}],
 "non_functional": []}
"""

CONTRACT_JSON = """
{"title": "Habits API",
 "entities": [{"name": "Habit", "fields": [{"name": "id", "type": "string", "required": true},
                                            {"name": "title", "type": "string", "required": true}]}],
 "endpoints": [{"op_id": "createHabit", "method": "post", "path": "/habits",
                "uses_entities": ["Habit"], "description": "create habit"}]}
"""

DECOMPose_JSON = """
{"sub_specs": {
  "frontend": {"component": "frontend",
               "pages": [{"name": "Home", "endpoints": ["createHabit"]}],
               "endpoints": [], "entities": [], "contract_version": 1},
  "backend": {"component": "backend", "pages": [],
              "endpoints": [{"op_id": "createHabit", "method": "put", "path": "/WRONG",
                             "uses_entities": ["Habit"], "description": ""}],
              "entities": ["Habit"], "contract_version": 1},
  "database": {"component": "database", "pages": [], "endpoints": [],
               "entities": ["Habit"], "contract_version": 1},
  "supporting": {"component": "supporting", "pages": [], "endpoints": [],
                 "entities": [], "contract_version": 1}}}
"""


def canned_gateway() -> FakeGateway:
    return FakeGateway(
        canned={
            "analyzer": ANALYSIS_JSON,
            "classifier": LABELS_JSON,
            "spec_writer": MASTER_JSON,
            "contract": CONTRACT_JSON,
            "decomposer": DECOMPose_JSON,
        }
    )


def test_analyzer_stage_canned_output() -> None:
    analysis, _ = run_analyzer(canned_gateway(), "habit tracker")
    assert analysis.entities == ["habit"]
    assert analysis.ambiguities[0].id == "AMB1"


def test_classifier_stage_canned_output() -> None:
    analysis, _ = run_analyzer(canned_gateway(), "habit tracker")
    out, _ = run_classifier(canned_gateway(), analysis)
    assert out.labels[0].label_class == "blocking"


def test_contract_stage_builds_lintable_openapi() -> None:
    master = {"summary": "s", "features": [{"id": "F1", "title": "t", "acceptance": ["a"]}]}
    contract, _ = run_contract(canned_gateway(), master)
    assert contract.version == 1
    assert lint_openapi(contract.openapi) == []


def test_decomposer_normalizes_endpoints_from_contract() -> None:
    master_obj, _ = run_spec_writer(canned_gateway(), "idea", [], [])
    contract, _ = run_contract(canned_gateway(), master_obj.model_dump())
    subs, _ = run_decomposer(canned_gateway(), master_obj, contract)
    backend = subs["backend"].endpoints[0]
    assert (backend.method, backend.path) == ("post", "/habits")  # corrected from put//WRONG
    assert subs["frontend"].pages[0].endpoints == ["createHabit"]


def test_pipeline_needs_answers_when_blocking_questions() -> None:
    with pytest.raises(NeedsAnswers) as exc_info:
        run_pipeline(canned_gateway(), "ses_abc123", "habit tracker")
    assert exc_info.value.gate.questions[0].category == "data_ownership"


def test_pipeline_produces_valid_package_with_answers() -> None:
    pkg, _ = run_pipeline(
        canned_gateway(), "ses_abc123", "habit tracker", answers=["single user"]
    )
    assert pkg.session_id == "ses_abc123"
    assert pkg.validation.schema_ok is True
    assert pkg.validation.openapi_lint_ok is True
    assert pkg.validation.consistency_ok is True
    assert pkg.validation.warnings == []
    assert pkg.sub_specs["database"].entities == ["Habit"]


def test_build_openapi_shapes_operations() -> None:
    from app.stages.contract import ContractDraft, EndpointDraft

    draft = ContractDraft(
        title="T",
        entities=[],
        endpoints=[
            EndpointDraft(op_id="getThings", method="get", path="/things"),
            EndpointDraft(op_id="deleteThing", method="delete", path="/things/{id}"),
        ],
    )
    doc = build_openapi(draft, version=2)
    assert doc["info"]["version"] == "2"
    assert doc["paths"]["/things"]["get"]["operationId"] == "getThings"
    assert doc["paths"]["/things/{id}"]["delete"]["operationId"] == "deleteThing"
    assert lint_openapi(doc) == []
