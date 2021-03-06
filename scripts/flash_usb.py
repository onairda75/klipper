#!/usr/bin/env python2
# Tool to enter a USB bootloader and flash Klipper
#
# Copyright (C) 2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, re, subprocess, optparse, time, fcntl, termios, struct

class error(Exception):
    pass

# Attempt to enter bootloader via 1200 baud request
def enter_bootloader(device):
    try:
        f = open(device, 'rb')
        fd = f.fileno()
        fcntl.ioctl(fd, termios.TIOCMBIS, struct.pack('I', termios.TIOCM_DTR))
        t = termios.tcgetattr(fd)
        t[4] = t[5] = termios.B1200
        termios.tcsetattr(fd, termios.TCSANOW, t)
        fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack('I', termios.TIOCM_DTR))
        f.close()
    except (IOError, OSError) as e:
        pass
    time.sleep(1.0)

# Translate a serial device name to a stable serial name in /dev/serial/by-path/
def translate_serial_to_tty(device):
    ttyname = os.path.realpath(device)
    for fname in os.listdir('/dev/serial/by-path/'):
        fname = '/dev/serial/by-path/' + fname
        if os.path.realpath(fname) == ttyname:
            return ttyname, fname
    return ttyname, ttyname

# Translate a serial device name to a usb path (suitable for dfu-util)
def translate_serial_to_usb_path(device):
    realdev = os.path.realpath(device)
    fname = os.path.basename(realdev)
    try:
        lname = os.readlink("/sys/class/tty/" + fname)
    except OSError as e:
        raise error("Unable to find tty device")
    ttypath_r = re.compile(r".*/usb\d+.*/(?P<path>\d+-[0-9.]+):\d+\.\d+/.*")
    m = ttypath_r.match(lname)
    if m is None:
        raise error("Unable to find tty usb device")
    return m.group("path")

# Flash via a call to bossac
def flash_bossac(device, binfile, extra_flags=[]):
    ttyname, pathname = translate_serial_to_tty(device)
    enter_bootloader(pathname)
    if os.path.exists(ttyname) and not os.path.exists(pathname):
        pathname = ttyname
    baseargs = ["lib/bossac/bin/bossac", "-U", "-p", pathname]
    args = baseargs + extra_flags + ["-w", binfile, "-v", "-b"]
    sys.stderr.write(" ".join(args) + '\n\n')
    res = subprocess.call(args)
    if res != 0:
        raise error("Error running bossac")
    if "-R" not in extra_flags:
        time.sleep(0.500)
        args = baseargs + ["-b", "-R"]
        try:
            subprocess.check_output(args, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            pass

# Invoke the dfu-util program
def call_dfuutil(flags, binfile):
    args = ["dfu-util"] + flags + ["-D", binfile]
    sys.stderr.write(" ".join(args) + '\n\n')
    res = subprocess.call(args)
    if res != 0:
        raise error("Error running dfu-util")

# Flash via a call to dfu-util
def flash_dfuutil(device, binfile, extra_flags=[]):
    hexfmt_r = re.compile(r"^[a-fA-F0-9]{4}:[a-fA-F0-9]{4}$")
    if hexfmt_r.match(device.strip()):
        call_dfuutil(["-d", ","+device.strip()] + extra_flags, binfile)
        return
    buspath = translate_serial_to_usb_path(device)
    enter_bootloader(device)
    call_dfuutil(["-p", buspath] + extra_flags, binfile)


######################################################################
# Device specific helpers
######################################################################

def flash_atsam(options, binfile):
    try:
        flash_bossac(options.device, binfile, ["-e"])
    except error as e:
        sys.stderr.write("Failed to flash to %s: %s\n" % (
            options.device, str(e)))
        sys.exit(-1)

def flash_atsamd(options, binfile):
    extra_flags = ["--offset=" + options.offset, "-R"]
    try:
        flash_bossac(options.device, binfile, extra_flags)
    except error as e:
        sys.stderr.write("Failed to flash to %s: %s\n" % (
            options.device, str(e)))
        sys.exit(-1)

SMOOTHIE_HELP = """
Failed to flash to %s: %s

If flashing Klipper to a Smoothieboard for the first time it may be
necessary to manually place the board into "bootloader mode" - press
and hold the "Play button" and then press and release the "Reset
button".

When a Smoothieboard is in bootloader mode it can be flashed with the
following command:
  make flash FLASH_DEVICE=1d50:6015

Alternatively, one can flash a Smoothieboard via SD card - copy the
"out/klipper.bin" file to a file named "firmware.bin" on an SD card
and then restart the Smoothieboard with that SD card.

"""

def flash_lpc176x(options, binfile):
    try:
        flash_dfuutil(options.device, binfile)
    except error as e:
        sys.stderr.write(SMOOTHIE_HELP % (options.device, str(e)))
        sys.exit(-1)

STM32F1_HELP = """
Failed to flash to %s: %s

If the device is already in bootloader mode it can be flashed with the
following command:
  make flash FLASH_DEVICE=1eaf:0003

If attempting to flash via 3.3V serial, then use:
  make serialflash FLASH_DEVICE=%s

"""

def flash_stm32f1(options, binfile):
    try:
        flash_dfuutil(options.device, binfile, ["-R", "-a", "2"])
    except error as e:
        sys.stderr.write(STM32F1_HELP % (
            options.device, str(e), options.device))
        sys.exit(-1)

MCUTYPES = {
    'atsam': flash_atsam, 'atsamd': flash_atsamd,
    'lpc176x': flash_lpc176x, 'stm32f1': flash_stm32f1
}


######################################################################
# Startup
######################################################################

def main():
    usage = "%prog [options] -t <type> -d <device> <klipper.bin>"
    opts = optparse.OptionParser(usage)
    opts.add_option("-t", "--type", type="string", dest="mcutype",
                    help="micro-controller type")
    opts.add_option("-d", "--device", type="string", dest="device",
                    help="serial port device")
    opts.add_option("-o", "--offset", type="string", dest="offset",
                    help="flash offset")
    options, args = opts.parse_args()
    if len(args) != 1:
        opts.error("Incorrect number of arguments")
    if options.mcutype not in MCUTYPES:
        opts.error("Not a valid mcu type")
    if not options.device:
        sys.stderr.write("\nPlease specify FLASH_DEVICE\n\n")
        sys.exit(-1)
    MCUTYPES[options.mcutype](options, args[0])

if __name__ == '__main__':
    main()
