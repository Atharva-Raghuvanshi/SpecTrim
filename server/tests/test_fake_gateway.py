from app.gateway.fake import FakeGateway


def test_canned_response_per_stage_and_default() -> None:
    gw = FakeGateway(canned={"analyzer": '{"ambiguities": []}'}, default="{}")
    analyzer = gw.complete("analyzer", [{"role": "user", "content": "hi"}])
    assert analyzer.text == '{"ambiguities": []}'
    assert gw.complete("contract", [{"role": "user", "content": "hi"}]).text == "{}"


def test_records_calls_for_assertions() -> None:
    gw = FakeGateway()
    gw.complete("classifier", [{"role": "user", "content": "abc"}])
    assert len(gw.calls) == 1
    assert gw.calls[0]["stage"] == "classifier"
    assert gw.calls[0]["messages"] == [{"role": "user", "content": "abc"}]


def test_usage_fields_present_and_nonzero() -> None:
    gw = FakeGateway(canned={"analyzer": "hello world"})
    res = gw.complete("analyzer", [{"role": "user", "content": "some prompt text"}])
    assert res.usage.prompt_tokens > 0
    assert res.usage.completion_tokens > 0
    assert res.usage.total_tokens == res.usage.prompt_tokens + res.usage.completion_tokens
    assert res.model == "fake-model"
