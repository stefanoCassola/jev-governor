"""Unit tests of the transient (PIMPLE) policy: no OpenFOAM and no network needed."""
import json

import pytest

from jev_governor.judges import MockJudge, make_judge
from jev_governor.sidecar import serve
from jev_governor.transient import OK, SLOW, UNSTABLE, GovernorT, RulesJudgeT, Step, features, tau_move

TARGETS = {"U": 1e-5, "p": 1e-4}


def seq(n_outer, *, erratic=False, start=1e-2, rate=-0.3):
    vals = [start * 10 ** (rate * i) for i in range(n_outer)]
    if erratic:
        vals = [v * (3.0 if i % 2 else 1.0) for i, v in enumerate(vals)]
    return vals


def step(i, n_outer, cls=OK, **kw):
    return {"step": i, "n_outer": n_outer, "class": cls, "U": seq(n_outer, **kw), "p": seq(n_outer, **kw)}


def make_state(n, steps, *, u=0.7, p=0.3, events=(), interval=10, cap=50, applied=None, mode="sync"):
    return {"iteration": n, "cap": cap, "targets": TARGETS, "events": list(events), "steps": steps,
            "config": {"interval": interval, "mode": mode, "backend": "mock", "algorithm": "PIMPLE",
                       "momentum": "U", "pressure": "p", "bounds": [0.3, 0.95], "pressure_bounds": [0.1, 0.9],
                       "floor": 3, "checkpoint": False, "description": ""},
            "controls": {"equations": {"U": u}, "fields": {"p": p}, "last_change_applied_at": applied}}


def steps(first, last, n_outer, cls=OK, **kw):
    return [step(i, n_outer, cls, **kw) for i in range(first, last + 1)]


def test_step_features_leave_out_the_unrelaxed_final_iteration():
    rec = step(1, 6)
    rec["U"][-1] = 1.0                                   # the final iteration may jump: it must not count as a rise
    s = Step(rec, TARGETS, "U", "p")
    assert s.rises == 0.0 and s.rate == pytest.approx(-0.3)
    f = features([Step(step(i, 12), TARGETS, "U", "p") for i in range(1, 21)], 20, None, 10, 50)
    assert f["n_mean"] == 12 and f["unstable"] == 0 and f["slow_capped"] == 0


def test_trial_then_accept_then_switch_to_the_other_factor():
    gov = GovernorT(RulesJudgeT())
    d1 = gov.decide(make_state(10, steps(1, 10, 20)))
    assert d1.move == "increase_large" and d1.equations["U"] > 0.7 and not d1.fields
    assert d1.guard["trial"] == {"factor": "U", "from": 0.7, "id": 10} and d1.guard["ref"] == 20
    u1 = d1.equations["U"]
    d2 = gov.decide(make_state(20, steps(11, 20, 12), u=u1, applied=10))     # it paid off: accepted, go on
    assert d2.move.startswith("increase") and gov.fails["U"] == 0


def test_an_increase_that_did_not_pay_off_is_taken_back_with_an_expiring_cap():
    gov = GovernorT(RulesJudgeT())
    d1 = gov.decide(make_state(10, steps(1, 10, 20)))
    u1 = d1.equations["U"]
    d2 = gov.decide(make_state(20, steps(11, 20, 21), u=u1, applied=10))
    assert d2.move == "take_back" and d2.equations["U"] == 0.7 and "trial" not in d2.guard
    assert gov.cap["U"] == 0.7 and gov.active == "p" and not gov.cliffs["U"]
    d3 = gov.decide(make_state(30, steps(21, 30, 20), u=0.7, applied=20))    # now the pressure factor, small probe
    assert d3.fields and d3.fields["p"] == tau_move(0.3, 1.2, 0.1, 0.9) and not d3.equations


def test_failure_1_slow_capped_steps_never_lower_the_factors():
    """cavT_Re400_dt10 of the study: the defaults hit the cap for 25 steps after the impulsive start while the
    residuals fall monotonically. Controller T lowered both factors and spiralled to the floor."""
    gov = GovernorT(RulesJudgeT())
    for n in (10, 20, 30):
        d = gov.decide(make_state(n, steps(n - 9, n, 50, SLOW, rate=-0.05)))
        assert not d.move.startswith("decrease"), d.note
        if d.equations or d.fields:
            assert d.equations.get("U", 1) >= 0.7 and d.fields.get("p", 1) >= 0.3
            break
    st = d.info["state"]["outer_loop"]
    assert "slow, not unstable" in st and "were unstable" not in st


def test_unstable_steps_do_lower():
    gov = GovernorT(RulesJudgeT())
    d = gov.decide(make_state(10, steps(1, 10, 50, UNSTABLE, erratic=True)))
    assert d.move == "decrease_large" and d.equations["U"] < 0.7


def test_failure_2_a_cliff_is_permanent_and_approached_by_bisection(tmp_path):
    """cav3T_Re400_dt30 of the study: ap 0.375 -> 0.4565 was taken back after one step and the run still died."""
    gov = GovernorT(MockJudge([{"safe_to_accelerate": 1.0}]), memory=tmp_path / "memory.json")
    gov.active = "p"
    gov.decide(make_state(10, steps(1, 10, 9), u=0.9, p=0.375))
    ev = {"step": 12, "action": "take_back", "reason": "unstable", "cliff": True, "factor": "p", "n_outer": 50,
          "from": {"U": 0.9, "p": 0.4565}, "to": {"U": 0.9, "p": 0.375}}
    d = gov.decide(make_state(20, steps(11, 20, 9), u=0.9, p=0.375, events=[ev], applied=12))
    assert d.move == "hold" and gov.cliffs["p"] == [[0.4565, 0.9]] and gov.cooldown == 1
    assert json.loads((tmp_path / "memory.json").read_text())["cliffs"]["p"] == [[0.4565, 0.9]]

    tried, p, u = [], 0.375, 0.9
    for n in range(30, 400, 10):                       # the cliff never expires
        gov.active = "p"
        d = gov.decide(make_state(n, steps(n - 9, n, 9), u=u, p=p, applied=n - 10))
        u = d.equations.get("U", u)
        if d.fields:
            p = d.fields["p"]
            tried.append(p)
    assert tried and all(v < 0.4565 for v in tried) and tried == sorted(tried)
    assert tried[0] < 0.42                             # half way in tau, not the old step over the cliff again
    assert "became unstable" in json.dumps(gov.decide(make_state(400, steps(391, 400, 9), u=u, p=p)).info["state"])

    gov.active = "p"                                   # with a gentler momentum factor the cliff does not apply
    d = gov.decide(make_state(410, steps(401, 410, 9), u=0.6, p=p, applied=400))
    assert gov._cliff("p", {"U": 0.6, "p": p}) is None


def test_cliffs_are_only_reloaded_on_a_restart(tmp_path):
    mem = tmp_path / "memory.json"
    mem.write_text(json.dumps({"cliffs": {"U": [], "p": [[0.45, 0.9]]}}))
    fresh = GovernorT(RulesJudgeT(), memory=mem)
    fresh.decide(make_state(10, steps(1, 10, 9)))
    assert fresh.cliffs["p"] == []                     # a new run from step 1 starts clean
    restart = GovernorT(RulesJudgeT(), memory=mem)
    restart.decide(make_state(90, steps(81, 90, 9)))
    assert restart.cliffs["p"] == [[0.45, 0.9]]        # a run continued from a checkpoint remembers


def test_lower_both_event_sets_expiring_caps_and_a_cooldown():
    gov = GovernorT(MockJudge([{"safe_to_accelerate": 1.0}]))
    ev = {"step": 8, "action": "lower_both", "reason": "unstable", "cliff": True, "factor": "both", "n_outer": 50,
          "from": {"U": 0.9, "p": 0.6}, "to": {"U": 0.8654, "p": 0.5172}}
    d = gov.decide(make_state(10, steps(1, 10, 15), u=0.8654, p=0.5172, events=[ev]))
    assert d.move == "hold" and gov.cap == {"U": 0.8654, "p": 0.5172}
    assert "safety guard" in json.dumps(d.info["state"]["observations"])


def test_floor_holds_without_asking_the_judge():
    judge = MockJudge([{"safe_to_accelerate": 1.0}])
    d = GovernorT(judge).decide(make_state(10, steps(1, 10, 3)))
    assert d.move == "hold" and judge.calls == 0 and "floor" in d.note


def test_async_one_change_in_flight():
    gov = GovernorT(RulesJudgeT())
    d1 = gov.decide(make_state(10, steps(1, 10, 20), mode="async"))
    d2 = gov.decide(make_state(20, steps(11, 20, 20), mode="async"))          # factors still the old ones
    assert d2.move == "hold" and "not applied yet" in d2.note and d2.guard["trial"]["id"] == 10
    d3 = gov.decide(make_state(30, steps(21, 30, 12), u=d1.equations["U"], applied=23, mode="async"))
    assert gov.last["step"] in (23, 30) and d3.move != "take_back"


def test_decision_file_carries_the_guard_data():
    text = GovernorT(RulesJudgeT()).decide(make_state(10, steps(1, 10, 20))).to_foam()
    assert "guardRef        20" in text and "trial\n{" in text and "id              10;" in text
    assert text.count("{") == text.count("}")


def test_questions_and_factory():
    assert make_judge("rules", algorithm="PIMPLE").name == "rules"
    assert isinstance(make_judge("rules", algorithm="PIMPLE"), RulesJudgeT)


def test_sidecar_serves_pimple_states_and_ignores_a_stale_done_file(tmp_path):
    import os
    import threading
    import time
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    (handoff / "done").write_text("old run\n")
    os.utime(handoff / "done", (time.time() - 3600, time.time() - 3600))
    t = threading.Thread(target=serve, args=(tmp_path,), kwargs={"backend": "rules"})
    t.start()
    (handoff / "state_10.json").write_text(json.dumps(make_state(10, steps(1, 10, 20))))
    deadline = time.time() + 10
    while not (handoff / "decision_10").exists() and time.time() < deadline:
        time.sleep(0.01)
    assert "guardRef" in (handoff / "decision_10").read_text()
    (handoff / "done").write_text("10\n")
    t.join(timeout=10)
    assert not t.is_alive()


def test_sidecar_idle_timeout(tmp_path):
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    (handoff / "state_10.json").write_text(json.dumps(make_state(10, steps(1, 10, 20))))
    assert serve(tmp_path, backend="rules", idle_timeout_s=0.3) == 1       # the solver died without "done"


def test_summary_file_for_batch_runners(tmp_path):
    handoff = tmp_path / "jevGovernor"
    handoff.mkdir()
    (handoff / "steps.csv").write_text("step,time,n_outer,class,aU,ap\n1,0.1,50,slow_capped,0.7,0.3\n"
                                       "2,0.2,12,ok,0.7,0.3\n3,0.3,50,unstable,0.8,0.3\n")
    (handoff / "state_10.json").write_text(json.dumps(make_state(10, steps(1, 10, 20))))
    serve(tmp_path, backend="rules", idle_timeout_s=0.3)
    out = json.loads((handoff / "summary.json").read_text())
    assert out == {"completed": False, "decisions": 1, "steps": 3, "outer_total": 112, "slow_capped_steps": 1,
                   "unstable_steps": 1, "final_factors": {"U": 0.8, "p": 0.3}}
