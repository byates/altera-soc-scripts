#!/usr/bin/python

#-------------------------------------------------------------------------------
# The MIT License (MIT)
#
# Copyright (c) 2015 Brent Yates
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#-------------------------------------------------------------------------------
# Script to create Boot SD card for the Altera SOC FPGAs
#
# Requires the following Python modules:
#   shell_helper (https://github.com/byates/shell_helper)
#   reparted
#   colorama
#-------------------------------------------------------------------------------

from __future__ import print_function

import sys
import os
import argparse
import glob
import shutil
import pprint
from colorama import Fore, Style
import reparted
from shell_helper import ShellHelper
from time import sleep

# A neat trick is to make our heads and sectors per track line up (to 128KiB) by using
# 224 heads and 56 sectors per track. An SDCARD is not a hard drive and it does not have
# sectors, tracks, OR heads - but it pretends to be a hard drive, and so, it has
# a drive geometry just like any hard drive would. This geometry business of
# heads/sectors/tracks and so on has no physical relationship to actual physical
# properties on our SDCARD, it just helps to make a convenient way to talk about
# how data is organised on the drive. Note that there are other combinations that
# also make 128K alignment easy, but this is the one mentioned in several SSD and
# flash articles around the web.
HEADS = 224
SECTORS = 56
SECTOR_SIZE = 512  # bytes
CYLINDER_SIZE_BYTES = HEADS * SECTORS * SECTOR_SIZE
CYLINDER_SIZE_BLKS = CYLINDER_SIZE_BYTES / 1024

# Changing these will require changing the preloader and u-boot images.
FAT_PARTITION = 1
ROOTFS_PARTITION = 2
RAW_PARTITION = 3

# We look for Preloader, FAT, and ROOTFS files in a set of directories under a common
# directory (args.images_loc). Each partition type has a directory.
# The path to each partition's files is then:
#     os.path.join(args.images_loc,IMAGE_FILES_RAW_LOC)
#     os.path.join(args.images_loc,IMAGE_FILES_FAT_LOC)
#     os.path.join(args.images_loc,IMAGE_FILES_ROOTFS_LOC)
IMAGE_FILES_RAW_LOC = "raw_partition"
IMAGE_FILES_FAT_LOC = "fat_partition"
IMAGE_FILES_ROOTFS_LOC = "rootfs_partition"

# SYNC is used to flush file system buffers so that that SDCard gets the data
# we have written. It is avaialbe natively in python3 but not in 2.
if hasattr(os, 'sync'):
    sync = os.sync
else:
    import ctypes
    libc = ctypes.CDLL("libc.so.6")
    def sync():
        libc.sync()

class SystemDevicesInterface(object):

    LastCommandResult = 0

    def __init__(self, logFile, echo_cmds = False):
        self.EchoCmds = echo_cmds
        # Uses reparted get a list of all the drives in the system.
        self.devices = reparted.device.probe_standard_devices()
        self._shell_helper = ShellHelper(logFile)
        # This is the largest SDCARD size we expect to see and is used to validate the target
        # device (as a safety measure).
        self.max_size_to_be_an_sdcard = reparted.Size(32, "GiB")

    def find_device(self, deviceName):
        for device in self.devices:
            if (deviceName == device.path) or (('/dev/' + deviceName) == device.path):
                return(device)

    def validate_device(self, targetDevice):
        if not targetDevice:
            return(False)
        # Check to see if the size of the target drive is larger than what we expect
        # for an SDCard.
        if targetDevice.size > self.max_size_to_be_an_sdcard:
            print("ERROR: Target drive is larger than we expected.")
            print("       Are you sure this is the drive to want?")
            print("       Target drive size is {0}".format(targetDevice.size.pretty(units = "GiB")))
            return(False)
        # Found target in drive list and its size is ok
        return(True)

    def list_devices(self):
        for device in self.devices:
            Text = device.path + " [{0}]".format(device.size.pretty(units = "GiB"))
            if device.size <= self.max_size_to_be_an_sdcard:
                print(Fore.GREEN + Text + Fore.RESET)
            else:
                print(Style.DIM + Text + "  Not an SDCard" + Style.RESET_ALL)

    def run_cmd(self, cmd, workingDir = "", inputStr = None, suppress_errors={}):
        """
        Runs the speicifed command and reports any errors.
        Returns True if command had no errors otherwise False.
        LastCommandResult holds the command result code.
        """
        r = self._shell_helper.RunCmdCaptureOutput(cmd, workingDir, inputStr, echo_cmd = self.EchoCmds)
        if self.EchoCmds:
            if r[1]:
                for line in r[1]:
                    print("  " + line)
        self.LastCommandResult = r[0]
        # Check for errors (non-zero return code)
        if self.LastCommandResult != 0 and (not self.LastCommandResult in suppress_errors):
            if r[2]:
                for line in r[2]:
                    print(Fore.RED + "  " + line + Fore.RESET)
            cmdName = cmd.split()[0]
            print(Fore.RED + "ERROR: "+cmdName+" failed with code {}.".format(r[0]) + Fore.RESET)
            return(False)
        return(True)

    def unmount_device(self, targetDevice):
        """
        Unmounts any mounted partitions on the target device
        """
        # The file '/proc/mounts' contains a list of mounts.  Parse this file and grab any that
        # are part of the target drive.
        Mounts = []
        for line in file('/proc/mounts'):
            if line[0] == '/':
                MountPath = line.split()[0]
                if MountPath.startswith(targetDevice.path):
                    Mounts.append(MountPath)

        for mount in Mounts:
            print("Unmounting '" + mount + "'")
            cmd = 'umount ' + mount
            if not self.run_cmd(cmd):
                return(False)
        return(True)

    def is_mounted(self, targetDevice, node_index):
        """
        Returns mount point if the node_index for the target device is mounted otherwise
        returns None.

        node_index is a number (1,2,...)
        """
        # The file '/proc/mounts' contains a list of mounts.  Parse this file and look for the
        # target node.
        NodePath = targetDevice.path + str(node_index)
        for line in file('/proc/mounts'):
            LineParts = line.split()
            if LineParts[0] == NodePath:
                return(LineParts[1])
        return(None)

    def zero_first_1mb(self, targetDevice):
        """
        Writes 0 to first 1MB+1024 of the target device.
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        cmd = "dd if=/dev/zero of=" + targetDevice.path + " bs=1024 count=1025"
        if not self.run_cmd(cmd):
            return(False)
        return(True)

    def write_spl(self, targetDevice, file_path, nodePath):
        """
        Writes 2nd stage bootloader to RAW partition on the target device.
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        cmd = "dd if='"+file_path+"' of=" + nodePath + " bs=512"
        if not self.run_cmd(cmd):
            return(False)
        return(True)

    def create_partitions(self, targetDevice, partitions):
        """
        Create new partition table based on the partition parameters given.
        [ start, end, type, bootable ]
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        SFDiskArgs = ""
        for part in partitions:
            for item in part:
                SFDiskArgs = SFDiskArgs + item + ','
            SFDiskArgs = SFDiskArgs + "\n"
        Cylinders = int(targetDevice.size.to("B") / CYLINDER_SIZE_BYTES)
        Cmd = "sfdisk -f -D -H " + str(HEADS) + " -S "+ str(SECTORS)+" -C " + str(Cylinders) + " -uB " + targetDevice.path
        if not self.run_cmd(Cmd, inputStr = SFDiskArgs):
            return(False)
        return(True)

    def format_fat_partition(self, targetDevice):
        """
        Formats the FAT partition as an FAT32 file system
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)

        NodePath = targetDevice.path + str(FAT_PARTITION)
        print("Formatting " + NodePath + ' as BOOT:FAT32')
        # First zero out the first sector per http://linux.die.net/man/8/fdisk
        Cmd = 'dd if=/dev/zero of='+NodePath+' bs=512 count=1'
        if not self.run_cmd(Cmd):
            return(False)
        # The format
        Cmd = 'mkfs.vfat -F 32 -n "BOOT" ' + NodePath
        if not self.run_cmd(Cmd):
            return(False)
        return(True)

    def format_rootfs_partition(self, targetDevice):
        """
        Formats the rootfs partition as an EXT4 file system
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        NodePath = targetDevice.path + str(ROOTFS_PARTITION)
        print("Formatting " + NodePath + ' as ROOTFS:EXT4')
        # These values are suppose to work well for SDCARDS.
        # See http://docs.pikatech.com/display/DEV/Optimizing+File+System+Parameters+of+SD+card+for+use+on+WARP+V3
        # See https://developer.ridgerun.com/wiki/index.php/High_performance_SD_card_tuning_using_the_EXT4_file_system
        print("Enter YES for journal support on EXT4 partition or NO (default) for data_writeback:")
        UserInput = raw_input("Type " + Fore.RED + "yes" + Fore.RESET + " or anything else for default: ")
        if UserInput == "yes":
            Cmd = 'mkfs.ext4 -E stride=2,stripe-width=256 -b 4096 -L "ROOTFS" '+ NodePath
            if not self.run_cmd(Cmd):
                return(False)
        else:
            Cmd = 'mkfs.ext4 -O ^has_journal -E stride=2,stripe-width=256 -b 4096 -L "ROOTFS" '+ NodePath
            if not self.run_cmd(Cmd):
                return(False)
            Cmd = 'tune2fs -o journal_data_writeback '+ NodePath
            if not self.run_cmd(Cmd):
                return(False)
            Cmd = 'tune2fs -O ^has_journal '+ NodePath
            if not self.run_cmd(Cmd):
                return(False)
        Cmd = 'tune2fs -O ^huge_file '+ NodePath
        if not self.run_cmd(Cmd):
            return(False)
        Cmd = 'e2fsck -fpv '+ NodePath
        # Return value 1 just means the command 'fixed' any issues.
        if not self.run_cmd(Cmd, suppress_errors={1}):
            return(False)
        return(True)

    def mount_fat_partition(self, targetDevice):
        """
        Mounts the rootfs partition using udisks
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        NodePath = targetDevice.path + str(FAT_PARTITION)
        print("Mounting " + NodePath)
        user = os.getenv("SUDO_USER")
        Cmd = 'sudo -u '+user+' udisks --mount ' + NodePath
        if not self.run_cmd(Cmd):
            return(False)
        return(True)

    def mount_rootfs_partition(self, targetDevice):
        """
        Mounts the rootfs partition using udisks
        """
        if not self.validate_device(targetDevice):
            print(Fore.RED + "ERROR: " + targetDevice.path + " is not a valid SDCard device. Aborting." + Fore.RESET)
            return(False)
        NodePath = targetDevice.path + str(ROOTFS_PARTITION)
        print("Mounting " + NodePath)
        Cmd = 'udisks --mount ' + NodePath
        if not self.run_cmd(Cmd):
            return(False)
        return(True)

    def copy_rootfs_to_gz_archive(self, nodePath, dst_file_path):
        """
        Copies all the rootfs files to a tar.gz archive.
        """
        cmd = "tar --numeric-owner --one-file-system -cpzf --exclude=./proc --exclude=./lost+found"
        cmd = cmd + " --exclude=./sys --exclude=./mnt --exclude=./media --exclude=./dev '"+dst_file_path+"' ."
        if not self.run_cmd(cmd, workingDir=nodePath):
            return(False)
        user = os.getenv("SUDO_USER")
        return(self.change_file_owner(dst_file_path, user))

    def untar_gz_archive_to_sdcard_rootfs(self, nodePath, src_script_path):
        """
        Un tars a tar.gz archive into the rootfs on the SDCARD using the scipt specified.
        """
        script = os.path.basename(src_script_path)
        script_loc = os.path.dirname(src_script_path)
        cmd = "sh '"+script+"' "+nodePath
        if not self.run_cmd(cmd, workingDir=script_loc):
            return(False)
        sync()
        return(True)

    def change_file_owner(self, file_path, new_owner):
        cmd = "chown "+new_owner+":"+new_owner+" "+file_path
        if not self.run_cmd(cmd):
            return(False)
        return(True)

    def change_file_permissions(self, file_path, permissions):
        cmd = "chmod "+permissions+" "+file_path
        if not self.run_cmd(cmd):
            return(False)
        return(True)

#########################################################################

def get_drive_selection(sysDevicesIF):
        FoundPossibleSDCard = False
        print("List of system devices.")
        for i, device in enumerate(sysDevicesIF.devices):
            Text = '  ' + str(i + 1) + ")  " + device.path + \
                   " [{0}]".format(device.size.pretty(units = "GiB"))
            if device.size <= sysDevicesIF.max_size_to_be_an_sdcard:
                print(Fore.GREEN + Text + Fore.RESET)
                FoundPossibleSDCard = True
            else:
                print(Style.DIM + Text + "  Not an SDCard" + Style.RESET_ALL)

        if not FoundPossibleSDCard:
            print(Fore.RED + "ERROR: no valid SDCards found in system. Aborting." + Fore.RESET)
            exit(-1)

        UserInput = raw_input("Choose target device then press <enter>:")
        if UserInput == "":
            print(Fore.RED + "User abort." + Fore.RESET)
            exit(0)
        try:
            Choice = sysDevicesIF.devices[int(UserInput) - 1]
            if sysDevicesIF.validate_device(Choice):
                return(Choice)
        except:
            pass
        print(Fore.RED + "ERROR: Invalid choice." + Fore.RESET)
        exit(-1)


def get_operation_selection(selectedDevice, operations):
    print("List of operations possible to perform on device ", end = '')
    print(Fore.GREEN + selectedDevice.path + Fore.RESET)
    for i, op in enumerate(operations):
        print('  ' + str(i + 1) + ') ' + op[0])
    UserInput = raw_input("Enter operation to perform then press <enter>:")
    if UserInput == "":
        print(Fore.RED + "User abort." + Fore.RESET)
        exit(0)
    try:
        Choice = operations[int(UserInput) - 1]
        return(Choice)
    except:
        pass
    print(Fore.RED + "ERROR: Invalid choice." + Fore.RESET)
    exit(-1)

def MountAllPartitions(sysDevicesIF, selectedDevice, args):
    if not sysDevicesIF.mount_fat_partition(selectedDevice):
        exit(-1)
    if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
        exit(-1)

def UnmountAllPartitions(sysDevicesIF, selectedDevice, args):
    if not sysDevicesIF.unmount_device(selectedDevice):
        exit(-1)

def PrepareSDCard(sysDevicesIF, selectedDevice, args, verifyOp = True):
    if verifyOp:
        print("")
        print(Fore.RED + "##########################################################" + Fore.RESET)
        print(Fore.RED + "THIS OPERATION WILL REPARTITION THE ENTIRE " + selectedDevice.path + " DEVICE" + Fore.RESET)
        print(Fore.RED + "!!!ALL DATA WILL BE LOST!!!" + Fore.RESET)
        print(Fore.RED + "##########################################################" + Fore.RESET)
        try:
            TargetDisk = reparted.Disk(selectedDevice)
            print("")
            print("Existing partitions on " + selectedDevice.path + ":")
            index = 1
            while True:
                Partition = TargetDisk.get_partition(index)
                index = index + 1
                if Partition.name:
                    Name = Partition.name
                else:
                    Name = str(Partition.num)
                print("  " + Name + "  " + Partition.size.pretty(units = "GiB"))
        except:
            pass
        print("")
    # Verify that the user REALLY wants to do this
    print("Type in 'yippie ki-yay' then press <enter> to perform the operation")
    UserInput = raw_input("or anything else to abort: ")
    if UserInput != "yippie ki-yay":
        print(Fore.RED + "User abort." + Fore.RESET)
        return

    print(Fore.GREEN + "Dis-mounting all mounts on " + selectedDevice.path + "..." + Fore.RESET)
    if not sysDevicesIF.unmount_device(selectedDevice):
        exit(-1)
    print(Fore.GREEN + "Writting zeros to first 1MB+1024 of SDCard to clear any left over data." + Fore.RESET)
    if not sysDevicesIF.zero_first_1mb(selectedDevice):
        exit(-1)
    print(Fore.GREEN + "Repartitioning to create FAT32 and Linux partitions." + Fore.RESET)
    # Set the SDCARD geometry such that the partitions align on cylinder boundaries.
    CylindersInDevice = int(selectedDevice.size.to("B") / CYLINDER_SIZE_BYTES)
    TotalBlocksInDevice = int(CylindersInDevice * CYLINDER_SIZE_BLKS)
    StartOfBootPartition  = 1024
    BlocksInBootPartition = int( 1 * CYLINDER_SIZE_BLKS) - 1024
    StartOfFatPartition   = int( 1 * CYLINDER_SIZE_BLKS)
    BlocksInFatPartition  = int(10 * CYLINDER_SIZE_BLKS)
    StartOfExtPartition   = int(11 * CYLINDER_SIZE_BLKS)
    BlocksInExtPartition  = int((TotalBlocksInDevice-StartOfExtPartition))
    # [ start, size, id/type, bootable ]
    Partitions = [ None, None, None ]
    Partitions[FAT_PARTITION-1]    = (str(StartOfFatPartition) , str(BlocksInFatPartition) , "0x0B", "*")
    Partitions[ROOTFS_PARTITION-1] = (str(StartOfExtPartition) , str(BlocksInExtPartition) , "0x83", "-")
    Partitions[RAW_PARTITION-1]    = (str(StartOfBootPartition), str(BlocksInBootPartition), "0xA2", "-")
    if not sysDevicesIF.create_partitions(selectedDevice, Partitions):
        exit(-1)

    print(Fore.GREEN + "Formatting partitions." + Fore.RESET)
    if not sysDevicesIF.format_fat_partition(selectedDevice):
        exit(-1)
    if not sysDevicesIF.format_rootfs_partition(selectedDevice):
        exit(-1)

    sleep(1)
    print(Fore.GREEN + "Mounting partitions." + Fore.RESET)
    if not sysDevicesIF.mount_fat_partition(selectedDevice):
        exit(-1)
    if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
        exit(-1)

    print(Fore.GREEN + "Repartitioning and Formatting complete." + Fore.RESET)

def InstallSPL(sysDevicesIF, selectedDevice, args):
    NodePath = selectedDevice.path + str(RAW_PARTITION)
    if args.spl_loc:
        SourceLoc = args.spl_loc
    else:
        print("")
        print("Preloader image files are located here:")
        ImagesPreloaderLoc = os.path.abspath(os.path.join(args.images_loc, IMAGE_FILES_RAW_LOC))
        print(Fore.GREEN+"  '"+ImagesPreloaderLoc+"'"+Fore.RESET)
        print("Choose preloader image from list:")
        fileList = glob.glob(ImagesPreloaderLoc+"/*.bin")
        for i, file in enumerate(fileList):
            file = os.path.basename(os.path.normpath(file))
            print('  ' + str(i + 1) + ') ' + file)
        UserInput = raw_input("Enter file # then press <enter>:")
        if UserInput == "":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
        try:
            SourceLoc = fileList[int(UserInput) - 1]
            print("SPL image will be read from : " + SourceLoc)
            print("SPL image will be written to: " + NodePath)
            if not sysDevicesIF.write_spl(selectedDevice, SourceLoc, NodePath):
                exit(-1)
            return
        except:
            e = sys.exc_info()[0]
            print(e)
            return
        print(Fore.RED + "ERROR: Invalid choice." + Fore.RESET)
        exit(-1)

def FormatFAT(sysDevicesIF, selectedDevice, args):
    if not args.force:
        print("Are you sure you want to format the FAT partition?")
        UserInput = raw_input("Type " + Fore.GREEN + "yes" + Fore.RESET + " or anything else to abort: ")
        if UserInput != "yes":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
    print("Dis-mounting all mounts on " + selectedDevice.path + "...")
    if not sysDevicesIF.unmount_device(selectedDevice):
        exit(-1)
    print("Formatting FAT partition...")
    if not sysDevicesIF.format_fat_partition(selectedDevice):
        exit(-1)
    print(Fore.GREEN + "Mounting partitions." + Fore.RESET)
    if not sysDevicesIF.mount_fat_partition(selectedDevice):
        exit(-1)
    if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
        pass
    print(Fore.GREEN + "Formatting FAT partition complete." + Fore.RESET)

def DeleteFATContents(sysDevicesIF, DestPath):
    for file_object in os.listdir(DestPath):
        file_object_path = os.path.join(DestPath, file_object)
        if os.path.isfile(file_object_path):
            print("Deleting file "+file_object_path)
            os.unlink(file_object_path)
        else:
            print("Deteting dir "+file_object_path)
            shutil.rmtree(file_object_path)

def WriteBootFiles(sysDevicesIF, selectedDevice, args, verifyOp = True):
    DestPath = sysDevicesIF.is_mounted(selectedDevice, FAT_PARTITION)
    if not DestPath:
        if not sysDevicesIF.mount_fat_partition(selectedDevice):
            exit(-1)
        DestPath = sysDevicesIF.is_mounted(selectedDevice, FAT_PARTITION)
        if not DestPath:
            print(Fore.RED + "ERROR: Unable to determine BOOT mount point." + Fore.RESET)
            exit(-1)
    print("")
    print("Delete all current files on FAT partition?")
    UserInput = raw_input("Type " + Fore.RED + "'yes'" + Fore.RESET + " or 'no (default)': ")
    if UserInput == "yes":
        DeleteFATContents(sysDevicesIF, DestPath)

    if args.boot_loc:
        SourceLoc = args.boot_loc
    else:
        print("")
        print("FAT image directories are located here:")
        ImagesBootLoc = os.path.abspath(os.path.join(args.images_loc, IMAGE_FILES_FAT_LOC))
        print(Fore.GREEN+"  '"+ImagesBootLoc+"'"+Fore.RESET)
        print("Choose boot source directory from list:")
        fileList = glob.glob(ImagesBootLoc+"/*/")
        for i, file in enumerate(fileList):
            file = os.path.basename(os.path.normpath(file))
            print('  ' + str(i + 1) + ') ' + file)
        UserInput = raw_input("Enter location # then press <enter>:")
        if UserInput == "":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
        SourceLoc = fileList[int(UserInput) - 1]
    print("BOOT files will be read from : " + SourceLoc)
    print("BOOT files will be written to: " + DestPath)
    if verifyOp:
        print("Are you sure you want to continue?")
        UserInput = raw_input("Type " + Fore.GREEN + "yes" + Fore.RESET + " or anything else to abort: ")
        if UserInput != "yes":
            print(Fore.RED + "User abort." + Fore.RESET)
            return

    # Copy each file in the file_list to the dest directory
    FileList = os.listdir(SourceLoc)
    for File in FileList:
        AbsFile = os.path.join(SourceLoc, File)
        if os.path.isfile(AbsFile):
            Dest = os.path.join(DestPath, File)
            print('  Coping "' + File + '" to ' + Dest)
            shutil.copyfile(AbsFile, Dest)
    sync()
    print(Fore.GREEN + "BOOT files have been copied to FAT partition." + Fore.RESET)

def CopyRootFS(sysDevicesIF, selectedDevice, args, verifyOp = True):
    SrcPath = sysDevicesIF.is_mounted(selectedDevice, ROOTFS_PARTITION)
    if not SrcPath:
        if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
            exit(-1)
        SrcPath = sysDevicesIF.is_mounted(selectedDevice, ROOTFS_PARTITION)
        if not SrcPath:
            print(Fore.RED + "ERROR: Unable to determine ROOTFS mount point." + Fore.RESET)
            exit(-1)
    if args.rootfs_copy_loc:
        DestLoc = args.rootfs_copy_loc
    else:
        print("")
        print("ROOTFS copy will be stored in the default image directory:")
        DestLoc = os.path.abspath(os.path.join(args.images_loc, IMAGE_FILES_ROOTFS_LOC))
        print(Fore.GREEN+"  '"+DestLoc+"'"+Fore.RESET)
        print("Enter file name of the archive (no extension):")
        BaseLoc = raw_input(">")
        if (BaseLoc == ""):
            print(Fore.RED + "User abort." + Fore.RESET)
            return
        BaseLoc = os.path.join(DestLoc, raw_input(">"))
        DestLoc = BaseLoc + ".tar.gz"
    print("ROOTFS files will be read from : " + SrcPath)
    print("ROOTFS files will be written to: " + DestLoc)
    if verifyOp:
        print("Are you sure you want to continue?")
        UserInput = raw_input("Type " + Fore.RED + "yes" + Fore.RESET + " or anything else to abort: ")
        if UserInput != "yes":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
    sysDevicesIF.copy_rootfs_to_gz_archive(SrcPath, DestLoc)
    # Now write out a simple script that can be used to write the ROOTFS to an SDCARD
    ScriptLoc = BaseLoc + ".sh"
    text_file = open(ScriptLoc, "w")
    text_file.write(
"""#!/bin/bash

if [ ${UID} -ne 0 ] ; then
    echo "${SELF}: error: you need root privileges. Use sudo."
    exit -1
fi

if [ -z "$1" ]
  then
    echo "error: you must specify the path to the SDCARD rootfs."
    exit -1
fi

tar -C $1 -xzpf """
                    )
    text_file.write(os.path.basename(DestLoc)+"\n")
    text_file.close()
    # fix ownership and permissions
    user = os.getenv("SUDO_USER")
    sysDevicesIF.change_file_owner(ScriptLoc, user)
    sysDevicesIF.change_file_permissions(ScriptLoc, "a+x")


def DeleteAllOnRootFs(sysDevicesIF, selectedDevice, args):
    if not args.force:
        print("Are you sure you want to format the ROOTFS partition?")
        UserInput = raw_input("Type " + Fore.GREEN + "yes" + Fore.RESET + " or anything else to abort: ")
        if UserInput != "yes":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
    print("Dis-mounting all mounts on " + selectedDevice.path + "...")
    if not sysDevicesIF.unmount_device(selectedDevice):
        exit(-1)
    print("Formatting ROOTFS partition...")
    if not sysDevicesIF.format_rootfs_partition(selectedDevice):
        exit(-1)
    print(Fore.GREEN + "Mounting partitions." + Fore.RESET)
    if not sysDevicesIF.mount_fat_partition(selectedDevice):
        exit(-1)
    if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
        pass
    print(Fore.GREEN + "Formatting ROOTFS partition complete." + Fore.RESET)


def InstallRootFS(sysDevicesIF, selectedDevice, args, verifyOp = True):
    DestPath = sysDevicesIF.is_mounted(selectedDevice, ROOTFS_PARTITION)
    if not DestPath:
        if not sysDevicesIF.mount_rootfs_partition(selectedDevice):
            exit(-1)
        DestPath = sysDevicesIF.is_mounted(selectedDevice, ROOTFS_PARTITION)
        if not DestPath:
            print(Fore.RED + "ERROR: Unable to determine ROOTFS mount point." + Fore.RESET)
            exit(-1)
    if args.rootfs_loc:
        SourceLoc = args.rootfs_loc
    else:
        print("")
        print("ROOTFS image scripts are located here:")
        ImagesRootfsLoc = os.path.abspath(os.path.join(args.images_loc, IMAGE_FILES_ROOTFS_LOC))
        print(Fore.GREEN+"  '"+ImagesRootfsLoc+"'"+Fore.RESET)
        print("Choose ROOTFS script from list:")
        fileList = glob.glob(ImagesRootfsLoc+"/*.sh")
        for i, file in enumerate(fileList):
            file = os.path.basename(file)
            print('  ' + str(i + 1) + ') ' + file)
        UserInput = raw_input("Enter file # then press <enter>:")
        if UserInput == "":
            print(Fore.RED + "User abort." + Fore.RESET)
            return
        SourceLoc = fileList[int(UserInput) - 1]

    print("ROOTFS files will be read from : " + SourceLoc)
    print("ROOTFS files will be written to: " + DestPath)
    if verifyOp:
        print("Are you sure you want to continue?")
        UserInput = raw_input("Type " + Fore.GREEN + "yes" + Fore.RESET + " or anything else to abort: ")
        if UserInput != "yes":
            print(Fore.RED + "User abort." + Fore.RESET)
            return

    if sysDevicesIF.untar_gz_archive_to_sdcard_rootfs(DestPath, SourceLoc):
        print(Fore.GREEN + "ROOTFS files have been copied to ROOTFS partition." + Fore.RESET)
    else:
        exit(-1)

####################################################################################################

if __name__ == '__main__':

    if os.geteuid() != 0:
        print("You must be root to run this script.")
        sys.exit(1)
    # Create a parser for the command line arguments.  Add any special options prior
    # to creating the esd_shell_utilities class instance.
    Parser = argparse.ArgumentParser()
    Parser.add_argument('-d', '--device', default = '',
                        help = 'Used to specify the SD block device node (e.g., sdc)')
    Parser.add_argument('--prepare_card', action = 'store_true',
                        help = 'Will re-partition and format the target device')
    Parser.add_argument('-i', '--images_loc', default = '../ImageFiles',
                        help = 'Specifies the image files directory containing partition directories.')
    Parser.add_argument('-s', '--spl_loc',
                        help = 'Specifies the source data for the SPL image to be written to the RAW partition.')
    Parser.add_argument('-r', '--rootfs_loc',
                        help = 'Specifies the source data for the files written to the ROOTFS partition.')
    Parser.add_argument('-c', '--rootfs_copy_loc',
                        help = 'Specifies the file path for storing a ROOTFS copy.')
    Parser.add_argument('-b', '--boot_loc',
                        help = 'Specifies the source data for the files written to the FAT partition.')
    Parser.add_argument('--logfile', default = 'log.txt',
                        help = 'Used to override the default log file.')
    Parser.add_argument('-f', '--force', action = 'store_true',
                        help = 'No user prompts to make sure before executing the chosen action.')
    Parser.add_argument('--list', action = 'store_true',
                        help = 'Outputs detected device information (list of devices).')
    Parser.add_argument('-v', '--verbose', action = 'store_true',
                        help = 'Increases the amount of output messages.')

    Args = Parser.parse_args()
    SysDevicesIF = SystemDevicesInterface(logFile = Args.logfile, echo_cmds = Args.verbose)

    if Args.list:
        SysDevicesIF.list_devices()
        exit(0)

    if Args.device:
        ArgDevice = SysDevicesIF.find_device(Args.device)
    else:
        ArgDevice = None

    # If the user has specified a SPL file location.
    # Check to make sure it is a valid file.
    if Args.spl_loc:
        if not os.path.isfile(Args.spl_loc):
            print(Fore.RED + "SPL file location specified '" + Args.spl_loc + "' is not valid." + Fore.RESET)
            exit(-1)

    # If the user has specified a boot files source location.
    # Check to make sure it is a valid directory.
    if Args.boot_loc:
        if not os.path.isdir(Args.boot_loc):
            print(Fore.RED + "Boot files location specified '" + Args.boot_loc + "' is not valid." + Fore.RESET)
            exit(-1)

    # If the user has specified a rootfs files source location.
    # Check to make sure it is a valid directory or file.
    if Args.rootfs_loc:
        if not os.path.isdir(Args.rootfs_loc):
            print(Fore.RED + "ROOTFS files location specified '" + Args.boot_loc + "' is not valid." + Fore.RESET)
            exit(-1)

    print("------------------------------------------------------")
    print("| Script to create Boot SD card for Altera SOC FPGAs |")
    print("------------------------------------------------------")
    print("")
    print(Fore.RED + "Warning: Will delete all data on target device!!!" + Fore.RESET)

    if (not Args.boot_loc) and (not Args.rootfs_loc) and (not Args.prepare_card):
        # no command line options to tell us what to do so go run the interactive version
        #--------------------------------------------------------------------------------
        Operations = [
                      ('mount partitions on SDCARD', MountAllPartitions),
                      ('unmount partitions on SDCARD', UnmountAllPartitions),
                      ('re-partition and format entire SDCARD', PrepareSDCard),
                      ('install SPL to RAW partition', InstallSPL),
                      ('format FAT partition', FormatFAT),
                      ('install boot files on FAT partition', WriteBootFiles),
                      ('copy ROOTFS from SDCARD to local archive', CopyRootFS),
                      ('format ROOTFS partition', DeleteAllOnRootFs),
                      ('install ROOTFS to SDCARD', InstallRootFS)
                     ]
        # If a device was specified use it otherwise ask
        if ArgDevice:
            if not SysDevicesIF.validate_device(ArgDevice):
                exit(-1)
            SelectedDrive = ArgDevice
        else:
            SelectedDrive = get_drive_selection(SysDevicesIF)
        while True:
            print("")
            SelectedOperation = get_operation_selection(SelectedDrive, Operations)
            SelectedOperation[1](SysDevicesIF, SelectedDrive, Args)
        exit(0)

    if Args.verbose:
        pprint.pprint(Args)

    # Verify that the specified drive is not a BIG drive.  This is a simple test to
    # ensure that we are not going to try to format any thing other than an SD card.
    # SD cards should be less than 32GiB.
    if not Args.device:
        print("ERROR: Missing target SDCard device.")
        print("")
        Parser.print_help()
        exit(-1)
    TargetDevice = ArgDevice

    if not SysDevicesIF.validate_device(TargetDevice):
        exit(-1)

    if Args.prepare_card:
        PrepareSDCard(SysDevicesIF, TargetDevice, Args, not Args.force)

    if Args.rootfs_loc:
        pass

    if Args.boot_loc:
        WriteBootFiles(SysDevicesIF, TargetDevice, Args, not Args.force)




