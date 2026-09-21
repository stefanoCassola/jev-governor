"""Generic incremental parser for steady simpleFoam(-type) logs.

Per iteration: first initial residual and linear-solver iteration count of every solved field
(vector components are merged into the vector name by taking the maximum), continuity error,
execution/clock time, and optionally a monitored scalar matched by a user regex (e.g. the
permeability printed by simpleFoamMod).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RE_TIME = re.compile(r"^Time = (\d+)$")
RE_SOLVE = re.compile(
    r"Solving for (\w+), Initial residual = ([\deE.+-]+|nan|-?inf), Final residual = ([\deE.+-]+|nan|-?inf), "
    r"No Iterations (\d+)")
RE_CONT = re.compile(r"time step continuity errors : sum local = ([\deE.+-]+|nan|-?inf)")
RE_EXEC = re.compile(r"^ExecutionTime = ([\d.]+) s\s+ClockTime = ([\d.]+) s")
RE_CONV = re.compile(r"(SIMPLE solution converged in|Convergence is reached)")
COMPONENTS = {"Ux": "U", "Uy": "U", "Uz": "U"}


@dataclass
class Iter:
    it: int
    res: dict = field(default_factory=dict)
    nit: dict = field(default_factory=dict)
    cont: float | None = None
    exec_s: float | None = None
    clock_s: float | None = None
    monitor: float | None = None


class FoamLog:
    def __init__(self, path: Path, monitor_re: str | None = None):
        self.path = Path(path)
        self.monitor_re = re.compile(monitor_re) if monitor_re else None
        self.offset = 0
        self.buf = ""
        self.iters: list[Iter] = []
        self.cur: Iter | None = None
        self.converged = False
        self.ended = False
        self.fatal = False
        self.rereads: list[int] = []   # iteration during which fvSolution was re-read
        self.async_applied: list[int] = []   # iterations at which the async FO re-read fvSolution

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
            if (m := RE_TIME.match(ln)):
                self._close()
                self.cur = Iter(int(m.group(1)))
                continue
            if "Re-reading object fvSolution" in ln:
                self.rereads.append(self.cur.it if self.cur is not None else 0)
            if ln.startswith("jevSync: async re-read at "):
                self.async_applied.append(int(ln.split()[-1]))
            if RE_CONV.search(ln):
                self.converged = True
            if ln.startswith("End"):
                self.ended = True
            if "FOAM FATAL" in ln or "sigFpe::sigHandler" in ln or "sigSegv::sigHandler" in ln:
                self.fatal = True
            if self.cur is None:
                continue
            if (m := RE_SOLVE.search(ln)):
                name = COMPONENTS.get(m.group(1), m.group(1))
                r = float(m.group(2))
                if name in self.cur.res and m.group(1) not in COMPONENTS:
                    continue  # keep the first solve of a field in this iteration
                self.cur.res[name] = max(r, self.cur.res.get(name, 0.0))
                self.cur.nit[name] = self.cur.nit.get(name, 0) + int(m.group(4))
            elif (m := RE_CONT.search(ln)):
                if self.cur.cont is None:
                    self.cur.cont = float(m.group(1))
            elif (m := RE_EXEC.match(ln)):
                self.cur.exec_s, self.cur.clock_s = float(m.group(1)), float(m.group(2))
            elif self.monitor_re and (m := self.monitor_re.search(ln)):
                self.cur.monitor = float(m.group(1))
        if self.ended or self.converged or self.fatal:
            self._close()

    def _close(self) -> None:
        if self.cur is not None and self.cur.res:
            if not self.iters or self.iters[-1].it != self.cur.it:
                self.iters.append(self.cur)
        self.cur = None

    def complete_through(self, n: int) -> None:
        """The solver is blocked after iteration n (sync hand-off): treat n as complete."""
        self.poll()
        if self.cur is not None and self.cur.it == n:
            self._close()

    def upto(self, n: int) -> list[Iter]:
        return [i for i in self.iters if i.it <= n]
