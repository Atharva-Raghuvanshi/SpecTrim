import pytest
from app.schemas.envelope import MessageEnvelope, MessageType
from app.schemas.package import Contract, Feature, SpecPackage, ValidationReport
from pydantic import ValidationError


def envelope(**over) -> dict:
    base = {
        "id": "msg_1",
        "from": "user",
        "to": "agent",
        "type": "request",
        "payload": {"idea": "todo app"},
    }
    base.update(over)
    return base


def test_envelope_valid_roundtrip() -> None:
    env = MessageEnvelope.model_validate(envelope())
    assert env.type is MessageType.request
    assert env.from_ == "user"
    assert env.schema_version == "1.0"


def test_envelope_bad_id_rejected() -> None:
    with pytest.raises(ValidationError, match="msg_"):
        MessageEnvelope.model_validate(envelope(id="abc"))


def test_envelope_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(envelope(type="gossip"))


def test_envelope_empty_payload_rejected() -> None:
    with pytest.raises(ValidationError, match="payload"):
        MessageEnvelope.model_validate(envelope(payload={}))


def test_envelope_empty_from_rejected() -> None:
    with pytest.raises(ValidationError, match="from"):
        MessageEnvelope.model_validate(envelope(**{"from": "  "}))


def minimal_package(**over) -> dict:
    base = {
        "session_id": "ses_abc123",
        "version": 1,
        "master_spec": {
            "summary": "A todo app",
            "features": [{"id": "F1", "title": "Add todo", "acceptance": ["POST /todos works"]}],
        },
        "sub_specs": {
            "backend": {
                "component": "backend",
                "endpoints": [
                    {"op_id": "createTodo", "method": "post", "path": "/todos", "uses_entities": ["Todo"]}
                ],
                "entities": ["Todo"],
                "contract_version": 1,
            }
        },
        "contract": {
            "version": 1,
            "openapi": {
                "openapi": "3.1.0",
                "info": {"title": "todo", "version": "1.0.0"},
                "paths": {
                    "/todos": {
                        "post": {
                            "operationId": "createTodo",
                            "responses": {"200": {"description": "ok"}},
                        }
                    }
                },
            },
            "entities": [{"name": "Todo", "fields": [{"name": "id", "type": "string"}]}],
        },
        "validation": {"warnings": []},
    }
    base.update(over)
    return base


def test_package_valid() -> None:
    pkg = SpecPackage.model_validate(minimal_package())
    assert pkg.validation.schema_ok is True
    assert pkg.sub_specs["backend"].endpoints[0].op_id == "createTodo"


def test_package_bad_session_id_rejected() -> None:
    with pytest.raises(ValidationError, match="ses_"):
        SpecPackage.model_validate(minimal_package(session_id="nope"))


def test_package_empty_feature_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Feature(id="F1", title="", acceptance=["x"])


def test_package_feature_bad_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Feature(id="feature-1", title="t", acceptance=["a"])


def test_contract_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Contract(version=0, openapi={}, entities=[{"name": "T", "fields": [{"name": "a", "type": "str"}]}])


def test_validation_defaults() -> None:
    rep = ValidationReport(warnings=[])
    assert rep.openapi_lint_ok and rep.consistency_ok and rep.schema_ok
