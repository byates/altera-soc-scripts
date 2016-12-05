./prepare_sd_card_on_soc.py -d mmcblk0  --prepare_card -f           \
 --spl_loc    ./ImageFiles/raw_partition/preloader-pod.bin          \
 --boot_loc   ./ImageFiles/fat_partition/cv_pod_proc                \
 --rootfs_loc ./ImageFiles/rootfs_partition/buildroot.cmm.rootfs.sh \
 --user_loc   ./ImageFiles/user_partition/cv_pod_proc               \
