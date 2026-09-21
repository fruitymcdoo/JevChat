"""Thin layer between the chat pipeline and Jev.

Everything the bot "says" is the result of typed decisions. This module owns
the one place those decisions are made, so the rest of the app never touches
the SDK directly and every decision lands in a trace the UI can show.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from typing import Any, Mapping, Protocol

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

Question = Choice | Noul | Score


@dataclass
class Decision:
    """One answered question, normalised across Choice / Noul / Score."""

    id: str
    kind: str  # "choice" | "noul" | "score"
    value: Any  # chosen label, yes-probability, or expected score
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class DecisionBatch:
    """All questions answered by one request, plus what it cost."""

    stage: str
    decisions: dict[str, Decision]
    latency_ms: int
    input_tokens: int | None = None
    model: str | None = None
    requests: int = 1

    def to_json(self, labels: Mapping[str, str] | None = None, top: int = 5) -> dict:
        """Trim for the UI: keep only the top few options per Choice."""
        out = {
            "stage": self.stage,
            "requests": self.requests,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "model": self.model,
            "decisions": [],
        }
        labels = labels or {}
        for d in self.decisions.values():
            item = asdict(d)
            ranked = sorted(d.probabilities.items(), key=lambda kv: -kv[1])[:top]
            item["probabilities"] = [
                {"option": k, "label": labels.get(k, k), "p": round(v, 4)} for k, v in ranked
            ]
            item["value_label"] = labels.get(d.value, d.value) if d.kind == "choice" else d.value
            out["decisions"].append(item)
        return out


class Decider(Protocol):
    name: str

    def decide(self, stage: str, state: Any, questions: Mapping[str, Question]) -> DecisionBatch: ...


# A request holds 64k tokens; stay well inside it, estimating ~3 characters per token.
WAVE_TOKEN_BUDGET = 30_000
WAVE_MAX_QUESTIONS = 48
WAVE_WORKERS = 8


def decide_wave(decider: Decider, stage: str, state: Any, questions: Mapping[str, Question]) -> DecisionBatch:
    """Answer many independent questions at once.

    They all share one state, so they go out as one request when they fit and
    as several concurrent requests when they don't. Either way the caller gets
    a single batch back; its latency is wall-clock time for the whole wave.
    """
    state_cost = len(json.dumps(state)) // 3
    chunks: list[dict[str, Question]] = [{}]
    used = 0
    for qid, q in questions.items():
        cost = state_cost + len(json.dumps([q.instructions, dict(q.criteria or {})])) // 3
        if chunks[-1] and (used + cost > WAVE_TOKEN_BUDGET or len(chunks[-1]) >= WAVE_MAX_QUESTIONS):
            chunks.append({})
            used = 0
        chunks[-1][qid] = q
        used += cost

    started = time.perf_counter()
    if len(chunks) == 1:
        batches = [decider.decide(stage, state, chunks[0])]
    else:
        with ThreadPoolExecutor(max_workers=WAVE_WORKERS) as pool:
            batches = list(pool.map(lambda c: decider.decide(stage, state, c), chunks))
    merged: dict[str, Decision] = {}
    for b in batches:
        merged.update(b.decisions)
    tokens = [b.input_tokens for b in batches if b.input_tokens is not None]
    return DecisionBatch(stage, merged, int((time.perf_counter() - started) * 1000),
                         sum(tokens) if tokens else None, batches[0].model, requests=len(batches))


class JevDecider:
    """Asks Jev. Independent questions over the same state go in one request."""

    def __init__(self, model: str | None = None):
        self.client = TypeSafeClient(model=model)  # reads TYPESAFE_API_KEY
        self.name = model or "jev-latest"

    def decide(self, stage, state, questions):
        started = time.perf_counter()
        resp = self.client.system_one(state=state, questions=questions)
        latency = int((time.perf_counter() - started) * 1000)

        decisions: dict[str, Decision] = {}
        for qid, a in resp.choices.items():
            decisions[qid] = Decision(qid, "choice", a.choice, a.confidence, dict(a.probabilities))
        for qid, a in resp.nouls.items():
            decisions[qid] = Decision(qid, "noul", a.noul)
        for qid, a in resp.scores.items():
            probs = {str(k): v for k, v in a.probabilities.items()}
            decisions[qid] = Decision(qid, "score", a.score, a.confidence, probs)
        return DecisionBatch(stage, decisions, latency, resp.usage.input_tokens, resp.model)


class MockDecider:
    """Offline stand-in so the app runs without an API key.

    Scores each Choice option by word overlap with the latest user message.
    It is a wiring aid, not a model: expect clumsy replies.
    """

    name = "mock (no TYPESAFE_API_KEY)"

    def decide(self, stage, state, questions):
        started = time.perf_counter()
        message = state.get("user_message", "") if isinstance(state, dict) else str(state)
        words = set(re.findall(r"[a-z']+", message.lower()))

        decisions: dict[str, Decision] = {}
        for qid, q in questions.items():
            if isinstance(q, Choice):
                raw = {}
                for label, desc in q.criteria.items():
                    text = f"{label} {desc or ''}".lower()
                    raw[label] = 1.0 + len(words & set(re.findall(r"[a-z']+", text)))
                total = sum(raw.values())
                probs = {k: v / total for k, v in raw.items()}
                best = max(probs, key=probs.get)
                decisions[qid] = Decision(qid, "choice", best, probs[best], probs)
            elif isinstance(q, Noul):
                decisions[qid] = Decision(qid, "noul", 0.95)  # wave everything through
            else:
                decisions[qid] = Decision(qid, "score", 0.0, 0.0)
        latency = int((time.perf_counter() - started) * 1000)
        return DecisionBatch(stage, decisions, latency, None, self.name)


def make_decider() -> Decider:
    if os.environ.get("TYPESAFE_API_KEY"):
        return JevDecider(os.environ.get("TYPESAFE_DEFAULT_MODEL"))
    return MockDecider()
