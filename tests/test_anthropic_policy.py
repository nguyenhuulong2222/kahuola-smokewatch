"""AnthropicPolicy: same rails as Bedrock, usage in the trace, key read once from SSM."""
from types import SimpleNamespace as NS

from smokewatch import agent
from smokewatch.store import LocalStore
from tests.test_fire import _episode


class _FakeMessages:
    """messages.create stub: a one-scan review attempt (refused by the rail), track, review."""

    def __init__(self, steps):
        self.steps, self.requests = steps, []

    def create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})   # snapshot: list grows
        content = self.steps[len(self.requests) - 1]
        return NS(content=content, stop_reason="tool_use" if content[-1].type == "tool_use" else "end_turn",
                  usage=NS(input_tokens=2000, output_tokens=60))


def _tool(i, name, **inp):
    return NS(type="tool_use", id=f"t{i}", name=name, input=inp)


def _policy(steps):
    pol = agent.AnthropicPolicy.__new__(agent.AnthropicPolicy)
    pol.model_id, pol.usage = "claude-sonnet-5-5", []
    pol.client = NS(messages=_FakeMessages(steps))
    pol.tools = [{"name": n, "description": d, "input_schema": s} for n, d, s in agent._tool_schemas()]
    return pol


def test_anthropic_policy_rails_and_usage(tmp_path):
    pol = _policy([
        [_tool(1, "request_human_review", det_id=1, confidence=0.9, rationale="looks hot")],
        [_tool(2, "track_plume", det_id=1, minutes_back=10)],
        [_tool(3, "request_human_review", det_id=1, confidence=0.8, rationale="+30 K, 3/3 scans")],
    ])
    ep = _episode(store=LocalStore(str(tmp_path)))
    pol.run(ep)
    reqs = pol.client.messages.requests
    # review before track_plume is refused in code and reported back as an error result
    first_result = reqs[1]["messages"][-1]["content"][0]
    assert first_result["is_error"] and "track_plume" in first_result["content"]
    assert ep.outcome[1]["action"] == "request_human_review"
    assert all("temperature" not in r for r in reqs) and reqs[0]["max_tokens"] == 8000
    assert agent._llm_totals(ep.trace)["calls"] == 3 and agent._llm_totals(ep.trace)["input_tokens"] == 6000


def test_anthropic_outage_falls_back_to_rules(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRMS_MAP_KEY", raising=False)
    monkeypatch.delenv("FIRMS_ARCHIVE_DIR", raising=False)

    class Boom:
        def create(self, **kw):
            raise RuntimeError("api down")
    pol = _policy([])
    pol.client = NS(messages=Boom())
    ep = _episode()
    s = agent.run_scan(ep.frame, ep.src, LocalStore(str(tmp_path)), pol, background=ep.bg)
    assert s["outcomes"][1]["action"] == "request_human_review"
    assert any(t.get("fallback") == "rules" and "api down" in t["error"] for t in s["trace"])


def test_key_read_once_from_ssm(monkeypatch):
    calls = []

    class FakeSSM:
        def get_parameter(self, Name, WithDecryption):
            calls.append((Name, WithDecryption))
            return {"Parameter": {"Value": "k" * 40}}
    import boto3
    monkeypatch.setattr(boto3, "client", lambda svc, **kw: FakeSSM())
    monkeypatch.setenv("ANTHROPIC_KEY_PARAM", "/smokewatch/anthropic_api_key")
    agent._anthropic_key.cache_clear()
    assert agent._anthropic_key() == agent._anthropic_key()
    assert calls == [("/smokewatch/anthropic_api_key", True)]
    agent._anthropic_key.cache_clear()


def test_make_policy_names(monkeypatch):
    monkeypatch.setenv("AGENT_POLICY", "rules")
    assert agent.make_policy().name == "rules"
    assert agent.make_policy("unknown").name == "rules"


def test_successful_tool_with_null_error_is_not_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(agent.Episode, "t_query_firms",
                        lambda self, det_id: {"count": 3, "error": None})
    pol = _policy([
        [_tool(1, "query_firms", det_id=1)],
        [_tool(2, "dismiss", det_id=1, rationale="test")],
    ])
    pol.run(_episode(store=LocalStore(str(tmp_path))))
    result = pol.client.messages.requests[1]["messages"][-1]["content"][0]
    assert "is_error" not in result
