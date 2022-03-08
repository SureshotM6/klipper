# T5UID1 DGUSPrinterMenu menus
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import ast, math, logging, textwrap, threading
import jinja2
from collections import OrderedDict

BOOLEAN_STATES = {"1": True, "yes": True, "true": True, "on": True,
                  "0": False, "no": False, "false": False, "off": False}

class MenuError(Exception):
    pass

class OptionTemplate:
    def __init__(self, menu, config, option, default=None, parse_fn=str,
                 render_fn=None):
        printer = config.get_printer()
        self.gcode = printer.lookup_object("gcode")
        self.menu = menu
        self.is_static = True
        if default is None:
            script = config.get(option)
        else:
            script = config.get(option, default)
        if type(script) is not str:
            self.template = script
            return
        try:
            script = parse_fn(script.strip())
            success = True
        except Exception:
            success = False
        if type(script) is not str:
            self.template = script
            return
        if "{" not in script or "}" not in script:
            if not success:
                raise config.error("Option '%s' in section '%s' is not valid"
                                   % (option, config.get_name()))
            self.template = script
            return
        if render_fn is None:
            self.render_fn = parse_fn
        else:
            self.render_fn = render_fn
        self.is_static = False
        env = jinja2.Environment("{%", "%}", "{", "}",
                                 extensions=["jinja2.ext.do"])
        self.name = "%s:%s" % (config.get_name(), option)
        try:
            self.template = env.from_string(script)
        except Exception:
            raise config.error("Option '%s' in section '%s' is not valid"
                               % (option, config.get_name()))

    def render(self, context):
        if self.is_static:
            return self.template
        try:
            result = str(self.template.render(context)).strip()
        except Exception as e:
            logging.error("DPM: Error evaluating '%s': %s"
                          % (self.name, str(e)))
            raise e
        try:
            return self.render_fn(result)
        except Exception as e:
            logging.error("DPM: Error rendering '%s': %s"
                          % (self.name, str(e)))
            raise e

    def run_script(self, context):
        script = self.render(context)
        if type(script) is str:
            self.menu.run_script(script)

class Menu(object):
    error = MenuError

    def __init__(self, config, dpm, name=None, page=None):
        if type(self) is Menu:
            raise self.error("Abstract Menu cannot be instantiated directly")

        self.dpm = dpm
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.virtual_sdcard = None

        if name is None:
            name_parts = config.get_name().split()[1:]
            if any(":" in n for n in name_parts):
                raise config.error("Section name '%s' is not valid"
                                   % config.get_name())
            if len(name_parts) > 1:
                self.previous = ":".join(name_parts[:-1])
                self.name = ":".join(name_parts)
            else:
                self.previous = None
                self.name = name_parts[0]
        else:
            self.previous = None
            self.name = name

        page_data = None
        if page is not None:
            if page not in dpm.pages:
                raise config.error("Option 'page' in section '%s' is not valid"
                                   % config.get_name())
            page_data = dpm.pages[page]
        self.page = page

        self.base_context = {
            "get_brightness": self.get_brightness,
            "format_duration": self.format_duration,
            "wrap_text": self.wrap_text,
            "map_range": self.map_range,
            "parse_boolean": self.parse_boolean,
            "sd_filename": self.sd_filename
        }
        self.full_context = {
            "play_sound": self.play_sound,
            "stop_sound": self.stop_sound,
            "set_brightness": self.set_brightness,
            "request_update": self.request_update,
            "set_menu": self.set_menu,
            "go_back": self.go_back,
            "set_message": self.set_message,
            "set_title": self.set_title,
            "set_back_enabled": self.set_back_enabled
        }
        self.context = {}
        self.params = {}

        self.title = ""
        self.condition = True
        self.back_enabled = True

        self.has_title = False
        if page_data is not None and "title" in page_data.display:
            self.has_title = True
        self.has_back = False
        if page_data is not None and "back_icon" in page_data.display:
            self.has_back = True

        for option in config.get_prefix_options("param_"):
            pname = option[6:].strip()
            if (len(pname) < 1 or pname in ["name", "data", "silent"]
                or pname in self.params):
                raise config.error("Option '%s' in section '%s' is not valid"
                                   % (option, config.get_name()))
            try:
                self.params[pname] = ast.literal_eval(config.get(option))
            except ValueError:
                raise config.error(
                    "Option '%s' in section '%s' is not a valid literal"
                    % (option, config.get_name()))

        if not config.get_name().startswith("dgus_menu "):
            self.condition_tmpl = None
            self.enable_tmpl = None
            self.setup_tmpl = None
            self.update_tmpl = None
            return

        self.title = config.get("title")
        self.back_enabled = config.getboolean("back", True)
        self.condition_tmpl = self.template_boolean(config, "condition", None)
        self.enable_tmpl = self.template_boolean(config, "enable", None)
        self.setup_tmpl = self.template(config, "setup", None)
        self.update_tmpl = self.template(config, "update", None)

    def ready(self):
        if self.condition_tmpl is None:
            self.condition = True
        else:
            self.update_context()
            context = self.get_context(full=False)
            self.condition = self.condition_tmpl.render(context)
        self.virtual_sdcard = self.printer.lookup_object("virtual_sdcard", None)

    def run_script(self, script):
        wait = False
        script = script.strip()
        lines = script.split("\n")
        if len(lines) > 0 and lines[0].strip() == "DGUS_WAIT":
            wait = True
            script = "\n".join(lines[1:])
        if not script:
            return
        if wait:
            self.gcode.run_script(script)
        else:
            self.dpm.queue_gcode(script)

    def template(self, config, option, default=None, parse_fn=str,
                 render_fn=None):
        if default is None and config.get(option, None) is None:
            return None
        return OptionTemplate(self, config, option, default,
                              parse_fn=parse_fn, render_fn=render_fn)

    def template_int(self, config, option, default=None):
        return self.template(config, option, default, parse_fn=int)

    def template_float(self, config, option, default=None):
        return self.template(config, option, default, parse_fn=float)

    def template_boolean(self, config, option, default=None):
        return self.template(config, option, default,
                             parse_fn=self.parse_boolean,
                             render_fn=(lambda v: bool(ast.literal_eval(v))))

    def parse_boolean(self, value):
        if value.lower() not in BOOLEAN_STATES:
            raise ValueError("Not a boolean: %s" % value)
        return BOOLEAN_STATES[value.lower()]

    def get_context(self, params=None, full=True):
        context = dict(self.context)
        if full:
            context.update(self.full_context)
        if params is not None:
            context["params"] = params
        return context

    def update_context(self):
        self.context = self.gcode_macro.create_template_context()
        self.context.update(self.base_context)

    def play_sound(self, slen=100):
        self.dpm.t5uid1.play_sound(slen)

    def stop_sound(self):
        self.dpm.t5uid1.stop_sound()

    def get_brightness(self):
        return self.dpm.t5uid1.get_brightness()

    def set_brightness(self, brightness, save=False):
        self.dpm.t5uid1.set_brightness(brightness, save=save)

    def request_update(self):
        self.dpm.request_update()

    def set_menu(self, name, silent=False, **kwargs):
        success = self.dpm.set_menu(name, **kwargs)
        if success and not silent:
            self.play_sound()
        return success

    def go_back(self, force=False, silent=False):
        if self.name == "home":
            return False
        if not force:
            if self.has_back and not self.get_display_control("back_icon"):
                return False
        if self.previous is not None:
            if self.set_menu(self.previous, silent=silent):
                return True
        return self.set_menu("home", silent=silent)

    def set_message(self, message):
        self.dpm.set_message(message)

    def format_duration(self, seconds):
        if type(seconds) is not int:
            seconds = int(seconds)
        if seconds < 0:
            seconds = 0
        minutes = seconds / 60
        hours = minutes / 60
        days = hours / 24
        days %= 365
        hours %= 24
        minutes %= 60
        seconds %= 60
        if days <= 0:
            days = None
            if hours <= 0:
                hours = None
                if minutes <= 0:
                    minutes = None
        if hours is not None:
            seconds = None
        result = ""
        if seconds is not None:
            result = str(seconds) + "s " + result
        if minutes is not None:
            result = str(minutes) + "m " + result
        if hours is not None:
            result = str(hours) + "h " + result
        if days is not None:
            result = str(days) + "d " + result
        return result.strip()

    def wrap_text(self, text, len, max=None):
        lines = textwrap.wrap(text.strip(), len)
        if max is not None:
            lines = lines[:max]
        return [c.strip() for c in lines]

    def map_range(self, value, imin, imax, omin, omax):
        result = (value - imin) * (omax - omin) / (imax - imin) + omin
        if type(value) is int:
            result = int(round(result))
        return max(omin, min(result, omax))

    def sd_filename(self, path):
        if self.virtual_sdcard is None:
            return path
        prefix = self.virtual_sdcard.sdcard_dirname + "/"
        if not path.startswith(prefix):
            return path
        return path[len(prefix):]

    def set_title(self, title):
        if not self.has_title:
            return
        self.set_display_control("title", title)

    def set_back_enabled(self, state):
        if not self.has_back:
            return
        self.set_display_control("back_icon", state)

    def get_display_control(self, name):
        if self.page is None:
            raise self.error("No page")
        return self.dpm.get_display_control(self.page, name)

    def set_display_control(self, name, *args, **kwargs):
        if self.page is None:
            raise self.error("No page")
        self.dpm.set_display_control(self.page, name, *args, **kwargs)

    def set_touch_control(self, name, *args, **kwargs):
        if self.page is None:
            raise self.error("No page")
        self.dpm.set_touch_control(self.page, name, *args, **kwargs)

    def get_children(self):
        return self.dpm.get_menu_children(self.name)

    def is_enabled(self, params):
        if not self.condition:
            return False
        self.update_context()
        if self.enable_tmpl is not None:
            return self.enable_tmpl.render(self.get_context(params, False))
        return True

    def setup(self, **kwargs):
        params = dict(self.params)
        params.update(**kwargs)
        if len(params) != len(self.params):
            raise self.error("Invalid parameter provided")
        if not self.is_enabled(params):
            self.go_back(force=True, silent=True)
            return False
        return self.run_setup(params)

    def abort_setup(self, reason=""):
        if type(reason) is str:
            self.abort_reason = reason

    def run_setup(self, params):
        if self.has_title:
            self.set_title(self.title)
        if self.has_back:
            self.set_back_enabled(self.back_enabled)
        if self.setup_tmpl is not None:
            self.abort_reason = None
            context = self.get_context(params)
            context["abort_setup"] = self.abort_setup
            self.setup_tmpl.run_script(context)
            if self.abort_reason is not None:
                if self.abort_reason:
                    self.set_message(self.abort_reason)
                self.abort_reason = None
                return False
        return True

    def update(self, **kwargs):
        params = dict(self.params)
        params.update(**kwargs)
        if len(params) != len(self.params):
            raise self.error("Invalid parameter provided")
        if not self.is_enabled(params):
            self.go_back(force=True, silent=True)
            return
        self.run_update(params)

    def run_update(self, params):
        if self.update_tmpl is not None:
            self.update_tmpl.run_script(self.get_context(params))

    def receive(self, name, data, **kwargs):
        params = dict(self.params)
        params.update(**kwargs)
        if len(params) != len(self.params):
            raise self.error("Invalid parameter provided")
        handled = self.run_receive(name, data, params)
        if not handled and name == "back":
            self.go_back()

    def run_receive(self, name, data, params):
        return False

class MenuHome(Menu):
    def __init__(self, config, dpm):
        super(MenuHome, self).__init__(config, dpm, name="home", page="home")

        self.reactor = self.printer.get_reactor()
        self.dgus_status = self.printer.lookup_object("dgus_status")
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        self.toolhead = None
        self.fan = None
        self.heaters = {}

        self.title = "Home"
        self.params.setdefault("e1", "extruder")
        self.params.setdefault("e2", "extruder1")
        self.params.setdefault("bed", "heater_bed")
        self.params.setdefault("fan", "fan")

    def ready(self):
        super(MenuHome, self).ready()
        self.toolhead = self.printer.lookup_object("toolhead")

    def refresh_heaters(self, params):
        self.heaters = {}
        pheaters = self.printer.lookup_object("heaters", None)
        if pheaters is None:
            return
        for name in ["e1", "e2", "bed"]:
            try:
                self.heaters[name] = pheaters.lookup_heater(params[name])
            except:
                pass

    def update_heaters(self, eventtime):
        icons = []
        for name in ["e1", "e2", "bed"]:
            if name in self.heaters:
                icons.append(name)
                temp = self.heaters[name].get_temp(eventtime)
                current = "%.0f" % temp[0]
                if temp[1] > 0:
                    target = "%.0f" % temp[1]
                else:
                    target = ""
            else:
                target = ""
                current = ""
            self.set_display_control(name + "_target", target)
            self.set_display_control(name + "_current", current)
        return icons

    def update_fan(self, eventtime):
        icons = []
        if self.fan is not None:
            fan = self.fan.get_status(eventtime)
        else:
            fan = {}
        if "speed" in fan:
            icons.append("fan")
            self.set_display_control("fan", "{:.0%}".format(fan["speed"]))
        else:
            self.set_display_control("fan", "")
        return icons

    def update_factors(self, eventtime):
        gcode_move = self.gcode_move.get_status(eventtime)
        feed = gcode_move["speed_factor"]
        flow = gcode_move["extrude_factor"]
        self.set_display_control("feed", "{:.0%}".format(feed))
        self.set_display_control("flow", "{:.0%}".format(flow))
        return ["feed", "flow"]

    def update_axes(self, eventtime):
        axes = ["x", "y", "z"]
        toolhead = self.toolhead.get_status(eventtime)
        gcode_move = self.gcode_move.get_status(eventtime)
        for name in axes:
            if (name in toolhead["homed_axes"]):
                position = getattr(gcode_move["gcode_position"], name, None)
                if position is not None:
                    self.set_display_control(name, "%.2f" % position)
                else:
                    self.set_display_control(name, "??")
            else:
                self.set_display_control(name, "??")
        return axes

    def update_status(self, eventtime):
        icons = []
        dgus_status = self.dgus_status.get_status(eventtime)

        progress = dgus_status["progress"]
        if progress is not None:
            self.set_display_control("progress", int(round(progress * 100.)))
            self.set_display_control("progress_text", "{:.0%}".format(progress))
        else:
            self.set_display_control("progress", "none")
            self.set_display_control("progress_text", "")

        filename = dgus_status["filename"]
        if filename is not None:
            icons.append("filename")
            self.set_display_control("filename", filename)
        else:
            self.set_display_control("filename", "")

        printing_time = dgus_status["printing_time"]
        if printing_time is not None:
            icons.append("ellapsed")
            self.set_display_control("ellapsed",
                                     self.format_duration(printing_time))
        else:
            self.set_display_control("ellapsed", "")

        remaining_time = dgus_status["remaining_time"]
        if remaining_time is not None:
            icons.append("left")
            self.set_display_control("left",
                                     self.format_duration(remaining_time))
        else:
            self.set_display_control("left", "")

        return icons

    def run_setup(self, params):
        if not super(MenuHome, self).run_setup(params):
            return False
        self.fan = self.printer.lookup_object(params["fan"], None)
        self.refresh_heaters(params)
        return True

    def run_update(self, params):
        icons = []
        eventtime = self.reactor.monotonic()
        icons += self.update_heaters(eventtime)
        icons += self.update_fan(eventtime)
        icons += self.update_factors(eventtime)
        icons += self.update_axes(eventtime)
        icons += self.update_status(eventtime)
        self.set_display_control("icons", icons)
        super(MenuHome, self).run_update(params)

    def run_receive(self, name, data, params):
        if name != "menu":
            return False
        self.set_menu("main")
        return True

class MenuList(Menu):
    page_size = 5

    def __init__(self, config, dpm):
        super(MenuList, self).__init__(config, dpm, page="menu")
        self.params["page"] = 0
        self.buttons = []

    def ready(self):
        super(MenuList, self).ready()
        self.buttons = []
        for child in self.get_children():
            if not child.condition:
                continue
            self.buttons.append({
                "text": child.title,
                "enable": (lambda c=child: c.is_enabled(c.params)),
                "action": (lambda s=self, c=child: s.set_menu(c.name))
            })

    def is_button_enabled(self, button):
        func = button["enable"]
        try:
            return bool(func())
        except Exception as e:
            logging.error("DPM: Button error: %s" % str(e))
            return False

    def run_button_action(self, button):
        func = button["action"]
        try:
            func()
        except Exception as e:
            logging.error("DPM: Button action error: %s" % str(e))
            pass

    def enabled_buttons(self):
        return [b for b in self.buttons if self.is_button_enabled(b)]

    def get_page(self, count, page):
        max_page = max(0, (count - 1) // self.page_size)
        real_page = max(0, min(max_page, int(page)))
        return (real_page, (real_page < max_page))

    def update_buttons(self, params):
        enabled = self.enabled_buttons()
        page, has_next = self.get_page(len(enabled), params["page"])
        if page != params["page"]:
            new_params = dict(params)
            new_params["page"] = page
            self.set_menu(self.name, silent=True, **new_params)
            return False
        start = page * self.page_size
        current = enabled[start:start + self.page_size]
        i = 0
        buttons = []
        while i < len(current):
            self.set_display_control("button%d_text" % (i + 1),
                                     current[i]["text"])
            buttons.append("button%d" % (i + 1))
            i += 1
        while i < self.page_size:
            self.set_display_control("button%d_text" % (i + 1), "")
            i += 1
        self.set_display_control("buttons", buttons)
        arrows = []
        if page > 0:
            arrows.append("up")
        if has_next:
            arrows.append("down")
        self.set_display_control("arrows", arrows)
        return True

    def is_enabled(self, params):
        if not super(MenuList, self).is_enabled(params):
            return False
        return len(self.buttons) > 0

    def run_update(self, params):
        self.update_buttons(params)
        super(MenuList, self).run_update(params)

    def handle_button(self, name, params):
        enabled = self.enabled_buttons()
        page, has_next = self.get_page(len(enabled), params["page"])
        if page != params["page"]:
            new_params = dict(params)
            new_params["page"] = page
            self.set_menu(self.name, silent=True, **new_params)
            return True
        if name.startswith("button"):
            i = int(name[6:]) - 1
            start = page * self.page_size
            current = enabled[start:start + self.page_size]
            if i >= 0 and i < len(current):
                self.run_button_action(current[i])
            return True
        if name == "arrow_up":
            if page <= 0:
                return True
            new_params = dict(params)
            new_params["page"] = page - 1
            self.set_menu(self.name, **new_params)
            return True
        if name == "arrow_down":
            if not has_next:
                return True
            new_params = dict(params)
            new_params["page"] = page + 1
            self.set_menu(self.name, **new_params)
            return True
        return False

    def run_receive(self, name, data, params):
        if name == "button":
            if self.handle_button(data, params):
                return True
        return False

class MenuVSDList(MenuList):
    def __init__(self, config, dpm):
        super(MenuVSDList, self).__init__(config, dpm)

        self.reactor = self.printer.get_reactor()
        self.dgus_status = self.printer.lookup_object("dgus_status")

        self.file_buttons = []

    def is_enabled(self, params):
        if self.virtual_sdcard is None:
            return False
        if not super(MenuList, self).is_enabled(params):
            return False
        return True

    def enabled_buttons(self):
        buttons = self.buttons + self.file_buttons
        return [b for b in buttons if self.is_button_enabled(b)]

    def is_idle(self):
        eventtime = self.reactor.monotonic()
        dgus_status = self.dgus_status.get_status(eventtime)
        return dgus_status["state"] == "idle"

    def print_file(self, filename):
        self.run_script("M23 %s\nDGUS_REQUEST_UPDATE" % filename)
        self.play_sound()

    def run_setup(self, params):
        if self.virtual_sdcard is None:
            return False
        if not super(MenuVSDList, self).run_setup(params):
            return False
        self.file_buttons = []
        for name, _ in self.virtual_sdcard.get_file_list():
            self.file_buttons.append({
                "text": name,
                "enable": (lambda s=self: s.is_idle()),
                "action": (lambda s=self, n=name: s.print_file(n))
            })
        return True

class MenuText(Menu):
    def __init__(self, config, dpm):
        self.button_text = config.get("button", None)
        self.button_action_tmpl = self.template(config, "button_action", None)

        page = "text"
        if self.button_text is not None:
            page += "_button"
        super(MenuText, self).__init__(config, dpm, page=page)

        self.text = self.template(config, "text", None)
        if self.text is None:
            self.lines = []
            self.lines.append(self.template(config, "line1", None))
            self.lines.append(self.template(config, "line2", None))
            self.lines.append(self.template(config, "line3", None))
            self.lines.append(self.template(config, "line4", None))
            self.has_lines = any(l is not None for l in self.lines)
        else:
            self.has_lines = False

    def update_text(self, params):
        context = self.get_context(params, False)
        if self.has_lines:
            lines = []
            for tmpl in self.lines:
                if tmpl is None:
                    lines.append("")
                else:
                    lines.append(tmpl.render(context))
        else:
            if self.text is None:
                text = ""
            else:
                text = self.text.render(context)
            lines = self.wrap_text(text, 32, 4)
            if len(lines) < 3:
                lines.insert(0, "")
            while len(lines) < 4:
                lines.append("")
        for i, text in enumerate(lines):
            self.set_display_control("line%d" % (i + 1), text)

    def run_setup(self, params):
        if not super(MenuText, self).run_setup(params):
            return False
        if self.button_text is not None:
            self.set_display_control("button_text", self.button_text)
        return True

    def run_update(self, params):
        self.update_text(params)
        super(MenuText, self).run_update(params)

    def handle_button(self, name, params):
        if name != "confirm":
            return False
        if self.button_action_tmpl is not None:
            self.button_action_tmpl.run_script(self.get_context(params))
        self.play_sound()
        return True

    def run_receive(self, name, data, params):
        if name == "button":
            if self.handle_button(data, params):
                return True
        return False

class MenuNumberInput(Menu):
    max_digits = 12
    variations = ["step", "steps", "slider"]

    def __init__(self, config, dpm):
        self.variation = config.get("variation", None)
        if self.variation is not None and self.variation not in self.variations:
            raise config.error(
                "Option 'variation' in section '%s' is not valid"
                % config.get_name())
        self.button_text = config.get("button", None)
        self.button_action_tmpl = self.template(config, "button_action", None)

        page = "num_input"
        if self.variation is not None:
            page += "_" + self.variation
        if self.button_text is not None:
            page += "_button"
        super(MenuNumberInput, self).__init__(config, dpm, page=page)
        self.reactor = self.printer.get_reactor()

        self.base_context.update({
            "get_field_value": self.get_field_value
        })
        self.full_context.update({
            "set_field_value": self.set_field_value
        })

        self.field_title = config.get("field_title", "")
        self.field_unit = config.get("field_unit", "")
        self.decimals = config.getint("decimals", 0, minval=0, maxval=5)
        self.digits = self.max_digits - self.decimals
        self.min_value = None
        self.max_value = None
        self.min_tmpl = self.template_float(config, "min", None)
        self.max_tmpl = self.template_float(config, "max", None)
        if self.variation == "step":
            self.step = config.getfloat("step", above=0.)
        elif self.variation == "steps":
            self.steps = config.getlist("steps")
            if len(self.steps) > 3:
                raise config.error(
                    "Option 'steps' in section '%s' must have at most 3"
                    " elements"
                    % config.get_name())
            for step in self.steps:
                try:
                    if float(step) <= 0.:
                        raise
                except ValueError:
                    raise config.error(
                        "Option 'steps' in section '%s' is not valid"
                        % config.get_name())
            self.default_step = config.getint("default_step", 1, minval=1,
                                              maxval=3) - 1
            if self.default_step >= len(self.steps):
                raise config.error("Option 'step' in section '%s' is not valid"
                                   % config.get_name())
            self.current_step = self.default_step
        elif self.variation == "slider":
            if self.min_tmpl is None:
                raise config.error(
                    "Option 'min' in section '%s' must be specified"
                    % config.get_name())
            if self.max_tmpl is None:
                raise config.error(
                    "Option 'max' in section '%s' must be specified"
                    % config.get_name())
            self.slider_timer = self.reactor.register_timer(self.slider_cb)
        self.default_tmpl = self.template_float(config, "default", 0.)
        if self.decimals < 1:
            self.input_value = 0
        else:
            self.input_value = 0.
        self.input_action_tmpl = self.template(config, "input_action", None)

    def get_field_value(self):
        return self.input_value

    def set_field_value(self, value):
        if self.min_value is None or self.max_value is None:
            return
        value = max(self.min_value, min(value, self.max_value))
        value = round(value, self.decimals)
        if self.decimals < 1:
            value = int(value)
        self.input_value = value
        pattern = "%%.%df" % self.decimals
        self.set_display_control("field_value", pattern % value)
        if self.variation == "slider":
            self.reactor.update_timer(self.slider_timer, self.reactor.NEVER)
            slider = self.map_range(value, self.min_value, self.max_value,
                                    0, 100)
            self.set_display_control("slider", slider)

    def update_steps(self):
        if self.min_value is None or self.max_value is None:
            return
        if self.variation == "steps":
            for i in range(0, 3):
                if i >= len(self.steps):
                    self.set_display_control("step%d_text" % (i + 1), "")
                    self.set_display_control("step%d" % (i + 1), "none")
                    continue
                self.set_display_control("step%d_text" % (i + 1), self.steps[i])
                if i == self.current_step:
                    self.set_display_control("step%d" % (i + 1), "on")
                else:
                    self.set_display_control("step%d" % (i + 1), "off")
        elif self.variation != "step":
            return
        arrows = []
        if self.input_value < self.max_value:
            arrows.append("up")
        if self.input_value > self.min_value:
            arrows.append("down")
        self.set_display_control("arrows", arrows)

    def initialize_defaults(self, params):
        context = self.get_context(params, False)
        digits = self.max_digits - self.decimals
        max_abs = 10 ** digits - 10 ** -self.decimals
        min_abs = -max_abs
        if self.min_tmpl is None:
            self.min_value = min_abs
            min_digits = digits
        else:
            self.min_value = max(min_abs, self.min_tmpl.render(context))
            min_digits = len(str(abs(int(math.modf(self.min_value)[1]))))
        if self.max_tmpl is None:
            self.max_value = max_abs
            max_digits = digits
        else:
            self.max_value = min(max_abs, self.max_tmpl.render(context))
            max_digits = len(str(abs(int(math.modf(self.max_value)[1]))))
        self.digits = max(1, min(digits, max(min_digits, max_digits)))
        if self.min_value >= self.max_value:
            raise self.error("Invalid min/max")
        if self.variation == "steps":
            self.current_step = self.default_step
        self.set_field_value(self.default_tmpl.render(context))

    def run_setup(self, params):
        if not super(MenuNumberInput, self).run_setup(params):
            return False
        self.initialize_defaults(params)
        self.set_display_control("field_title", self.field_title)
        self.set_display_control("field_unit", self.field_unit)
        if self.button_text is not None:
            self.set_display_control("button_text", self.button_text)
        self.update_steps()
        return True

    def handle_input(self, data, params):
        self.set_field_value(data)
        if self.input_action_tmpl is not None:
            self.input_action_tmpl.run_script(self.get_context(params))
        return True

    def handle_slider(self, data, params):
        if self.variation != "slider":
            return False
        if self.min_value is None or self.max_value is None:
            return False
        self.slider_value = self.map_range(data, 0, 100,
                                            self.min_value, self.max_value)
        self.slider_params = params
        update_at = self.reactor.monotonic() + 0.100
        self.reactor.update_timer(self.slider_timer, update_at)
        return True

    slider_lock = threading.Lock()
    def slider_cb(self, eventtime):
        if not self.slider_lock.acquire(False):
            return self.reactor.NEVER
        try:
            if self.slider_value is not None and self.slider_params is not None:
                try:
                    self.handle_input(self.slider_value, self.slider_params)
                    self.play_sound(2)
                except Exception as e:
                    logging.error("DPM: Error in menu '%s' slider callback: %s"
                                % (self.name, str(e)))
                self.slider_value = None
                self.slider_params = None
            return self.reactor.NEVER
        finally:
            self.slider_lock.release()

    def handle_button(self, name, params):
        if name == "arrow_up":
            if self.variation == "step":
                step = self.step
            elif self.variation == "steps":
                step = float(self.steps[self.current_step])
            else:
                return False
            self.handle_input(self.input_value + step, params)
            self.play_sound(2)
            self.update_steps()
            return True
        if name == "arrow_down":
            if self.variation == "step":
                step = self.step
            elif self.variation == "steps":
                step = float(self.steps[self.current_step])
            else:
                return False
            self.handle_input(self.input_value - step, params)
            self.play_sound(2)
            self.update_steps()
            return True
        if name.startswith("step"):
            if self.variation != "steps":
                return False
            i = int(name[4:]) - 1
            if i != self.current_step and i < len(self.steps):
                self.current_step = i
                self.play_sound(2)
            self.update_steps()
            return True
        if name == "confirm":
            if self.button_action_tmpl is not None:
                self.button_action_tmpl.run_script(self.get_context(params))
            self.play_sound(2)
            return True
        return False

    def run_receive(self, name, data, params):
        if name == "input":
            if self.handle_input(data, params):
                return True
        elif name == "slider":
            if self.handle_slider(data, params):
                return True
        elif name == "button":
            if self.handle_button(data, params):
                return True
        return False

class MenuTextInput(Menu):
    def __init__(self, config, dpm):
        self.button_text = config.get("button", None)
        self.button_action_tmpl = self.template(config, "button_action", None)

        page = "text_input"
        if self.button_text is not None:
            page += "_button"
        super(MenuTextInput, self).__init__(config, dpm, page=page)

        self.base_context.update({
            "get_field_value": self.get_field_value
        })
        self.full_context.update({
            "set_field_value": self.set_field_value
        })

        self.field_title = config.get("field_title", "")
        self.max_length_tmpl = self.template_int(config, "max_length", 32)
        self.max_length = 32
        self.clear_input = config.getboolean("clear_input", False)
        self.default_tmpl = self.template(config, "default", "")
        self.input_value = ""
        self.input_action_tmpl = self.template(config, "input_action", None)

    def get_field_value(self):
        return self.input_value

    def set_field_value(self, text, input_text=None):
        if text is not None:
            self.input_value = text[:self.max_length]
            self.set_display_control("field_value", self.input_value)
        if input_text is not None:
            self.set_touch_control("input", input_text[:self.max_length])

    def initialize_defaults(self, params):
        context = self.get_context(params, False)
        self.max_length = self.max_length_tmpl.render(context)
        if self.max_length < 1 or self.max_length > 32:
            raise self.error("Invalid max_length")
        default_value = self.default_tmpl.render(context)
        if self.clear_input:
            input_text = ""
        else:
            input_text = default_value
        self.set_field_value(default_value, input_text)

    def run_setup(self, params):
        if not super(MenuTextInput, self).run_setup(params):
            return False
        self.initialize_defaults(params)
        self.set_display_control("field_title", self.field_title)
        if self.button_text is not None:
            self.set_display_control("button_text", self.button_text)
        return True

    def handle_input(self, data, params):
        if self.clear_input:
            input_text = ""
        else:
            input_text = data
        self.set_field_value(data, input_text)
        if self.input_action_tmpl is not None:
            self.input_action_tmpl.run_script(self.get_context(params))
        return True

    def handle_button(self, name, params):
        if name != "confirm":
            return False
        if self.button_action_tmpl is not None:
            self.button_action_tmpl.run_script(self.get_context(params))
        self.play_sound(2)
        return True

    def run_receive(self, name, data, params):
        if name == "input":
            if self.handle_input(data, params):
                return True
        elif name == "button":
            if self.handle_button(data, params):
                return True
        return False

class MenuCommand(Menu):
    def __init__(self, config, dpm):
        super(MenuCommand, self).__init__(config, dpm)

        self.action_tmpl = self.template(config, "action")

    def run_setup(self, params):
        if not super(MenuCommand, self).run_setup(params):
            return False
        self.action_tmpl.run_script(self.get_context(params))
        self.play_sound(2)
        return False

MENU_TYPES = {
    "list": MenuList,
    "vsdlist": MenuVSDList,
    "text": MenuText,
    "number_input": MenuNumberInput,
    "text_input": MenuTextInput,
    "command": MenuCommand
}

def parse_home_menu(config, dpm, display=None):
    configs = [c for c in config.get_prefix_sections("dgus_home ")]
    if config.has_section("dgus_home"):
        configs.append(config.getsection("dgus_home"))
    home_config = None
    for c in configs:
        if display is not None:
            displays = c.getlist("display", ("default",))
            if display not in displays:
                continue
        if home_config is not None:
            raise config.error("Multiple 'home' menus defined")
        home_config = c
    if home_config is None:
        return None
    return MenuHome(home_config, dpm)

def parse_menus(config, dpm, display=None):
    menus = OrderedDict()
    for c in config.get_prefix_sections("dgus_menu "):
        if display is not None:
            displays = c.getlist("display", ("default",))
            if display not in displays:
                continue
        menu = c.getchoice("type", MENU_TYPES)(c, dpm)
        if menu.name == "home":
            raise config.error("Menu name 'home' is reserved")
        if menu.name in menus:
            raise config.error("Menu '%s' is already defined"
                               % menu.name)
        menus[menu.name] = menu
    return menus
