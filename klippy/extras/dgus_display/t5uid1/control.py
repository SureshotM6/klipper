# T5UID1 controls
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import codecs
import logging
from . import lib

class ControlError(Exception):
    pass

class sentinel:
    pass

def config_getint(config, option, default=sentinel, minval=None, maxval=None):
    if default is sentinel:
        value = config.get(option)
    else:
        value = config.get(option, default)
    try:
        result = int(value, 0)
    except:
        raise config.error("Option '%s' in section '%s' is not valid"
                           % (option, config.get_name()))
    if minval is not None and result < minval:
        raise config.error("Option '%s' in section '%s' must have minimum of %s"
                           % (option, config.get_name(), minval))
    if maxval is not None and result > maxval:
        raise config.error("Option '%s' in section '%s' must have maximum of %s"
                           % (option, config.get_name(), maxval))
    return result

######################################################################
# Display controls
######################################################################

class DisplayControl(object):
    error = ControlError

    def __init__(self, config, data_type=None):
        if type(self) is DisplayControl:
            raise self.error(
                "Abstract DisplayControl cannot be instantiated directly")

        name_parts = config.get_name().split()
        if len(name_parts) > 3:
            raise config.error("Section name '%s' is not valid"
                               % config.get_name())
        if len(name_parts) > 2:
            self.page = name_parts[1]
            self.name = name_parts[2]
        else:
            self.page = None
            self.name = name_parts[1]

        self.vp = config_getint(config, "vp", minval=0, maxval=lib.MAX_RAM_ADDR)
        if data_type is None:
            self.data_type = config.get("data_type")
            if self.data_type not in lib.TYPES:
                raise config.error(
                    "Option 'data_type' in section '%s' is not valid"
                    % config.get_name())
        else:
            self.data_type = data_type
        self.value = None

    def transform(self, value):
        return value

    def format(self, value):
        if self.data_type not in lib.TYPES or self.data_type.endswith("int8"):
            raise self.error("Cannot format type '%s'" % self.data_type)
        try:
            data = lib.pack(self.data_type, value)
        except lib.error:
            raise self.error("Invalid value")
        return data

    def diff(self, old_value, new_value):
        end = len(new_value)
        if old_value == new_value or len(old_value) != end:
            return (0, new_value)
        start = 0
        while start < end - 1:
            if (old_value[start] != new_value[start]
                or old_value[start + 1] != new_value[start + 1]):
                break
            start += 2
        while end > start:
            if (old_value[end - 1] != new_value[end - 1]
                or old_value[end - 2] != new_value[end - 2]):
                break
            end -= 2
        return (start // 2, new_value[start:end])

    def set_value(self, value, update=True, **kwargs):
        value = self.transform(value, **kwargs)
        new_data = self.format(value)
        logging.debug("Update %s to %s", str(self), bytes(new_data))
        if not update:
            return lib.write_word(self.vp, new_data)
        if self.value == value:
            return None
        old_value = self.value
        self.value = value
        if old_value is None:
            return lib.write_word(self.vp, new_data)
        offset, data = self.diff(self.format(old_value), new_data)
        if len(data) < 1:
            return None
        return lib.write_word(self.vp + offset, data)

    def __str__(self):
        return "{}::{} ({})".format(self.page or "(none)", self.name, type(self).__name__)

class DC_VariableIcon(DisplayControl):
    def __init__(self, config):
        super(DC_VariableIcon, self).__init__(config, "uint16")

        self.map = {}
        for option in config.get_prefix_options("map_"):
            name = option[4:].strip()
            self.map[name] = config_getint(config, option,
                                           minval=0, maxval=0xffff)

    def transform(self, value):
        return self.map.get(value, value)

class DC_SliderIcon(DisplayControl):
    def __init__(self, config):
        super(DC_SliderIcon, self).__init__(config, "uint16")

class DC_BitIcon(DisplayControl):
    def __init__(self, config):
        super(DC_BitIcon, self).__init__(config, "uint16")

        self.rank = config.getint("rank", None, minval=0, maxval=15)
        if self.rank is None:
            ranks = {}
            for option in config.get_prefix_options("rank_"):
                name = option[5:].strip()
                ranks[name] = config.getint(option, minval=0, maxval=15)
            if len(ranks) > 0:
                self.rank = ranks

    def transform(self, value):
        if type(self.rank) is int:
            if type(value) is not bool:
                raise self.error("Invalid value")
            return (int(value) << self.rank)
        if type(self.rank) is dict:
            if type(value) is not list and type(value) is not tuple:
                raise self.error("Invalid value")
            bits = 0
            for name in value:
                rank = self.rank.get(name, None)
                if rank is None:
                    raise self.error("Invalid value")
                bits |= 1 << rank
            return bits
        return value

class DC_Data(DisplayControl):
    pass

class DC_Text(DisplayControl):
    def __init__(self, config):
        super(DC_Text, self).__init__(config, "string")

        self.length = config.getint("length", minval=1)
        self.alignment = config.get("alignment", "left")
        if self.alignment not in ["left", "center", "right"]:
            raise config.error(
                "Option 'alignment' in section '%s' is not valid"
                % config.get_name())
        self.pad = config.getboolean("pad", False)

    def transform(self, value, alignment=None, pad=None):
        if alignment is None:
            alignment = self.alignment
        if pad is None:
            pad = self.pad
        value = value.strip()[:self.length]
        diff = self.length - len(value)
        if diff > 0 and diff < self.length:
            if alignment == "center":
                value = (" " * (diff // 2 + diff % 2)) + value
            elif alignment == "right":
                value = (" " * diff) + value
        if pad:
            value += (" " * (self.length - len(value)))
        return value

    def format(self, value):
        try:
            data = bytearray(codecs.encode(value, "ascii"))
        except:
            raise self.error("Invalid value")
        dlen = len(data)
        if dlen < self.length - 1:
            data.extend([ 0xff, 0xff ])
            if dlen % 2 != 0:
                data.append(0xff)
        elif dlen % 2 != 0:
            data.append(ord(" "))
        return data

    def diff(self, old_value, new_value):
        if old_value == new_value:
            return (0, new_value)
        start = 0
        end = min(len(old_value), len(new_value))
        while start < end - 1:
            if (old_value[start] != new_value[start]
                or old_value[start + 1] != new_value[start + 1]):
                break
            start += 2
        return (start // 2, new_value[start:])

    def set_length(self, length):
        if length < 1:
            raise self.error("Invalid length")
        self.length = length
        self.value = None

DISPLAY_CONTROL_TYPES = {
    "variable_icon": DC_VariableIcon,
    "slider_icon": DC_SliderIcon,
    "bit_icon": DC_BitIcon,
    "data": DC_Data,
    "text": DC_Text
}

######################################################################
# Touch controls
######################################################################

class TouchControl(object):
    error = ControlError

    def __init__(self, config, data_type=None):
        if type(self) is TouchControl:
            raise self.error(
                "Abstract TouchControl cannot be instantiated directly")

        name_parts = config.get_name().split()
        if len(name_parts) > 3:
            raise config.error("Section name '%s' is not valid"
                               % config.get_name())
        if len(name_parts) > 2:
            self.page = name_parts[1]
            self.name = name_parts[2]
        else:
            self.page = None
            self.name = name_parts[1]

        self.vp = config_getint(config, "vp", minval=0, maxval=lib.MAX_RAM_ADDR)
        if data_type is None:
            self.data_type = config.get("data_type")
            if self.data_type not in lib.TYPES:
                raise config.error(
                    "Option 'data_type' in section '%s' is not valid"
                    % config.get_name())
        else:
            self.data_type = data_type
        self.index = config.getint("index", None, minval=0, maxval=0xff)
        self.value = None

    def parse(self, data):
        if self.data_type not in lib.TYPES or self.data_type.endswith("int8"):
            raise self.error("Cannot parse type '%s'" % self.data_type)
        try:
            return lib.unpack(data, self.data_type, strict=True)[0]
        except lib.error:
            raise self.error("Invalid data")

    def receive(self, data):
        self.value = self.parse(data)
        return self.value

class TC_DataInput(TouchControl):
    control_type = 0x00

    def __init__(self, config):
        super(TC_DataInput, self).__init__(config)

        if self.data_type.startswith("u"):
            raise config.error(
                "Option 'data_type' in section '%s' is not valid"
                % config.get_name())

    def parse(self, data):
        decimals = 3 # fixed in LCD resources
        value = super(TC_DataInput, self).parse(data)
        value = float(value) / 10 ** decimals
        return value

class TC_Slider(TouchControl):
    control_type = 0x03

    def __init__(self, config):
        super(TC_Slider, self).__init__(config, "uint16")

class TC_ReturnValue(TouchControl):
    control_type = 0x05

    def __init__(self, config):
        super(TC_ReturnValue, self).__init__(config, "uint16")

        self.map = {}
        for option in config.get_prefix_options("map_"):
            try:
                name = int(option[4:].strip())
                if name < 0 or name > 0xffff:
                    raise
            except:
                raise config.error("Option '%s' in section '%s' is not valid"
                                   % (option, config.get_name()))
            self.map[name] = config.get(option)

    def parse(self, data):
        value = super(TC_ReturnValue, self).parse(data)
        if len(self.map) > 0:
            try:
                value = self.map[value]
            except:
                raise self.error("Invalid data")
        return value

class TC_TextInput(TouchControl):
    control_type = 0x06

    def __init__(self, config):
        super(TC_TextInput, self).__init__(config, "string")

        length = config.getint("length", minval=1)
        self.length = [None, length]
        self.display = DC_Text(config)

    def parse(self, data):
        length = self.length[0]
        if length is None:
            length = self.length[1]
        data = data.split(bytearray([0x00, 0x00]), 1)[0]
        data = data.split(bytearray([0xff, 0xff]), 1)[0]
        data = data[:length]
        try:
            return codecs.decode(data, "ascii")
        except:
            raise self.error("Invalid data")

    def receive(self, data):
        value = super(TC_TextInput, self).receive(data)
        self.display.value = self.display.transform(value)
        return value

    def set_value(self, value, update=True):
        return self.display.set_value(value, update=update)

TOUCH_CONTROL_TYPES = {
    "data_input": TC_DataInput,
    "slider": TC_Slider,
    "return_value": TC_ReturnValue,
    "text_input": TC_TextInput
}

######################################################################
# Config helpers
######################################################################

def parse_display_controls(config):
    return [c.getchoice("type", DISPLAY_CONTROL_TYPES)(c)
            for c in config.get_prefix_sections("dgus_display_control ")]

def parse_touch_controls(config):
    return [c.getchoice("type", TOUCH_CONTROL_TYPES)(c)
            for c in config.get_prefix_sections("dgus_touch_control ")]
