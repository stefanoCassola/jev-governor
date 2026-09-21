"""Incremental parser for simpleFoamMod logs (serial, SolverPerformance debug output)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

RE_TIME = re.compile(r"^Time = (\d+)$")
RE_SOLVE = re.compile(
    r"Solving for (\w+), Initial residual = ([\deE.+-]+), Final residual = ([\deE.+-]+), No Iterations (\d+)"
)
RE_CONT = re.compile(r"time step continuity errors : sum local = ([\deE.+-]+)")
RE_EXEC = re.compile(r"^ExecutionTime = ([\d.]+) s\s+ClockTime = ([\d.]+) s")
RE_PERM = re.compile(r"^\s+- Main flow direction: ([\deE.+-]+) m")
RE_SLOPE = re.compile(r"^Normalized slope of linear regression: ([\deE.+-]+|nan|-?inf)")
RE_ERR = re.compile(r"^Error predicted vs. calculated permeability: ([\deE.+-]+|nan|-?inf)")
RE_REREAD = re.compile(r"Re-reading object fvSolution")
RE_SEGMENT = re.compile(r"^=== supervisor: segment (\d+) start, restart from time (\d+) ===")


@dataclass
class Iter:
    it: int
    res: dict = field(default_factory=dict)      # field -> initial residual
    nit: dict = field(default_factory=dict)      # field -> linear solver iterations
    cont: float | None = None
    exec_s: float | None = None
    clock_s: float | None = None
    perm: float | None = None
    slope: float | None = None
    err: float | None = None


class LogReader:
    """Reads new complete lines from a growing log and assembles per-iteration records."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.buf = ""
        self.iters: list[Iter] = []
        self.cur: Iter | None = None
        self.converged = False
        self.ended = False
        self.fatal = False
        self.rereads = 0
        self.restarts: list[dict] = []   # {"segment", "restart_from", "crashed_at"}

    def poll(self) -> list[Iter]:
        """Consume new text; return iterations whose permeability block is complete."""
        if not self.path.exists():
            return []
        with open(self.path, "r", errors="replace") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        self.buf += chunk
        lines = self.buf.split("\n")
        self.buf = lines.pop()
        done: list[Iter] = []
        for ln in lines:
            if (m := RE_SEGMENT.match(ln)):
                seg, t0 = int(m.group(1)), int(m.group(2))
                if seg > 0:
                    # Crashed segment: drop the partial iteration and everything after the checkpoint.
                    crashed_at = self.cur.it if self.cur is not None else None
                    self.cur = None
                    done = [i for i in done if i.it <= t0]
                    self.iters = [i for i in self.iters if i.it <= t0]
                    self.restarts.append({"segment": seg, "restart_from": t0, "crashed_at": crashed_at})
                self.fatal = False
                continue
            m = RE_TIME.match(ln)
            if m:
                if self.cur is not None and self.cur.perm is not None:
                    done.append(self.cur)
                self.cur = Iter(int(m.group(1)))
                continue
            if "Convergence is reached" in ln:
                self.converged = True
            if ln.startswith("End"):
                self.ended = True
            if "FOAM FATAL" in ln or ln.startswith("#0  Foam::error::printStack") or "sigFpe::sigHandler" in ln:
                self.fatal = True
            if RE_REREAD.search(ln):
                self.rereads += 1
            if self.cur is None:
                continue
            if (m := RE_SOLVE.search(ln)):
                name = m.group(1)
                if name in ("Ux", "Uy", "Uz", "p"):
                    self.cur.res[name] = float(m.group(2))
                    self.cur.nit[name] = int(m.group(4))
            elif (m := RE_CONT.search(ln)):
                self.cur.cont = float(m.group(1))
            elif (m := RE_EXEC.match(ln)):
                self.cur.exec_s, self.cur.clock_s = float(m.group(1)), float(m.group(2))
            elif (m := RE_PERM.match(ln)):
                self.cur.perm = float(m.group(1))
            elif (m := RE_SLOPE.match(ln)):
                self.cur.slope = float(m.group(1))
            elif (m := RE_ERR.match(ln)):
                self.cur.err = float(m.group(1))
        # The final (converged) iteration has no following "Time =" line.
        if (self.ended or self.converged) and self.cur is not None and self.cur.perm is not None:
            done.append(self.cur)
            self.cur = None
        self.iters.extend(done)
        return done


def parse_full(path: Path) -> LogReader:
    r = LogReader(path)
    r.poll()
    return r
