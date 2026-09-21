#!/bin/bash
# All conditions of the transient study. D = design set (used to develop controller T), H = held-out set.
cd /scratch/cassola/open_jev/bench; P=scripts_t/prepare_transient.sh
mk() { [ -f cases_t/$1/case.json ] || bash $P "$@"; }
# D (the first six already exist from prep_design.sh)
mk cav3T_Re1000_dt15 cav3T 0.15 40 1000 &
mk cav3T_Re1000_dt30 cav3T 0.3 40 1000 &
# H
mk cavT_Re400_dt10 cavT 0.1 20 400 &
mk cavT_Re2500_dt05 cavT 0.05 20 2500 &
mk cav3T_Re400_dt30 cav3T 0.3 40 400 &
mk cav3T_Re2000_dt15 cav3T 0.15 40 2000 &
mk cylT_Re150_dt05 cylT 0.5 300 1.5 &
mk cylT_Re60_dt10 cylT 1.0 300 0.6 &
mk pitzT_U20_dt1 pitzT 1e-4 0.1 20 &
mk pitzT_U5_dt2 pitzT 2e-4 0.1 5 &
wait
