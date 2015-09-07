if [ "$1" == "" ]; then
  echo "Missing path to u-boot.script file."
  exit -1
fi
PATH=../OfficalAlteraRepos/u-boot-socfpga/tools:$PATH
mkimage  -A arm -O linux -T script -C none -a 0 -e 0 -n "u-boot for loading FPGA" -d $1 u-boot.scr
