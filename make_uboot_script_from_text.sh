#!/bin/bash

if [ -z "$1" ]; then
  echo "Missing path to u-boot.script file."
  exit -1
fi

mkimage  -A arm -O linux -T script -C none -a 0 -e 0 -n "u-boot for loading FPGA" -d $1 u-boot.scr
