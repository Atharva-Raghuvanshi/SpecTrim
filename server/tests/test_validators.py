from app.schemas.package import SpecPackage
from app.validators.consistency import check_consistency, op_ids_of
from app.validators.openapi_lint import lint_openapi
from test_schemas import minimal_package


def good_package() -> SpecPackage:
    return SpecPackage.model_validate(minimal_package())


GOOD_OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1.0.0"},
    "paths": {"/todos": {"post": {"operationId": "createTodo", "responses": {"200": {"description": "ok"}}}}},
}


def test_lint_known_good_openapi_passes() -> None:
    assert lint_openapi(GOOD_OPENAPI) == []


def test_lint_seeded_bad_openapi_fails_with_reason() -> None:
    bad = {"openapi": "9.9", "info": {}, "paths": {}}
    errors = lint_openapi(bad)
    assert errors and "openapi_lint:" in errors[0]


def test_op_ids_extracted() -> None:
    assert op_ids_of(GOOD_OPENAPI) == {"createTodo"}


def test_consistency_known_good_package_passes() -> None:
    assert check_consistency(good_package()) == []


def test_page_referencing_unknown_endpoint_flagged() -> None:
    pkg = SpecPackage.model_validate(
        minimal_package(**{
            "sub_specs": {
                "frontend": {
                    "component": "frontend",
                    "pages": [{"name": "Home", "endpoints": ["missingOp"]}],
                    "contract_version": 1,
                },
                "backend": minimal_package()["sub_specs"]["backend"],
            }
        })
    )
    warnings = check_consistency(pkg)
    assert any("unknown endpoint 'missingOp'" in w for w in warnings)


def test_endpoint_referencing_unknown_entity_flagged() -> None:
    base = minimal_package()
    base["sub_specs"]["backend"]["endpoints"][0]["uses_entities"] = ["Ghost"]
    pkg = SpecPackage.model_validate(base)
    warnings = check_consistency(pkg)
    assert any("unknown entity 'Ghost'" in w for w in warnings)


def test_subspec_entity_missing_from_contract_flagged() -> None:
    base = minimal_package()
    base["sub_specs"]["backend"]["entities"] = ["Phantom"]
    pkg = SpecPackage.model_validate(base)
    warnings = check_consistency(pkg)
    assert any("'Phantom' missing from contract" in w for w in warnings)


def test_feature_without_acceptance_rejected_by_schema() -> None:
    import pytest
    from pydantic import ValidationError

    base = minimal_package()
    base["master_spec"]["features"][0]["acceptance"] = []
    with pytest.raises(ValidationError):
        SpecPackage.model_validate(base)
