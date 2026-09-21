"""Incremental parser for pimpleFoam logs with PIMPLE outer correctors.

Per time step: number of outer iterations, whether the outer loop met its residual control, the initial
residual of every solved field at each outer iteration (vector components merged by maximum, first solve
of a field per outer iteration), the maximum Courant number, execution and clock time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RE_TIME = re.compile(r"^Time = ([\deE.+-]+)$")
RE_SOLVE = re.compile(r"Solving for (\w+), Initial residual = ([\deE.+-]+|nan|-?inf), Final residual = "
                      r"([\deE.+-]+|nan|-?inf), No Iterations (\d+)")
RE_EXEC = re.compile(r"^ExecutionTime = ([\d.]+) s\s+ClockTime = ([\d.]+) s")
RE_CO = re.compile(r"^Courant Number mean: ([\deE.+-]+) max: ([\deE.+-]+)")
COMPONENTS = {"Ux": "U", "Uy": "U", "Uz": "U"}


@dataclass
class Step:
    idx: int                      # time index (1, 2, ...), equals Time::timeIndex() for a run started at 0
    t: float
    outer: list = field(default_factory=list)   # one {field: initial residual} per outer iteration
    lin: int = 0                  # linear-solver iterations of the pressure equation, summed over the step
    converged: bool = False
    co_max: float | None = None
    exec_s: float | None = None
    clock_s: float | None = None

    @property
    def n_outer(self) -> int:
        return len(self.outer)


class PimpleLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.buf = ""
        self.steps: list[Step] = []
        self.cur: Step | None = None
        self._co = None
        self.ended = False
        self.fatal = False
        self.async_applied: list[int] = []

    def poll(self) -> None:
        if not self.path.exists():
            return
        with open(self.path, "r", errors="replace") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        self.buf += chunk
        lines = self.buf.split("\n")
        self.buf = lines.pop()
        for ln in lines:
            if (m := RE_CO.match(ln)):
                self._co = float(m.group(2))
                continue
            if (m := RE_TIME.match(ln)):
                self._close()
                self.cur = Step(len(self.steps) + 1, float(m.group(1)), co_max=self._co)
                continue
            if ln.startswith("jevSync: async re-read at "):
                self.async_applied.append(int(ln.split()[-1]))
            if ln.startswith("End"):
                self.ended = True
            if "FOAM FATAL" in ln or "sigFpe::sigHandler" in ln or "sigSegv::sigHandler" in ln:
                self.fatal = True
            if self.cur is None:
                continue
            if ln.startswith("PIMPLE: iteration "):
                self.cur.outer.append({})
            elif ln.startswith("PIMPLE: converged in"):
                self.cur.converged = True
            elif (m := RE_SOLVE.search(ln)) and self.cur.outer:
                name = COMPONENTS.get(m.group(1), m.group(1))
                r = float(m.group(2))
                o = self.cur.outer[-1]
                if name == "p":
                    self.cur.lin += int(m.group(4))
                if name in o and m.group(1) not in COMPONENTS:
                    continue
                o[name] = max(r, o.get(name, 0.0))
            elif (m := RE_EXEC.match(ln)):
                self.cur.exec_s, self.cur.clock_s = float(m.group(1)), float(m.group(2))
        if self.ended or self.fatal:
            self._close()

    def _close(self) -> None:
        if self.cur is not None and self.cur.outer:
            self.steps.append(self.cur)
        self.cur = None

    def complete_through(self, n: int) -> None:
        """The solver is blocked after time step n (sync hand-off): treat step n as complete."""
        self.poll()
        if self.cur is not None and self.cur.idx == n:
            self._close()

    def upto(self, n: int) -> list[Step]:
        return [s for s in self.steps if s.idx <= n]
