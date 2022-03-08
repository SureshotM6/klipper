# T5UID1 implementation
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, threading
from . import lib, debug, dpm

DGUS_IMPLEMENTATION = {
    "debug": debug.init,
    "dgus_printer_menu": dpm.init
}

RW_TIMEOUT = 3.
COMMAND_TIMEOUT = 5.

class T5UID1Error(Exception):
    pass

class T5UID1:
    error = T5UID1Error

    def __init__(self, config, uart):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.config_name = config.get_name()

        if uart.rx_buffer < 48:
            raise config.error(
                "Option 'uart_rx_buffer' in section '%s' is not valid"
                % self.config_name)
        if uart.tx_buffer < 48:
            raise config.error(
                "Option 'uart_tx_buffer' in section '%s' is not valid"
                % self.config_name)
        if uart.rx_interval <= 0 or uart.rx_interval > 100:
            raise config.error(
                "Option 'uart_rx_interval' in section '%s' is not valid"
                % self.config_name)

        self._uart = uart

        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._completion = (None, None)

        self._brightness = config.getint("brightness", 100, minval=0,
                                         maxval=100)

        self.printer.register_event_handler("klippy:ready", self._handle_ready)

        self.impl = config.getchoice("implementation", DGUS_IMPLEMENTATION,
                                default="debug")(config, self)

    def get_status(self, eventtime):
        return self.impl.get_status(eventtime)

    def setup_shutdown_msg(self, data):
        if len(data) < 1:
            return
        try:
            self._uart.setup_shutdown_msg(data)
        except ValueError as e:
            raise self.error(str(e))

    def map_range(self, value, imin, imax, omin, omax):
        result = (value - imin) * (omax - omin) / (imax - imin) + omin
        if type(value) is int:
            result = int(round(result))
        return max(omin, min(result, omax))

    def _handle_ready(self):
        self.play_sound(1000)
        logging.info("LCD version: %s", self.get_version())
        self.set_brightness(self._brightness, force=True)

    def send(self, data, minclock=0, reqclock=0):
        try:
            logging.debug("UART TX[%u]: %s", len(data), data.hex())
            self._uart.uart_send(data, minclock=minclock, reqclock=reqclock)
        except ValueError as e:
            raise self.error(str(e))

    def _find_response(self):
        buf_len = len(self._buffer)
        if buf_len < 3:
            return False, 0
        if self._buffer[0] != 0x5a or self._buffer[1] != 0xa5:
            return False, 1
        cmd_len = self._buffer[2]
        if cmd_len < 1:
            return False, 3
        response_len = cmd_len + 3
        if buf_len < response_len:
            return False, 0
        return True, response_len

    def _process_command(self, command, data):
        if command == lib.Command.RAM_R:
            # also received for async VP updates
            # addr[2] mlen msg[]
            dlen = len(data)
            if dlen < 3:
                logging.warn("T5UID1: Invalid message")
                return
            try:
                addr, mlen = lib.unpack(data[:3], "uint16", "uint8")
            except lib.error:
                logging.warn("T5UID1: Invalid message")
                return
            mlen *= 2
            if dlen < mlen + 3:
                logging.warn("T5UID1: Invalid message")
                return
            with self._lock:
                cdata, completion = self._completion
                if cdata is not None and cdata[4:7] == data[:3]:
                    self._completion = (None, None)
                    self.reactor.async_complete(completion, data[3:mlen + 3])
            self.reactor.register_async_callback(
                (lambda e, s=self, a=addr, d=data[3:mlen + 3]:
                 s.impl.receive(a, d)))
        elif command == lib.Command.REG_R:
            # addr mlen msg[]
            dlen = len(data)
            if dlen < 2:
                logging.warn("T5UID1: Invalid message")
                return
            try:
                addr, mlen = lib.unpack(data[:2], "uint8", "uint8")
            except lib.error:
                logging.warn("T5UID1: Invalid message")
                return
            if dlen != mlen + 2:
                logging.warn("T5UID1: Invalid message")
                return
            with self._lock:
                cdata, completion = self._completion
                if cdata[4:6] == data[:2] and completion is not None:
                    self._completion = (None, None)
                    self.reactor.async_complete(completion, data[2:])
        else:
            logging.error("T5UID1: Unknown Rx command: 0x%02x: %s", command, data.hex())

    def process(self, data):
        logging.debug("UART RX[%u]: %s", len(data), data.hex())

        if len(data) < 1:
            self._buffer = bytearray()
            logging.warn("T5UID1: Serial RX overflow")
            return
        self._buffer.extend(data)
        while True:
            has_response, pop_count = self._find_response()
            if has_response:
                self._process_command(self._buffer[3],
                                      self._buffer[4:pop_count])
            if pop_count > 0:
                self._buffer = self._buffer[pop_count:]
            else:
                break

    def read(self, cdata):
        while True:
            if self.printer.is_shutdown():
                raise self.error("Printer is shutdown")
            with self._lock:
                completion = self._completion[1]
                if completion is None:
                    completion = self.reactor.completion()
                    self._completion = (cdata, completion)
                    break
            completion.wait()
        systime = self.reactor.monotonic()
        print_time = self._uart.mcu.estimated_print_time(systime) + 0.100
        reqclock = self._uart.mcu.print_time_to_clock(print_time)
        self.send(cdata, reqclock=reqclock)
        result = completion.wait(systime + RW_TIMEOUT)
        completion.complete(None)
        with self._lock:
            if self._completion[1] == completion:
                self._completion = (None, None)
        if type(result) is not bytearray:
            raise self.error("Timeout waiting for response")
        return result

    # TODO: may still need to wait for reads?
    def write(self, cdata):
        return self.send(cdata)

    def get_version(self):
        version, = lib.unpack(self.read(lib.get_version()), "uint8")
        return (version >> 4, version & 0xf)

    def get_page(self):
        page, = lib.unpack(self.read(lib.get_page()), "uint16")
        return page

    def set_page(self, page, wait=True):
        cdata = lib.set_page(page)
        self.write(cdata)
        if not wait:
            return
        systime = self.reactor.monotonic()
        timeout = systime + COMMAND_TIMEOUT
        while not self.printer.is_shutdown():
            if page == self.get_page():
                return
            if systime > timeout:
                raise self.error("Timeout waiting for page change")
            systime = self.reactor.pause(systime + 0.050)

    def play_sound(self, len_ms):
        cdata = lib.play_sound(len_ms)
        self.write(cdata)

    def stop_sound(self):
        self.play_sound(0)

    def get_brightness(self, bypass=False):
        if not bypass:
            return self._brightness
        brightness, = lib.unpack(self.read(lib.get_brightness()), "uint8")
        return brightness

    def set_brightness(self, brightness, save=False, force=False):
        if not force and brightness == self._brightness:
            return
        cdata = lib.set_brightness(brightness)
        self.write(cdata)
        if save:
            self._brightness = brightness
            configfile = self.printer.lookup_object("configfile")
            configfile.set(self.config_name, "brightness", brightness)

    def read_nor(self, nor_address, address, wlen):
        cdata = lib.read_nor(nor_address, address, wlen)
        self.write(cdata)
        systime = self.reactor.monotonic()
        timeout = systime + COMMAND_TIMEOUT
        while not self.printer.is_shutdown():
            flag, = lib.unpack(self.read(cdata), "uint8")
            if flag != 0x5a:
                return
            if systime > timeout:
                raise self.error("Timeout waiting for acknowledgement")
            systime = self.reactor.pause(systime + 0.050)

    def register_base_commands(self, display):
        cmds = ["DGUS_PLAY_SOUND", "DGUS_STOP_SOUND",
                "DGUS_GET_BRIGHTNESS", "DGUS_SET_BRIGHTNESS"]
        gcode = self.printer.lookup_object("gcode")
        for cmd in cmds:
            gcode.register_mux_command(
                cmd, "DISPLAY", display, getattr(self, "cmd_" + cmd),
                desc=getattr(self, "cmd_" + cmd + "_help", None))

    cmd_DGUS_PLAY_SOUND_help = "Play a sound"
    def cmd_DGUS_PLAY_SOUND(self, gcmd):
        slen = gcmd.get_int("LEN", default=1, minval=0, maxval=255*10)
        try:
            self.play_sound(slen)
        except self.error as e:
            raise gcmd.error(str(e))

    cmd_DGUS_STOP_SOUND_help = "Stop any currently playing sound"
    def cmd_DGUS_STOP_SOUND(self, gcmd):
        try:
            self.stop_sound()
        except self.error as e:
            raise gcmd.error(str(e))

    cmd_DGUS_GET_BRIGHTNESS_help = "Get the brightness"
    def cmd_DGUS_GET_BRIGHTNESS(self, gcmd):
        try:
            brightness = self.get_brightness()
        except self.error as e:
            raise gcmd.error(str(e))
        gcmd.respond_info("Brightness: %d%%" % brightness)

    cmd_DGUS_SET_BRIGHTNESS_help = "Set the brightness"
    def cmd_DGUS_SET_BRIGHTNESS(self, gcmd):
        brightness = gcmd.get_int("BRIGHTNESS", minval=0, maxval=100)
        save = gcmd.get_int("SAVE", 0)
        try:
            self.set_brightness(brightness, save=save)
        except self.error as e:
            raise gcmd.error(str(e))
