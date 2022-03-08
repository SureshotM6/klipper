# T5UID1 library
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import struct
from enum import IntEnum, unique

MAX_RAM_ADDR = 4096 / 2 - 1
MAX_PAGE = 374 - 1

@unique
class Command(IntEnum):
    REG_W = 0x80
    REG_R = 0x81
    RAM_W = 0x82
    RAM_R = 0x83
    CURVE_W = 0x84

@unique
class Reg(IntEnum):
    VERSION = 0x00
    BRIGHTNESS = 0x01
    BUZZ = 0x02
    PIC_ID = 0x03

TYPES = {
    "int8": "b",
    "uint8": "B",
    "int16": "h",
    "uint16": "H",
    "int32": "i",
    "uint32": "I",
}

class error(Exception):
    pass

def pack(*args):
    args_len = len(args)
    if args_len % 2 != 0:
        raise error("Invalid arguments")
    it = range(0, args_len, 2)
    format = "".join([TYPES[args[i]] for i in it])
    values = [args[i+1] for i in it]
    try:
        return bytearray(struct.pack(">" + format, *values))
    except:
        raise error("Invalid arguments")

def unpack(buffer, *args, **kwargs):
    args_len = len(args)
    if args_len < 1:
        raise error("Invalid arguments")
    size = len(buffer)
    format = ""
    for arg in args:
        format += TYPES[arg]
        size -= struct.calcsize(TYPES[arg])
    if size > 0 and not kwargs.get("strict", False):
        format += TYPES["uint8"] * size
    try:
        return struct.unpack(">" + format, buffer)[:args_len]
    except:
        raise error("Invalid arguments")

def build_command(command, data):
    message = pack("uint16", 0x5aa5,
                   "uint8", len(data) + 1,
                   "uint8", command)
    message.extend(data)
    return message

def read_word(address, rlen=1):
    if address < 0 or address > MAX_RAM_ADDR:
        raise error("Invalid address")
    if rlen < 1 or rlen > 0x7d:
        raise error("Invalid rlen")
    cdata = pack("uint16", address,
                 "uint8", rlen)
    return build_command(Command.RAM_R, cdata)

def write_word(address, data):
    if address < 0 or address > MAX_RAM_ADDR:
        raise error("Invalid address")
    if len(data) < 1 or len(data) > 248 or len(data) % 2 != 0:
        raise error("Invalid data length")
    cdata = pack("uint16", address)
    cdata.extend(data)
    return build_command(Command.RAM_W, cdata)

def read_reg(address, rlen=1):
    if address < 0 or address > 0xff:
        raise error("Invalid address")
    if rlen < 1 or rlen > 0xff:
        raise error("Invalid rlen")
    cdata = pack("uint8", address,
                 "uint8", rlen)
    return build_command(Command.REG_R, cdata)

def write_reg(address, data):
    if address < 0 or address > 0xff:
        raise error("Invalid address")
    if len(data) < 1 or len(data) > 248:
        raise error("Invalid data length")
    cdata = pack("uint8", address)
    cdata.extend(data)
    return build_command(Command.REG_W, cdata)

def get_version():
    return read_reg(Reg.VERSION)

def get_page():
    return read_reg(Reg.PIC_ID, 2)

def set_page(page):
    if page < 0 or page > MAX_PAGE:
        raise error("Invalid page")
    cdata = pack("uint16", page)
    return write_reg(Reg.PIC_ID, cdata)

def play_sound(len_ms=100):
    if len_ms < 0 or len_ms > 0xff * 10:
        raise error("Invalid len")
    cdata = pack("uint8", (len_ms + 9) // 10)
    return write_reg(Reg.BUZZ, cdata)

def stop_sound():
    return play_sound(0)

def get_brightness():
    return read_reg(Reg.BRIGHTNESS)

def set_brightness(brightness):
    if brightness < 0 or brightness > 100:
        raise error("Invalid brightness")
    cdata = pack("uint8", brightness * 0x40 // 100)
    return write_reg(Reg.BRIGHTNESS, cdata)

# inputs cannot be edited at runtime, only variables (via SP)
def read_nor(nor_address, address, wlen):
    raise error("not supported yet")
