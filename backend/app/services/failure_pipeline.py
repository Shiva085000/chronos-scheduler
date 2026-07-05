"""Multi-agent failure analysis — a LangGraph pipeline over DLQ errors.

Four specialist agents, wired as a StateGraph with conditional routing:

    START → triage ──(transient)──────────────→ remediate → compose → END
                └────(permanent / code / config)→ diagnose ──┘

- **triage** classifies the failure (transient | permanent | code | config).
  Transient failures (timeouts, connection resets, 5xx from a dependency)
  skip deep diagnosis — the interesting answer is "requeue it", and
  skipping a model call halves latency and cost for the most common case.
- **diagnose** does root-cause analysis over the traceback.
- **remediate** proposes one concrete, category-appropriate fix.
- **compose** writes the 2–3 sentence operator-facing note stored on the
  job and shown in the dashboard.

The LLM is injected (anything with `ainvoke(messages) -> .content`), so
the graph's routing and prompts are unit-tested with a scripted fake and
no network. Model wiring lives in AISummaryService, which also keeps the
Redis cache and the degrade-to-None behavior.
"""

from typing import Literal, TypedDict

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

logger = structlog.get_logger(__name__)

CATEGORIES = ("transient", "permanent", "code", "config")


class FailureState(TypedDict, total=False):
    # inputs
    task_name: str
    error_text: str
    attempt_count: int
    # produced by the agents
    category: str
    diagnosis: str
    remediation: str
    summary: str


TRIAGE_PROMPT = (
    "You are a triage agent for a distributed job scheduler. Classify the "
    "failure into exactly one category:\n"
    "- transient: network blips, timeouts, connection resets, dependency "
    "5xx — likely to succeed on requeue\n"
    "- permanent: the input or external state makes success impossible\n"
    "- code: a bug in the task handler (TypeError, KeyError, assertion...)\n"
    "- config: missing/invalid credentials, endpoints or settings\n"
    "Reply with ONLY the category word."
)

DIAGNOSE_PROMPT = (
    "You are a root-cause analyst. Given a job failure's traceback, state "
    "the most likely root cause in one short sentence. Name the failing "
    "operation and why it failed; no speculation beyond the evidence."
)

REMEDIATE_PROMPT = (
    "You are an SRE remediation agent. Given a failure's category and "
    "root cause, propose exactly ONE concrete next action for the operator "
    "(e.g. 'requeue the job', 'fix the payload field X', 'rotate the API "
    "key'). One short sentence."
)

COMPOSE_PROMPT = (
    "You write concise incident notes for a job dashboard. Combine the "
    "material into 2-3 plain-English sentences: what failed, why, and the "
    "recommended action. No preamble, no markdown."
)


def _msgs(system: str, user: str) -> list:
    return [SystemMessage(content=system), HumanMessage(content=user)]


def _text(response) -> str:
    content = response.content
    if isinstance(content, list):  # some providers return content parts
        content = " ".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return content.strip()


def build_failure_graph(llm):
    """Compile the pipeline around any LLM exposing `ainvoke`."""

    async def triage(state: FailureState) -> FailureState:
        raw = _text(
            await llm.ainvoke(
                _msgs(
                    TRIAGE_PROMPT,
                    f"Task: {state['task_name']}\n"
                    f"Attempts: {state['attempt_count']}\n"
                    f"Error:\n{state['error_text'][:2000]}",
                )
            )
        ).lower()
        category = next((c for c in CATEGORIES if c in raw), "permanent")
        return {"category": category}

    async def diagnose(state: FailureState) -> FailureState:
        diagnosis = _text(
            await llm.ainvoke(
                _msgs(
                    DIAGNOSE_PROMPT,
                    f"Task: {state['task_name']}\n"
                    f"Traceback:\n{state['error_text'][:2000]}",
                )
            )
        )
        return {"diagnosis": diagnosis}

    async def remediate(state: FailureState) -> FailureState:
        remediation = _text(
            await llm.ainvoke(
                _msgs(
                    REMEDIATE_PROMPT,
                    f"Category: {state['category']}\n"
                    f"Root cause: {state.get('diagnosis', 'not analyzed — transient')}\n"
                    f"Task: {state['task_name']}, failed "
                    f"{state['attempt_count']} attempts",
                )
            )
        )
        return {"remediation": remediation}

    async def compose(state: FailureState) -> FailureState:
        summary = _text(
            await llm.ainvoke(
                _msgs(
                    COMPOSE_PROMPT,
                    f"Task: {state['task_name']} "
                    f"(failed {state['attempt_count']} attempts)\n"
                    f"Category: {state['category']}\n"
                    f"Root cause: {state.get('diagnosis', 'likely transient')}\n"
                    f"Recommended action: {state['remediation']}",
                )
            )
        )
        return {"summary": f"[{state['category']}] {summary}"}

    def route_after_triage(state: FailureState) -> Literal["diagnose", "remediate"]:
        return "remediate" if state["category"] == "transient" else "diagnose"

    graph = StateGraph(FailureState)
    graph.add_node("triage", triage)
    graph.add_node("diagnose", diagnose)
    graph.add_node("remediate", remediate)
    graph.add_node("compose", compose)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges("triage", route_after_triage)
    graph.add_edge("diagnose", "remediate")
    graph.add_edge("remediate", "compose")
    graph.add_edge("compose", END)
    return graph.compile()


async def analyze_failure(
    llm, *, task_name: str, error_text: str, attempt_count: int
) -> str | None:
    """Run the pipeline; returns the operator summary (or None on failure)."""
    graph = build_failure_graph(llm)
    result: FailureState = await graph.ainvoke(
        {
            "task_name": task_name,
            "error_text": error_text,
            "attempt_count": attempt_count,
        }
    )
    summary = result.get("summary")
    if summary:
        logger.info(
            "failure_pipeline.analyzed",
            task_name=task_name,
            category=result.get("category"),
        )
    return summary or None
