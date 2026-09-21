#!/bin/bash
# Design-set conditions of the transient study (two per family).
cd /scratch/cassola/open_jev/bench; P=scripts_t/prepare_transient.sh
$P cavT_Re1000_dt05 cavT 0.05 20 1000 &
$P cavT_Re1000_dt10 cavT 0.1 20 1000 &
$P cylT_Re100_dt05 cylT 0.5 300 1 &
$P cylT_Re100_dt10 cylT 1.0 300 1 &
$P pitzT_U10_dt1 pitzT 1e-4 0.1 10 &
$P pitzT_U10_dt2 pitzT 2e-4 0.1 10 &
$P tjT_p40_dt10 tjT 0.001 0.5 40 &
$P tjT_p40_dt20 tjT 0.002 0.5 40 &
wait
