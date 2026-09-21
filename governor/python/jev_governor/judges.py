"""Judges turn the features into four probabilities; the policy maps those to one bounded move.

- JevJudge:   TypeSafe's System One model Jev answers four yes/no questions (Nouls) in one request.
- RulesJudge: fixed thresholds on the same features. No network, no key; also the baseline that
              any claim about Jev has to beat.
- MockJudge:  scripted answers for tests.
"""
from __future__ import annotations

import json
import os
import sys
import time

from .features import Features

QUESTIONS = ("diverging", "safe_to_accelerate", "stagnating", "stuck_high")

Q_TEXT = {
    "diverging": (
        "Do `residuals` and `observations` show that the iteration is diverging or becoming "
        "unstable, meaning residuals rising persistently or an oscillation that grows, rather than "
        "normal convergence, slow convergence or stagnation?",
        {"true": "The iteration is diverging or becoming unstable.",
         "false": "The iteration is converging, converging slowly, or stagnating without instability."}),
    "safe_to_accelerate": (
        "Is the iteration stable enough that a larger pseudo-time step (a higher momentum "
        "under-relaxation factor) would be safe: the slowest equation is falling or stagnating "
        "without strong oscillation, and no equation is rising?",
        {"true": "Stable enough to take a larger step.",
         "false": "Not safe: something is rising or strongly oscillating."}),
    "stagnating": (
        "Is the slowest equation making little or no progress towards its convergence target "
        "(stagnating or falling only slowly)?",
        {"true": "Progress is slow or stalled.", "false": "Progress is steady or fast."}),
    "stuck_high": (
        "Is the iteration stuck: no progress for a long time according to `observations`, "
        "without diverging, even though the momentum factor has already been raised to a high value "
        "or its limit? (A pseudo-time step that is too large can stall convergence.)",
        {"true": "Stuck despite a high momentum factor.",
         "false": "Not stuck, or the factor is not high."}),
}


class BackendUnavailable(RuntimeError):
    """The judge cannot work and retrying will not help (no key, key rejected, SDK missing)."""


KEY_HELP = ("the jev backend needs a valid TYPESAFE_API_KEY in the environment of the process that starts the "
            "solver (or the sidecar); create one at https://console.typesafe.ai/keys, or use 'backend rules', "
            "which needs no key")


class RulesJudge:
    name = "rules"

    def judge(self, feats: Features, state: dict) -> tuple[dict, dict]:
        fl = feats.fields
        s = feats.slowest
        act = [v for v in fl.values() if not v.bad and v.orders > 0]
        rising = any(v.s100 > 0.05 and v.rise > 0.3 for v in act)
        strong = any(v.osc == "strong oscillation" for v in act)
        blowup = any(v.s100 > 0.5 and v.rise > 1.0 for v in act)
        return {
            "diverging": 1.0 if blowup else (0.7 if (rising or strong) else 0.0),
            "safe_to_accelerate": 0.0 if (rising or strong) else 1.0,
            "stagnating": 1.0 if (s is not None and fl[s].s100 > -0.3) else 0.0,
            "stuck_high": 1.0 if feats.no_progress_iters >= 100 else 0.0,
        }, {}


class MockJudge:
    """Returns scripted probabilities (the last entry repeats)."""
    name = "mock"

    def __init__(self, script: list[dict] | None = None):
        self.script = script or [{"diverging": 0.0, "safe_to_accelerate": 0.0,
                                  "stagnating": 0.0, "stuck_high": 0.0}]
        self.calls = 0

    def judge(self, feats: Features, state: dict) -> tuple[dict, dict]:
        p = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return {q: float(p.get(q, 0.0)) for q in QUESTIONS}, {}


class JevJudge:
    name = "jev"

    def __init__(self, model: str = "jev-1.13.0", client=None, max_backoff: float = 300.0,
                 sleep=time.sleep, questions: dict | None = None):
        try:
            from typesafe_sdk import Noul, TypeSafeClient
        except ImportError as ex:
            raise BackendUnavailable(f"typesafe-sdk is not installed (pip install typesafe-sdk): {ex}") from ex
        if client is None:
            if not os.environ.get("TYPESAFE_API_KEY", "").strip():
                raise BackendUnavailable(f"TYPESAFE_API_KEY is not set: {KEY_HELP}")
            try:
                client = TypeSafeClient(model=model)
            except Exception as ex:  # noqa: BLE001
                raise BackendUnavailable(f"cannot create the TypeSafe client ({ex}): {KEY_HELP}") from ex
        self.client = client
        self.questions = {k: Noul(instructions=t, criteria=c) for k, (t, c) in (questions or Q_TEXT).items()}
        self.max_backoff = max_backoff
        self.sleep = sleep

    def judge(self, feats: Features, state: dict) -> tuple[dict, dict]:
        """Never skips a decision: on API errors it retries with back-off while the solver
        waits (sync mode) or keeps its current settings (async mode). The exception is a rejected
        key (HTTP 401/403): that raises BackendUnavailable and the run continues ungoverned."""
        t0 = time.time()
        response, attempt, outage = None, 0, 0.0
        while response is None:
            try:
                response = self.client.system_one(state=state, questions=self.questions)
            except Exception as ex:  # noqa: BLE001
                if getattr(ex, "status", None) in (401, 403):
                    # a rejected key does not get better by waiting; everything else (rate limits,
                    # exhausted credits, outages) does, and is retried
                    raise BackendUnavailable(f"TYPESAFE_API_KEY was rejected by the API "
                                             f"(HTTP {ex.status}): {KEY_HELP}") from ex
                attempt += 1
                wait = min(self.max_backoff, 2.0 * attempt)
                print(f"jev-governor: API error (attempt {attempt}), retrying in {wait:.0f} s: {ex!r}"[:400],
                      file=sys.stderr, flush=True)
                self.sleep(wait)
                outage += wait
        probs = {k: float(response.answers[k].noul) for k in self.questions}
        info = {"latency_s": round(time.time() - t0 - outage, 3), "model": getattr(response, "model", None),
                "input_tokens": getattr(getattr(response, "usage", None), "input_tokens", None)}
        if attempt:
            info.update(api_retries=attempt, api_outage_s=outage)
        return probs, info


def make_judge(backend: str, model: str = "jev-1.13.0", algorithm: str = "SIMPLE"):
    transient = algorithm == "PIMPLE"
    if transient:
        from .transient import Q_TEXT_T, RulesJudgeT
    if backend == "rules":
        return RulesJudgeT() if transient else RulesJudge()
    if backend == "jev":
        return JevJudge(model=model, questions=Q_TEXT_T if transient else None)
    if backend == "mock":
        # JEV_GOVERNOR_MOCK: JSON list of probability dicts (the last entry repeats); used by the flip tests
        script = os.environ.get("JEV_GOVERNOR_MOCK")
        return MockJudge(json.loads(script) if script else None)
    raise ValueError(f"unknown backend {backend!r}: use rules, jev or mock")
