#!/bin/bash
# Held-out benchmark conditions (not used while designing controller v3), derived from existing
# async templates by changing viscosity or inlet velocity (turbulence inlet values scaled so that
# intensity and length scale are kept: k ~ U^2, epsilon ~ U^3, omega ~ U^2 per the P&M formula).
set -e
B=/scratch/cassola/open_jev/bench/cases_async; cd $B
nu() { sed -i "s/^nu .*/nu $(python3 -c "print(1/$2)");/" $1/constant/transportProperties; }
rm -rf cavity_Re700 lbfs_Re300 lbfs_Re600 pitzDaily_U20 pm_bfs_U35 pm_bfs_U65
cp -r cavity_Re400 cavity_Re700 && nu cavity_Re700 700
cp -r lbfs_Re400 lbfs_Re300 && nu lbfs_Re300 300
cp -r lbfs_Re400 lbfs_Re600 && nu lbfs_Re600 600
cp -r pitzDaily pitzDaily_U20
sed -i "s/uniform (10 0 0)/uniform (20 0 0)/" pitzDaily_U20/0/U
sed -i "s/uniform 0.375;/uniform 1.5;/" pitzDaily_U20/0/k
sed -i "s/uniform 14.855;/uniform 118.84;/" pitzDaily_U20/0/epsilon
for V in 35 65; do
  c=pm_bfs_U$V; cp -r pm_bfs_U44 $c
  read K W < <(python3 -c "k=1.5*($V*6.1e-4)**2; print(f'{k:.10g}', f'{k/(6.7e-7*0.009):.10g}')")
  sed -i "s/^internalField .*/internalField uniform $K;/" $c/0/k
  sed -i "s/^internalField .*/internalField uniform $W;/" $c/0/omega
  sed -i "s/value uniform (44.2 0 0);/value uniform ($V 0 0);/" $c/0/U
done
grep -h "inlet" -A3 pitzDaily_U20/0/U | grep value; grep -h "value uniform (" pm_bfs_U35/0/U pm_bfs_U65/0/U; grep -h "^internalField" pm_bfs_U35/0/k pm_bfs_U65/0/omega
grep -h "^nu" cavity_Re700/constant/transportProperties lbfs_Re300/constant/transportProperties lbfs_Re600/constant/transportProperties
