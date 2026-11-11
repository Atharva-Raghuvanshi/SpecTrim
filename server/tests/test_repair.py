import pytest
from app.gateway.base import GatewayError
from app.gateway.fake import FakeGateway
from app.gateway.repair import complete_validated, extract_json
from pydantic import BaseModel


class Out(BaseModel):
    name: str
    count: int


def test_extract_json_tolerates_fences_and_prose() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": 2} hope that helps') == {"a": 2}
    assert extract_json('{"a": 3}') == {"a": 3}


def test_first_attempt_valid_no_repair() -> None:
    gw = FakeGateway(canned={"extractor": '{"name": "x", "count": 2}'})
    out, res = complete_validated(gw, "extractor", [], Out)
    assert out.count == 2
    assert len(gw.calls) == 1


def test_repair_retry_succeeds() -> None:
    gw = FakeGateway(canned={"extractor": "not json at all"})
    gw.set_response("extractor_repair", '{"name": "x", "count": 1}')
    # repair call goes to the same stage; use a queue-style fake via subclass
    replies = iter(["not json at all", '{"name": "x", "count": 1}'])

    class Queue(FakeGateway):
        def complete(self, stage, messages):  # type: ignore[override]
            self.calls.append({"stage": stage, "messages": messages})
            text = next(replies)
            from app.gateway.base import CompletionResult, Usage

            return CompletionResult(
                stage=stage,
                model="fake",
                text=text,
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                latency_ms=0,
                cost_usd=0.0,
            )

    qgw = Queue()
    out, _ = complete_validated(qgw, "extractor", [{"role": "user", "content": "go"}], Out)
    assert out.name == "x"
    assert len(qgw.calls) == 2
    repair_prompt = qgw.calls[1]["messages"][-1]["content"]
    assert "STRICT JSON" in repair_prompt
    assert "not json at all" in repair_prompt


def test_second_failure_is_permanent() -> None:
    replies = iter(["bad one", "bad two"])

    class Queue(FakeGateway):
        def complete(self, stage, messages):  # type: ignore[override]
            self.calls.append({"stage": stage, "messages": messages})
            from app.gateway.base import CompletionResult, Usage

            return CompletionResult(
                stage=stage,
                model="fake",
                text=next(replies),
                usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
                latency_ms=0,
                cost_usd=0.0,
            )

    with pytest.raises(GatewayError, match="permanent"):
        complete_validated(Queue(), "extractor", [], Out)
