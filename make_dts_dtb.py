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
# Script to create full DTS and DTB files from board level DTS using the
# include files in the Linux source tree.
#
# Requires the following Python modules:
#   shell_helper (https://github.com/byates/shell_helper)
#-------------------------------------------------------------------------------

from __future__ import print_function

import sys
import os
import argparse
import glob
import shutil
import pprint
from shell_helper import ShellHelper
from time import sleep

EchoCmds = False
LastCommandResult = ""

def run_cmd(cmd, workingDir = "", inputStr = None, suppress_errors={}):
    """
    Runs the speicifed command and reports any errors.
    Returns True if command had no errors otherwise False.
    LastCommandResult holds the command result code.
    """
    global ShellHelperInst
    global EchoCmds
    global LastCommandResult
    r = ShellHelperInst.RunCmdCaptureOutput(cmd, workingDir, inputStr, echo_cmd = EchoCmds)
    if EchoCmds:
        if r[1]:
            for line in r[1]:
                print("  " + line)
    LastCommandResult = r[0]
    # Check for errors (non-zero return code)
    if LastCommandResult != 0 and (not LastCommandResult in suppress_errors):
        if r[2]:
            for line in r[2]:
                print(line)
        cmdName = cmd.split()[0]
        print("ERROR: "+cmdName+" failed with code {}.".format(r[0]))
        return(False)
    return(True)

####################################################################################################

if __name__ == '__main__':

    # Create a parser for the command line arguments.  Add any special options prior
    # to creating the esd_shell_utilities class instance.
    Parser = argparse.ArgumentParser()
    Parser.add_argument('-k', '--kernel_loc', default = '',
                        help = 'Used to specify the location of the kernel source. Default=$ALTERA_SOC_LINUX_KERNEL_LOC')
    Parser.add_argument('-c', '--compiler', default = '',
                        help = 'Used to specify the location of compiler to use. Default=$CROSS_COMPILE')
    Parser.add_argument('-a', '--arch', default = 'arm',
                        help = 'Used to specify the target architecture. Default=arm')
    Parser.add_argument('--logfile', default = None,
                        help = 'Used to enable and specify a log file.')
    Parser.add_argument('-v', '--verbose', action = 'store_true',
                        help = 'Echos commands to console.')
    Parser.add_argument('dts_file', default='',
                        help = 'board level dts file to compile.')

    Args = Parser.parse_args()

    EchoCmds = Args.verbose
    ShellHelperInst = ShellHelper(logfile = Args.logfile)

    # If the user has specified a location for the kernel source then use it otherwise
    # try the environment variable.
    if Args.kernel_loc == "":
        kernel_src_loc = os.getenv("ALTERA_SOC_LINUX_KERNEL_LOC")
    else:
        kernel_src_loc = Args.kernel_loc
    if ( (not os.path.isdir(kernel_src_loc)) or \
         (not os.path.isdir(os.path.join(kernel_src_loc, "arch"))) or \
         (not os.path.isdir(os.path.join(kernel_src_loc, "scripts"))) \
       ):
        print("Kernel source location specified '" + kernel_src_loc + "' is not valid.")
        exit(-1)

    # If the user has specified a location for the compiler then use it otherwise
    # try the environment variable.
    if Args.compiler == "":
        compiler = os.getenv("CROSS_COMPILE")
        if not compiler:
            print("ERROR: No compiler specified.")
            Parser.print_help()
            exit(-1)
        compiler = compiler + "gcc"
    else:
        compiler = Args.compiler
    if not os.path.isfile(compiler):
        print("Compiler specified '" + compiler + "' is not valid.")
        exit(-1)
    
    # If the user has specified a source file and check to make sure it is a valid file.
    if not Args.dts_file:
        print("ERROR: Missing source file.")
        print("")
        Parser.print_help()
        exit(-1)
    if Args.dts_file:
        if not os.path.isfile(Args.dts_file):
            print("Source file '" + Args.boot_loc + "' is not valid.")
            exit(-1)

    fullpath = os.path.abspath(Args.dts_file)
    filename = os.path.basename(fullpath)
    dir = os.path.dirname(fullpath)
    base, ext = os.path.splitext(filename)
    abs_base = os.path.join(dir, base)
    
    if ext != ".dts":
        print("ERROR: Expected DTS source file.")
        print("")
        exit(-1)
            
    DTC_CPP_FLAGS="-E -Wp,-MD,{0}.pre.tmp -nostdinc -Iarch/{1}/boot/dts -Iarch/{1}/boot/dts/include -undef -D__DTS__  -x assembler-with-cpp"
    DTC_CPP_FLAGS=DTC_CPP_FLAGS.format(abs_base, Args.arch)
    
    # Run source through the compiler preprocessor to handle include files
    TempFile = abs_base+".tmp"
    cmd=compiler+" "+DTC_CPP_FLAGS+" -o "+TempFile+" "+fullpath
    if not run_cmd(cmd=cmd, workingDir=kernel_src_loc):
        print("ERROR: Preprocessing of DTS failed.")
        exit(-1)
        
    # Use Linux DTS compiler to produce the full DTS
    DTC_DTC_FLAGS="-O dts -o {0}.out.dts -b 0 -i arch/{1}/boot/dts -d {0}.dtc.tmp {2}"
    DTC_DTC_FLAGS=DTC_DTC_FLAGS.format(abs_base, Args.arch, TempFile)
    cmd="scripts/dtc/dtc "+DTC_DTC_FLAGS
    if not run_cmd(cmd=cmd, workingDir=kernel_src_loc):
        print("ERROR: Unbale to produce full DTS file.")
        exit(-1)

    # remove temporary files
    cmd="rm {0}*.tmp".format(abs_base)
    run_cmd(cmd)

    # Use Linux DTS compiler to produce the DTB from the full DTS
    DTC_DTC_FLAGS="-I dts -O dtb -o {0}.dtb {0}.out.dts"
    DTC_DTC_FLAGS=DTC_DTC_FLAGS.format(abs_base)
    cmd="scripts/dtc/dtc "+DTC_DTC_FLAGS
    if not run_cmd(cmd=cmd, workingDir=kernel_src_loc):
        print("ERROR: Unbale to produce DTB file.")
        exit(-1)
    
    



