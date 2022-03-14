# Support for DGUS touchscreens
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from .. import bus
from . import t5uid1

DGUS_TYPE = {
    "dgusm": t5uid1.init,
}

class DGUSDisplay:
    def __init__(self, config):
        self.uart = bus.MCU_UART_from_config(config, self.receive_cb,
                                             default_rx_buffer=96,
                                             default_tx_buffer=192,
                                             default_rx_interval=25)
        config.get_printer().load_object(config, "dgus_status")
        self.impl = config.getchoice("type", DGUS_TYPE)(config, self.uart)

    def get_status(self, eventtime):
        return self.impl.get_status(eventtime)

    def receive_cb(self, data):
        self.impl.process(data)

def load_config(config):
    return DGUSDisplay(config)
