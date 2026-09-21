#!/bin/bash
# Transient benchmark templates (pimpleFoam with PIMPLE outer correctors and a fixed, deliberately large time
# step) in bench/cases_t. Four families:
#   cavT   lid-driven cavity, impulsive start (laminar, 2D, 16,384 cells; mesh of the steady study)
#   cav3T  lid-driven cubic cavity, impulsive start (laminar, 3D, 64,000 cells)
#   cylT   vortex shedding behind a cylinder (laminar, 2D; OpenFOAM tutorial pimpleFoam/laminar/cylinder2D)
#   pitzT  pitzDaily, unsteady RANS k-epsilon (2D; tutorial pimpleFoam/RAS/pitzDaily)
#   tjT    T-junction with a time-varying inlet total pressure, k-epsilon (3D; tutorial pimpleFoam/RAS/TJunction,
#          mesh refined by 2 in every direction)
# usage: prepare_transient.sh <name> <family> <deltaT> <endTime> [<family-specific value>]
#   family-specific value: cavT Reynolds number; cylT inlet velocity factor (1 = Re 100); pitzT inlet velocity;
#   tjT inlet total pressure at t = 1 s (tutorial: 40)
set -e
B=/scratch/cassola/open_jev/bench; SIF=/work/cassola/openfoam/openfoam2312Mod.sif
TUT=/usr/lib/openfoam/openfoam2312/tutorials/incompressible/pimpleFoam
FOAM() { apptainer exec --bind /scratch,/work "$SIF" openfoam2312 "$@"; }
NAME=$1; FAM=$2; DT=$3; END=$4; VAL=${5:-}
mkdir -p $B/cases_t; cd $B/cases_t; rm -rf $NAME
case $FAM in
  cavT)  cp -rL $B/cases_async/cavity_Re1000 $NAME; rm -rf $NAME/dynamicCode $NAME/case.json
         sed -i "s/^nu .*/nu              $(python3 -c "print(1.0/$VAL)");/" $NAME/constant/transportProperties
         sed -i 's/steadyState/Euler/' $NAME/system/fvSchemes ;;
  cav3T) cp -rL $B/cases_async/cavity_Re1000 $NAME; rm -rf $NAME/dynamicCode $NAME/case.json $NAME/constant/polyMesh
         sed -i "s/^nu .*/nu              $(python3 -c "print(1.0/$VAL)");/" $NAME/constant/transportProperties
         sed -i 's/steadyState/Euler/' $NAME/system/fvSchemes
         sed -i 's/ 0.01)/ 1)/g; s/(128 128 1)/(40 40 40)/; s/frontAndBack { type empty;/frontAndBack { type wall;/' $NAME/system/blockMeshDict
         sed -i 's/frontAndBack { type empty; }/frontAndBack { type noSlip; }/' $NAME/0/U
         sed -i 's/frontAndBack { type empty; }/frontAndBack { type zeroGradient; }/' $NAME/0/p
         (cd $NAME && FOAM blockMesh > log.blockMesh 2>&1) ;;
  cylT)  apptainer exec --bind /scratch,/work "$SIF" cp -r $TUT/laminar/cylinder2D $NAME
         (cd $NAME && cp -r 0.orig 0 && FOAM blockMesh -dict system/blockMeshDict.main > log.blockMesh 2>&1 && FOAM mirrorMesh -overwrite > log.mirrorMesh 2>&1)
         rm -rf $NAME/system/DMDs $NAME/system/coarseMesh $NAME/system/ROM* $NAME/system/snappyHexMeshDict $NAME/Allr* $NAME/Allclean
         [ -n "$VAL" ] && sed -i "s/^internalField .*/internalField   uniform ($(python3 -c "print(0.012984*$VAL)") 0 0);/" $NAME/0/U ;;
  pitzT) apptainer exec --bind /scratch,/work "$SIF" cp -r $TUT/RAS/pitzDaily $NAME
         (cd $NAME && FOAM blockMesh > log.blockMesh 2>&1)
         if [ -n "$VAL" ]; then python3 - $NAME $VAL <<'PY'
import re, sys
c, v = sys.argv[1], float(sys.argv[2]); s = v / 10.0
for f, e in (("U", 1), ("k", 2), ("epsilon", 3)):
    p = f"{c}/0/{f}"; t = open(p).read()
    if f == "U":
        t = t.replace("(10 0 0)", f"({v:g} 0 0)")
    else:   # keep turbulence intensity and length scale: k ~ U^2, epsilon ~ U^3
        t = re.sub(r"uniform ([0-9.eE+-]+);", lambda m: f"uniform {float(m.group(1)) * s ** e:.6g};", t)
    open(p, "w").write(t)
PY
         fi ;;
  tjT)   apptainer exec --bind /scratch,/work "$SIF" cp -r $TUT/RAS/TJunction $NAME
         sed -i 's/(50 5 5)/(100 10 10)/; s/(5 5 5)/(10 10 10)/; s/(5 50 5)/(10 100 10)/g' $NAME/system/blockMeshDict
         [ -n "$VAL" ] && sed -i "s/(1 40)/(1 $VAL)/" $NAME/0/p
         (cd $NAME && FOAM blockMesh > log.blockMesh 2>&1) ;;
  *) echo "unknown family $FAM"; exit 1 ;;
esac
cd $NAME
python3 - "$B/common/jevSyncFO_async" $FAM $DT $END <<'PY'
import re, sys
fo, fam, dt, end = open(sys.argv[1]).read(), sys.argv[2], sys.argv[3], sys.argv[4]
p = "system/controlDict"; s = open(p).read()
i = s.find("\nfunctions")
s = (s[:i] if i >= 0 else s.rstrip().rstrip("/*").rstrip())
s = re.sub(r"^application\s+.*$", "application     pimpleFoam;", s, flags=re.M)
for k, v in (("startFrom", "startTime"), ("startTime", "0"), ("endTime", end), ("deltaT", dt), ("writeControl", "timeStep"),
             ("writeInterval", "1000000"), ("adjustTimeStep", "no"), ("runTimeModifiable", "false"), ("purgeWrite", "0")):
    if re.search(rf"^{k}\s", s, re.M):
        s = re.sub(rf"^{k}\s+.*$", f"{k:15s} {v};", s, flags=re.M)
    else:
        s += f"\n{k:15s} {v};\n"
s = re.sub(r"^maxCo\s+.*$\n", "", s, flags=re.M)
open(p, "w").write(s + "\nfunctions\n{\n" + fo + "}\n")

p = "system/fvSolution"; s = open(p).read()
turb = fam in ("pitzT", "tjT")
s = s[:s.index("solvers")]
s += """solvers
{
    p       { solver GAMG; smoother GaussSeidel; tolerance 1e-7; relTol 0.01; }
    pFinal  { $p; relTol 0.01; }
    "(U|k|epsilon)"      { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol 0.1; }
    "(U|k|epsilon)Final" { $U; relTol 0.1; }
}

PIMPLE
{
    nOuterCorrectors 50;
    nCorrectors     1;
    nNonOrthogonalCorrectors 0;
    pRefCell        0;
    pRefValue       0;
    residualControl
    {
        p { tolerance 1e-4; relTol 0; }
        U { tolerance 1e-5; relTol 0; }
    }
}

relaxationFactors
{
    fields { p 0.3; pFinal 1; }
    equations { U 0.7; UFinal 1; }
}
"""
open(p, "w").write(s)
PY
python3 - $FAM $DT $END "$VAL" <<'PY'
import json, sys
fam, dt, end, val = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
desc = {
    "cavT": f"two-dimensional lid-driven square cavity at Reynolds number {val}, impulsively started from rest, uniform 128x128 mesh, laminar",
    "cav3T": f"three-dimensional lid-driven cubic cavity at Reynolds number {val}, impulsively started from rest, uniform 40x40x40 mesh, laminar",
    "cylT": "two-dimensional laminar flow past a circular cylinder at a Reynolds number of about %s, started from a uniform field; vortex shedding develops during the run" % (round(100 * float(val or 1))),
    "pitzT": f"two-dimensional turbulent flow over a backward-facing step with a contraction (pitzDaily), unsteady RANS with the k-epsilon model, inlet velocity {val or 10} m/s, started from rest",
    "tjT": "three-dimensional turbulent flow through a T-junction driven by an inlet total pressure that rises linearly in time (from 10 to %s Pa within one second), unsteady RANS with the k-epsilon model, started from rest" % (val or 40),
}[fam]
turb = fam in ("pitzT", "tjT")
ctx = (f"Transient incompressible flow: {desc}. Solved with OpenFOAM pimpleFoam (PIMPLE algorithm) with a fixed, deliberately "
       "large time step (Courant number well above one), so every time step needs several under-relaxed outer "
       "pressure-velocity iterations; the outer loop of a step ends when the initial residuals of all equations are "
       "below their tolerances, or at a cap of 50 outer iterations. Goal: finish the run with as few outer iterations "
       "per time step as possible while every step still meets its tolerances. The momentum (U) under-relaxation factor "
       "of the outer iterations acts like a pseudo time step within the time step: larger values (less under-relaxation) "
       "give faster convergence of the outer loop up to a stability limit, beyond which the outer loop converges more "
       "slowly, erratically or not at all. The pressure factor is set automatically from the momentum factor; only the "
       "momentum factor is decided.")
prof = {"solver": "pimpleFoam", "family": fam, "description": desc, "context": ctx, "U": 0.7, "p": 0.3,
        "turb": None, "turb_fields": [], "turb_follow": False,   # turbulence is solved on the final outer iteration only
        "targets": {"p": 1e-4, "U": 1e-5}, "cap": 50,
        "p_rule": "one_minus", "floor": 3.0, "deltaT": float(dt), "endTime": float(end)}
json.dump(prof, open("case.json", "w"), indent=1)
PY
# compile the function object once, in the template
e=$(sed -n 's/^endTime *\([0-9.eE+-]*\);/\1/p' system/controlDict)
sed -i "s/^endTime .*/endTime         $DT;/" system/controlDict
mkdir -p sync; echo 0 > sync/interval
FOAM pimpleFoam > log.compile 2>&1 || { tail -30 log.compile; exit 1; }
grep -q "jevSync: interval" log.compile || { echo "FO inactive $NAME"; tail -5 log.compile; exit 1; }
sed -i "s/^endTime .*/endTime         $e;/" system/controlDict
grep "Courant Number" log.compile | tail -1
rm -rf sync log.compile 0/uniform; find . -maxdepth 1 -type d -regex './[0-9.e+-]+' ! -name 0 -exec rm -rf {} +
echo "$NAME ok: $(grep -m1 nCells log.blockMesh 2>/dev/null || true) cells $(head -c 0 /dev/null)"
