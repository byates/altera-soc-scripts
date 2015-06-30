PATH=../OfficalAlteraRepos/u-boot-socfpga/tools:$PATH
mkimage  -A arm -O linux -T script -C none -a 0 -e 0 -n "u-boot for loading FPGA" -d u-boot-load-fpga.script u-boot.scr
