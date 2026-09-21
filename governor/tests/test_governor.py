"""Unit tests of the sidecar: no OpenFOAM and no network needed."""
import json
import math
import threading
import time

import pytest

from jev_governor import features as ft
from jev_governor.judges import JevJudge, MockJudge, RulesJudge, make_judge
from jev_governor.policy import Governor, _step
from jev_governor.sidecar import serve

FIELDS = ["U", "p"]


def make_state(n, rate, *, u=0.7, p=0.3, consistent=False, upwind=False, interval=25, level=1e-1,
               bounds=(0.3, 0.95), followers=(), nan=False):
    """Residuals level * 10**(rate * it / 100): rate < 0 converges, rate > 0 diverges."""
    samples = [[it, [level * 10 ** (rate * it / 100)] * 2] for it in range(n - interval + 1, n + 1)]
    if nan:
        samples[-1][1] = [None, None]
    return {"iteration": n, "time": float(n), "fields": FIELDS, "targets": {"U": 1e-5, "p": 1e-5},
            "config": {"interval": interval, "mode": "sync", "backend": "mock", "momentum": "U", "pressure": "p",
                       "followers": list(followers), "bounds": list(bounds), "upwind_fallback": True,
                       "description": ""},
            "controls": {"equations": {"U": u, **{f: u for f in followers}}, "fields": {"p": p},
                         "consistent": consistent, "upwind": upwind},
            "samples": samples}


def test_slope_and_words():
    vals = [10 ** (-0.01 * i) for i in range(50)]          # one decade per 100 iterations
    assert ft.slope(vals) * 100 == pytest.approx(-1.0)
    assert ft.trend_word(-1.5) == "falling fast"
    assert ft.trend_word(0.0) == "stagnating (not falling)"
    assert ft.trend_word(2.0) == "rising fast"
    zigzag = [1e-3 * (2 if i % 2 else 1) for i in range(30)]
    assert ft.osc_word(ft.oscillation(zigzag)) == "strong oscillation"
    assert ft.osc_word(ft.oscillation(vals)) == "smooth"


def test_step_is_bounded_and_multiplicative_in_tau():
    assert _step(0.5, 2.0, 0.3, 0.95) == pytest.approx(2 / 3, abs=1e-4)
    assert _step(0.94, 2.0, 0.3, 0.95) == 0.95
    assert _step(0.31, 0.5, 0.3, 0.95) == 0.3
    assert _step(1.0, 0.5, 0.3, 0.95) <= 0.95               # an unrelaxed equation does not divide by zero


def test_rules_accelerate_a_slow_stable_run_and_apply_the_simple_pressure_rule():
    gov = Governor(RulesJudge())
    d = gov.decide(make_state(25, rate=-0.1, followers=("k",)))
    assert d.move == "increase_large"
    assert d.equations["U"] > 0.7 and d.equations["k"] == d.equations["U"]
    assert d.fields["p"] == pytest.approx(1 - d.equations["U"], abs=1e-4)


def test_consistent_simplec_leaves_pressure_alone():
    d = Governor(RulesJudge()).decide(make_state(25, rate=-0.1, consistent=True, p=None))
    assert d.equations and not d.fields


def test_divergence_decreases_then_cools_down():
    gov = Governor(RulesJudge())
    d1 = gov.decide(make_state(25, rate=+6.0, level=1e-4))    # more than a decade inside the window
    assert d1.move == "decrease_large" and d1.equations["U"] < 0.7
    d2 = gov.decide(make_state(50, rate=-1.0, u=d1.equations["U"], level=1e-4))
    assert d2.move == "hold" and not d2.changed               # cooldown although the run looks fine again


def test_guard_overrides_the_judge():
    calm = MockJudge([{"safe_to_accelerate": 1.0}])
    gov = Governor(calm)
    gov.decide(make_state(25, rate=0.0, level=1e-3))
    d = gov.decide(make_state(50, rate=0.0, level=1.0))        # residual jumped a thousandfold
    assert d.move == "decrease_large" and "guard" in d.info and calm.calls == 1

    d = Governor(calm).decide(make_state(25, rate=0.0, nan=True))
    assert d.move == "decrease_large" and "non-finite" in d.info["guard"]


def test_upwind_fallback_only_at_the_floor_then_restored_then_sticky():
    gov = Governor(MockJudge([{"diverging": 1.0}, {"diverging": 1.0}] + [{}] * 4 + [{"diverging": 1.0}]))
    d = gov.decide(make_state(25, rate=0.0, u=0.35))
    assert not d.upwind and d.equations["U"] == 0.3            # relaxation comes first
    d = gov.decide(make_state(50, rate=0.0, u=0.3))
    assert d.upwind and "ON" in d.note                         # at the floor: fallback
    for n in (75, 100, 125):
        assert gov.decide(make_state(n, rate=0.0, u=0.3, upwind=True)).upwind
    d = gov.decide(make_state(150, rate=0.0, u=0.3, upwind=True))
    assert not d.upwind and "OFF" in d.note                    # four calm decisions: schemes restored
    d = gov.decide(make_state(175, rate=0.0, u=0.3, upwind=False))
    assert d.upwind and gov.upwind_sticky                      # divergence came straight back: keep upwind
    for n in range(200, 400, 25):
        assert gov.decide(make_state(n, rate=0.0, u=0.3, upwind=True)).upwind


def test_stall_backs_off_and_sets_an_expiring_ceiling():
    gov = Governor(MockJudge([{"stuck_high": 1.0, "safe_to_accelerate": 1.0}]), ceiling_ttl=100)
    d = None
    for n in range(25, 200, 25):
        d = gov.decide(make_state(n, rate=0.0, u=0.95))
        if d.move == "decrease_small":
            break
    assert d.move == "decrease_small" and gov.ceiling == d.equations["U"]
    set_at = d.iteration
    later = gov.decide(make_state(set_at + 100, rate=0.0, u=d.equations["U"]))
    assert gov.ceiling is None and later.iteration == set_at + 100


def test_converged_run_is_left_alone():
    judge = MockJudge([{"safe_to_accelerate": 1.0}])
    d = Governor(judge).decide(make_state(25, rate=0.0, level=1e-7))
    assert d.move == "hold" and judge.calls == 0


def test_decision_file_is_a_valid_openfoam_dictionary():
    text = Governor(RulesJudge()).decide(make_state(25, rate=-0.1)).to_foam()
    assert text.count("{") == text.count("}")
    assert "relaxationFactors" in text and "upwind          false;" in text
    for line in text.splitlines():
        line = line.strip()
        assert not line or line in "{}" or line.endswith(";") or line in ("relaxationFactors", "equations", "fields")


def test_verbal_state_contains_no_raw_residuals():
    gov = Governor(MockJudge())
    d = gov.decide(make_state(25, rate=-0.5))
    text = json.dumps(d.info["state"]["residuals"]) + json.dumps(d.info["state"]["observations"])
    assert "e-0" not in text and "falling" in text            # words, not numbers, carry the trend


class FakeClient:
    def __init__(self, failures=0):
        self.failures, self.calls = failures, 0

    def system_one(self, state, questions):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("402 payment required")
        assert set(questions) == {"diverging", "safe_to_accelerate", "stagnating", "stuck_high"}
        noul = lambda v: type("N", (), {"noul": v})()   # noqa: E731
        return type("R", (), {"answers": {k: noul(0.9 if k == "diverging" else 0.1) for k in questions},
                              "model": "jev-test", "usage": None})()


def test_jev_judge_never_skips_a_decision():
    pytest.importorskip("typesafe_sdk")
    slept = []
    judge = JevJudge(client=FakeClient(failures=3), sleep=slept.append)
    d = Governor(judge).decide(make_state(25, rate=-0.1))
    assert d.move == "decrease_large"
    assert d.info["api_retries"] == 3 and slept == [2.0, 4.0, 6.0]


def test_unknown_backend():
    with pytest.raises(ValueError):
        make_judge("llm")


def test_sidecar_answers_state_files_atomically(tmp_path):
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    t = threading.Thread(target=serve, args=(tmp_path,), kwargs={"backend": "rules"})
    t.start()
    for n in (25, 50):
        (handoff / f"state_{n}.json").write_text(json.dumps(make_state(n, rate=-0.1)))
        deadline = time.time() + 10
        while not (handoff / f"decision_{n}").exists() and time.time() < deadline:
            time.sleep(0.01)
        assert f"iteration       {n};" in (handoff / f"decision_{n}").read_text()
    (handoff / "done").write_text("50\n")
    t.join(timeout=10)
    assert not t.is_alive()
    log = [json.loads(line) for line in (handoff / "decisions.jsonl").read_text().splitlines()]
    assert [r["iteration"] for r in log] == [25, 50] and all(math.isfinite(r["decision_wall_s"]) for r in log)
    assert not list(handoff.glob(".decision_*"))


def test_plateau_settles_on_the_best_factor_and_freezes_but_the_guard_stays_armed():
    judge = MockJudge([{"safe_to_accelerate": 1.0}])
    gov = Governor(judge, settle_after=100)
    u, d = 0.5, None
    for n in range(25, 300, 25):                               # flat residuals: nothing the governor does helps
        d = gov.decide(make_state(n, rate=0.0, u=u, level=1e-3))
        u = d.equations.get("U", u)
        if gov.frozen:
            break
    assert gov.frozen and "settle" in d.note and u == 0.5      # back to the factor of the best level
    calls = judge.calls
    d = gov.decide(make_state(d.iteration + 25, rate=0.0, u=u, level=1e-3))
    assert d.move == "hold" and not d.changed and "frozen" in d.note
    d = gov.decide(make_state(d.iteration + 25, rate=0.0, u=u, level=1.0))
    assert d.move == "decrease_large" and "guard" in d.note    # frozen, not blind
    assert judge.calls >= calls


def test_small_wiggles_on_a_plateau_are_not_an_oscillation():
    noise = [1e-3 * (1.05 if i % 2 else 1.0) for i in range(40)]      # 0.02 decades
    swing = [1e-3 * (2.0 if i % 2 else 1.0) for i in range(40)]       # 0.3 decades
    assert ft.osc_word(ft.oscillation(noise)) == "smooth"
    assert ft.osc_word(ft.oscillation(swing)) == "strong oscillation"


def test_unavailable_backend_holds_loudly_instead_of_hanging_the_solver(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_GOVERNOR_BACKEND", "no-such-backend")
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    (handoff / "state_25.json").write_text(json.dumps(make_state(25, rate=-0.1)))
    (handoff / "done").write_text("25\n")
    assert serve(tmp_path) == 1
    text = (handoff / "decision_25").read_text()
    assert "NOT governed" in text and "relaxationFactors" not in text


def test_async_only_one_change_in_flight_and_no_move_from_a_stale_factor():
    judge = MockJudge([{"safe_to_accelerate": 1.0}])
    gov = Governor(judge)
    d1 = gov.decide(make_state(25, rate=-0.5, u=0.7))
    assert d1.equations["U"] > 0.7
    d2 = gov.decide(make_state(50, rate=-0.5, u=0.7))          # written before d1 reached the solver
    assert d2.move == "hold" and not d2.changed and "not applied yet" in d2.note and judge.calls == 1
    d3 = gov.decide(make_state(75, rate=-0.5, u=d1.equations["U"]))
    assert d3.equations["U"] > d1.equations["U"]               # acknowledged: the next move builds on it


def test_a_lost_change_does_not_block_the_governor_for_ever():
    gov = Governor(MockJudge([{"safe_to_accelerate": 1.0}]))
    gov.decide(make_state(25, rate=-0.5, u=0.7))
    notes = [gov.decide(make_state(n, rate=-0.5, u=0.7)).note for n in range(50, 300, 25)]
    assert any("increase_small" in x for x in notes[8:])


def test_async_sidecar_decides_on_the_newest_state_but_keeps_all_samples(tmp_path):
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    for n in (25, 50, 75):
        st = make_state(n, rate=-0.5)
        st["config"]["mode"] = "async"
        (handoff / f"state_{n}.json").write_text(json.dumps(st))
    (handoff / "done").write_text("75\\n")
    gov = Governor(RulesJudge())
    assert serve(tmp_path, governor=gov) == 1
    assert (handoff / "decision_75").exists() and not (handoff / "decision_25").exists()
    assert len(gov.samples) == 75


def test_settle_window_counts_from_the_iteration_the_change_was_applied():
    gov = Governor(MockJudge([{"safe_to_accelerate": 1.0}, {}]))   # one increase, then holds
    d1 = gov.decide(make_state(25, rate=-0.5, u=0.7))
    st = make_state(75, rate=-0.5, u=d1.equations["U"])
    st["controls"]["last_change_applied_at"] = 61              # async: the change landed 36 iterations late
    d = gov.decide(st)
    assert d.move == "hold" and gov.last_change["iteration"] == 61
    # window = (applied + SETTLE, n]: nothing from before or right after the change is used
    feats = ft.compute(gov.samples, {"U": 1e-5, "p": 1e-5}, 75, 61, 25)
    assert feats.window[0] == 61 + ft.SETTLE + 1

    legacy = Governor(MockJudge([{"safe_to_accelerate": 1.0}, {}]))
    d1 = legacy.decide(make_state(25, rate=-0.5, u=0.7))
    legacy.decide(make_state(75, rate=-0.5, u=d1.equations["U"]))   # a state without the new entry
    assert legacy.last_change["iteration"] == 50               # fallback: one interval before the state


# ------------------------------------------------------------------ missing or invalid API key

class ApiError(Exception):
    def __init__(self, status):
        super().__init__(f"{status} error")
        self.status = status


class RejectingClient:
    """Stands in for the SDK client: every request fails with the given HTTP status (no network)."""

    def __init__(self, status, failures=10 ** 9):
        self.status, self.failures, self.calls = status, failures, 0

    def system_one(self, state, questions):
        self.calls += 1
        if self.calls <= self.failures:
            raise ApiError(self.status)
        noul = lambda v: type("N", (), {"noul": v})()   # noqa: E731
        return type("R", (), {"answers": {k: noul(0.1) for k in questions}, "model": "jev-test", "usage": None})()


def _serve_one_state(tmp_path, **kw):
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    (handoff / "state_25.json").write_text(json.dumps(make_state(25, rate=-0.1)))
    (handoff / "done").write_text("25\n")
    t0 = time.time()
    n = serve(tmp_path, **kw)
    return n, time.time() - t0, (handoff / "decision_25").read_text()


def test_missing_api_key_does_not_hang_the_solver_and_says_what_to_do(tmp_path, monkeypatch, capsys):
    pytest.importorskip("typesafe_sdk")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    n, wall, text = _serve_one_state(tmp_path, backend="jev")
    assert n == 1 and wall < 5                                  # answered at once: the solver is not kept waiting
    assert "NOT governed" in text and "TYPESAFE_API_KEY is not set" in text
    assert "console.typesafe.ai/keys" in text and "backend rules" in text
    assert "relaxationFactors" not in text                      # the current factors are held
    assert text.count('"') == 2 and "\n" not in text.split('note')[1].strip().rstrip(";\n")[1:-1]
    assert "NOT governed" in capsys.readouterr().out            # and in sidecar.log


def test_blank_api_key_counts_as_missing(tmp_path, monkeypatch):
    pytest.importorskip("typesafe_sdk")
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    _, _, text = _serve_one_state(tmp_path, backend="jev")
    assert "TYPESAFE_API_KEY is not set" in text


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_api_key_is_not_retried_and_the_run_continues_ungoverned(tmp_path, status):
    pytest.importorskip("typesafe_sdk")
    slept = []
    client = RejectingClient(status)
    gov = Governor(JevJudge(client=client, sleep=slept.append))
    n, wall, text = _serve_one_state(tmp_path, governor=gov)
    assert n == 1 and client.calls == 1 and slept == []         # one request, no back-off loop
    assert "NOT governed" in text and f"HTTP {status}" in text and "TYPESAFE_API_KEY was rejected" in text
    assert "relaxationFactors" not in text


def test_rejected_key_keeps_holding_for_every_later_state(tmp_path):
    pytest.importorskip("typesafe_sdk")
    client = RejectingClient(401)
    gov = Governor(JevJudge(client=client, sleep=lambda s: None))
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    for n in (25, 50, 75):
        (handoff / f"state_{n}.json").write_text(json.dumps(make_state(n, rate=-0.1)))
    (handoff / "done").write_text("75\n")
    assert serve(tmp_path, governor=gov) == 3 and client.calls == 1   # asked once, then never again
    assert all("NOT governed" in (handoff / f"decision_{n}").read_text() for n in (25, 50, 75))


def test_exhausted_credits_and_outages_are_still_retried(tmp_path):
    pytest.importorskip("typesafe_sdk")
    slept = []
    client = RejectingClient(402, failures=2)                   # payment required twice, then fine
    d = Governor(JevJudge(client=client, sleep=slept.append)).decide(make_state(25, rate=-0.1))
    assert client.calls == 3 and slept == [2.0, 4.0] and d.info["api_retries"] == 2
