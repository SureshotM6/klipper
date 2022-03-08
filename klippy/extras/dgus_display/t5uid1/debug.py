# T5UID1 debug implementation
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, codecs

class T5UID1Debug:
    def __init__(self, config, t5uid1):
        self.printer = config.get_printer()
        self.t5uid1 = t5uid1

        name_parts = config.get_name().split()
        if len(name_parts) > 2:
            raise config.error("Section name '%s' is not valid"
                               % config.get_name())
        name = "default"
        if len(name_parts) > 1:
            name = name_parts[1]

        self.register_commands(name)
        if name == "default":
            self.register_commands(None)

    def get_status(self, eventtime):
        return {}

    def format_data(self, address, data, glen=16):
        result = []
        padding = address % glen
        address -= padding
        words = zip(data[::2], data[1::2])
        for _ in range(0, padding):
            words.insert(0, (None, None))
        groups = [words[i:i+glen] for i in range(0, len(words), glen)]
        offsets = []
        for i in range(0, glen):
            offsets.append("0x%04x" % i)
        result.append("------  %s" % " ".join(offsets))
        for idx, group in enumerate(groups):
            values = []
            for h, l in group:
                if h is None or l is None:
                    values.append("------")
                else:
                    values.append("0x%02x%02x" % (h, l))
            while len(values) < glen:
                values.append("------")
            result.append("0x%04x: %s"
                          % (address + glen * idx, " ".join(values)))
        return result

    def receive(self, address, data):
        lines = self.format_data(address, data)
        logging.info("T5UID1: %d bytes received:" % len(data))
        for line in lines:
            logging.info(line)

    def register_commands(self, display):
        self.t5uid1.register_base_commands(display)

        cmds = ["DGUS_READ", "DGUS_WRITE", "DGUS_SET_PAGE",
                "DGUS_ENABLE_CONTROL", "DGUS_DISABLE_CONTROL",
                "DGUS_READ_CONTROL", "DGUS_WRITE_CONTROL", "DGUS_READ_NOR"]
        gcode = self.printer.lookup_object("gcode")
        for cmd in cmds:
            gcode.register_mux_command(
                cmd, "DISPLAY", display, getattr(self, "cmd_" + cmd),
                desc=getattr(self, "cmd_" + cmd + "_help", None))

    cmd_DGUS_READ_help = "Read data from the screen"
    def cmd_DGUS_READ(self, gcmd):
        addr = gcmd.get("ADDR", minval=0, maxval=0xffff,
                        parser=lambda x: int(x, 0))
        wlen = gcmd.get("WLEN", minval=1, maxval=0x7d,
                        parser=lambda x: int(x, 0))
        try:
            data = self.t5uid1.read(addr, wlen)
        except self.t5uid1.error as e:
            raise gcmd.error(str(e))
        lines = self.format_data(addr, data)
        for line in lines:
            gcmd.respond_info(line)

    cmd_DGUS_WRITE_help = "Write data to the screen"
    def cmd_DGUS_WRITE(self, gcmd):
        addr = gcmd.get("ADDR", minval=0, maxval=0xffff,
                        parser=lambda x: int(x, 0))
        data = gcmd.get("DATA_STR", default=None,
                        parser=lambda x: bytearray(codecs.encode(x, "ascii")))
        if data is not None:
            if len(data) % 2 != 0:
                data.append(ord(" "))
        else:
            data = gcmd.get("DATA",
                            parser=lambda x: bytearray(codecs.decode(x, "hex")))
        if len(data) < 1 or len(data) > 248 or len(data) % 2 != 0:
            raise gcmd.error("Invalid DATA parameter")
        try:
            self.t5uid1.write(addr, data)
        except self.t5uid1.error as e:
            raise gcmd.error(str(e))

    cmd_DGUS_SET_PAGE_help = "Switch to a page"
    def cmd_DGUS_SET_PAGE(self, gcmd):
        page = gcmd.get("PAGE", minval=0, maxval=0xff,
                        parser=lambda x: int(x, 0))
        try:
            self.t5uid1.set_page(page)
        except self.t5uid1.error as e:
            raise gcmd.error(str(e))

    cmd_DGUS_READ_NOR_help = "Read NOR data into memory"
    def cmd_DGUS_READ_NOR(self, gcmd):
        nor_addr = gcmd.get("NOR_ADDR", minval=0, maxval=0x27fff,
                            parser=lambda x: int(x, 0))
        if nor_addr % 2 != 0:
            raise gcmd.error("Invalid NOR_ADDR parameter")
        addr = gcmd.get("ADDR", minval=0, maxval=0xffff,
                        parser=lambda x: int(x, 0))
        if addr % 2 != 0:
            raise gcmd.error("Invalid ADDR parameter")
        wlen = gcmd.get("WLEN", minval=1, maxval=0xffff,
                        parser=lambda x: int(x, 0))
        if wlen % 2 != 0:
            raise gcmd.error("Invalid WLEN parameter")
        try:
            self.t5uid1.read_nor(nor_addr, addr, wlen)
        except self.t5uid1.error as e:
            raise gcmd.error(str(e))

def init(config, t5uid1):
    return T5UID1Debug(config, t5uid1)
