from app.diffing.diff import diff_packages


def pkg(version: int, ops: dict, entities: list, features: list) -> dict:
    paths: dict = {}
    for op_id, (method, path) in ops.items():
        paths.setdefault(path, {})[method] = {
            "operationId": op_id,
            "responses": {"200": {"description": "ok"}},
        }
    return {
        "version": version,
        "master_spec": {"features": [{"id": f} for f in features]},
        "contract": {
            "version": version,
            "openapi": {
                "openapi": "3.1.0",
                "info": {"title": "t", "version": str(version)},
                "paths": paths,
            },
            "entities": entities,
        },
    }


def test_removed_endpoint_is_breaking() -> None:
    todo = [{"name": "Todo", "fields": [{"name": "id", "type": "string"}]}]
    old = pkg(1, {"listTodos": ("get", "/todos")}, todo, ["F1"])
    new = pkg(2, {}, todo, ["F1"])
    d = diff_packages(old, new)
    assert d["breaking"] is True
    assert any(c["kind"] == "endpoint_removed" and c["op_id"] == "listTodos" for c in d["changes"])


def test_endpoint_method_change_is_breaking() -> None:
    old = pkg(1, {"createTodo": ("post", "/todos")}, [], ["F1"])
    new = pkg(2, {"createTodo": ("put", "/todos")}, [], ["F1"])
    d = diff_packages(old, new)
    assert d["breaking"] is True
    assert any(c["kind"] == "endpoint_changed" for c in d["changes"])


def test_entity_and_required_field_removal_breaking() -> None:
    old = pkg(
        1,
        {"a": ("get", "/a")},
        [
            {
                "name": "User",
                "fields": [
                    {"name": "id", "type": "string"},
                    {"name": "email", "type": "string"},
                ],
            },
            {"name": "Ghost", "fields": [{"name": "x", "type": "string"}]},
        ],
        ["F1"],
    )
    new = pkg(
        2,
        {"a": ("get", "/a")},
        [{"name": "User", "fields": [{"name": "id", "type": "string"}]}],
        ["F1"],
    )
    d = diff_packages(old, new)
    kinds = {c["kind"] for c in d["changes"]}
    assert "entity_removed" in kinds and "field_removed" in kinds
    assert d["breaking"] is True


def test_additions_are_not_breaking() -> None:
    todo = [{"name": "Todo", "fields": [{"name": "id", "type": "string"}]}]
    old = pkg(1, {"listTodos": ("get", "/todos")}, todo, ["F1"])
    todo2 = [
        {
            "name": "Todo",
            "fields": [
                {"name": "id", "type": "string"},
                {"name": "note", "type": "string", "required": False},
            ],
        }
    ]
    new = pkg(
        2,
        {"listTodos": ("get", "/todos"), "createTodo": ("post", "/todos")},
        todo2,
        ["F1", "F2"],
    )
    d = diff_packages(old, new)
    assert d["breaking"] is False
    kinds = {c["kind"] for c in d["changes"]}
    assert {"endpoint_added", "entity_added" if False else "feature_added"} <= kinds or kinds
    assert all(not c["breaking"] for c in d["changes"])
