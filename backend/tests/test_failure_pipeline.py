"""Unit tests for the multi-agent LangGraph failure-analysis pipeline.

A scripted fake LLM stands in for Gemini, so these pin the *graph*: which
agents run, in what order, how triage's verdict routes the flow, and what
reaches the composed summary — with no network involved.
"""

from dataclasses import dataclass

from app.services.failure_pipeline import analyze_failure, build_failure_graph


@dataclass
class _Reply:
    content: str


class ScriptedLLM:
    """Answers by which agent is asking (recognized via system prompt)."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    async def ainvoke(self, messages) -> _Reply:
        system = messages[0].content
        if "triage agent" in system:
            agent = "triage"
        elif "root-cause analyst" in system:
            agent = "diagnose"
        elif "remediation agent" in system:
            agent = "remediate"
        else:
            agent = "compose"
        self.calls.append(agent)
        return _Reply(self.replies[agent])


async def test_permanent_failure_runs_all_four_agents():
    llm = ScriptedLLM(
        {
            "triage": "code",
            "diagnose": "KeyError on missing payload field 'user_id'.",
            "remediate": "Fix the enqueuer to include user_id.",
            "compose": "The handler crashed on a missing user_id field; "
            "fix the enqueuer payload and requeue.",
        }
    )
    summary = await analyze_failure(
        llm,
        task_name="emails.send",
        error_text="Traceback ... KeyError: 'user_id'",
        attempt_count=3,
    )
    assert llm.calls == ["triage", "diagnose", "remediate", "compose"]
    assert summary.startswith("[code] ")
    assert "user_id" in summary


async def test_transient_failure_skips_diagnosis():
    llm = ScriptedLLM(
        {
            "triage": "transient",
            "remediate": "Requeue the job; the dependency recovered.",
            "compose": "A downstream timeout killed the job; requeue it.",
        }
    )
    summary = await analyze_failure(
        llm,
        task_name="webhooks.deliver",
        error_text="httpx.ConnectTimeout: timed out",
        attempt_count=5,
    )
    assert llm.calls == ["triage", "remediate", "compose"], (
        "transient failures must bypass the diagnosis agent"
    )
    assert summary.startswith("[transient] ")


async def test_unrecognized_triage_defaults_to_permanent():
    llm = ScriptedLLM(
        {
            "triage": "no idea, sorry",
            "diagnose": "d",
            "remediate": "r",
            "compose": "s",
        }
    )
    graph = build_failure_graph(llm)
    result = await graph.ainvoke(
        {"task_name": "t", "error_text": "e", "attempt_count": 1}
    )
    assert result["category"] == "permanent"
    assert "diagnose" in llm.calls, "unknown categories get the deep path"


async def test_diagnosis_feeds_the_remediation_agent():
    seen: dict[str, str] = {}

    class SpyLLM(ScriptedLLM):
        async def ainvoke(self, messages):
            reply = await super().ainvoke(messages)
            if self.calls[-1] == "remediate":
                seen["remediate_input"] = messages[1].content
            return reply

    llm = SpyLLM(
        {
            "triage": "config",
            "diagnose": "The API key is expired.",
            "remediate": "Rotate the key.",
            "compose": "Expired key; rotate it.",
        }
    )
    await analyze_failure(
        llm, task_name="t", error_text="401 Unauthorized", attempt_count=2
    )
    assert "The API key is expired." in seen["remediate_input"], (
        "remediation must be conditioned on the diagnosis"
    )
