#!/bin/bash
# Checkpoint settings for crash-tolerant runs. usage: set_checkpoint.sh <rundir> <writeInterval>
C=$1/system/controlDict
sed -i "s/^startFrom .*/startFrom       latestTime;/; s/^writeInterval .*/writeInterval   $2;/; s/^purgeWrite .*/purgeWrite      2;/; s/^writeFormat .*/writeFormat     binary;/" "$C"
