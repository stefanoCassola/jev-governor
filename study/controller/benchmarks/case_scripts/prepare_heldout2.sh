#!/bin/bash
# Second held-out set (never used in any design step), created only after v4 was frozen.
set -e
B=/scratch/cassola/open_jev/bench/cases_async; cd $B
nu() { sed -i "s/^nu .*/nu $(python3 -c "print(1/$2)");/" $1/constant/transportProperties; }
rm -rf cavity_Re250 lbfs_Re500 pitzDaily_U15 pm_bfs_U30 pm_bfs_U55 pm_bfs_U70
cp -r cavity_Re400 cavity_Re250 && nu cavity_Re250 250
cp -r lbfs_Re400 lbfs_Re500 && nu lbfs_Re500 500
cp -r pitzDaily pitzDaily_U15
sed -i "s/uniform (10 0 0)/uniform (15 0 0)/" pitzDaily_U15/0/U
sed -i "s/uniform 0.375;/uniform 0.84375;/" pitzDaily_U15/0/k
sed -i "s/uniform 14.855;/uniform 50.1356;/" pitzDaily_U15/0/epsilon
for V in 30 55 70; do
  c=pm_bfs_U$V; cp -r pm_bfs_U44 $c
  read K W < <(python3 -c "k=1.5*($V*6.1e-4)**2; print(f'{k:.10g}', f'{k/(6.7e-7*0.009):.10g}')")
  sed -i "s/^internalField .*/internalField uniform $K;/" $c/0/k
  sed -i "s/^internalField .*/internalField uniform $W;/" $c/0/omega
  sed -i "s/value uniform (44.2 0 0);/value uniform ($V 0 0);/" $c/0/U
done
grep -h "value uniform (" pitzDaily_U15/0/U pm_bfs_U30/0/U pm_bfs_U55/0/U pm_bfs_U70/0/U | sort -u
