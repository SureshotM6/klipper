# T5UID1 DGUSPrinterMenu implementation
#
# Copyright (C) 2021  Desuuuu <contact@desuuuu.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, re, ast, logging, threading
from . import menu as m_menu
from .. import lib, control as m_control, page as m_page

BOOT_TIME = 1.500
UPDATE_TIME = 1.000
UPDATE_MIN_TIME = 0.100

class DGUSPrinterMenu:
    def __init__(self, config, t5uid1):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_queue = []
        self.t5uid1 = t5uid1

        name_parts = config.get_name().split()
        if len(name_parts) > 2:
            raise config.error("Section name '%s' is not valid"
                               % config.get_name())
        self.display = "default"
        if len(name_parts) > 1:
            self.display = name_parts[1]

        self.message_timeout = config.getfloat("message_timeout", 30.,
                                               minval=5.,
                                               maxval=3600.)

        self.pages = {}
        self.menus = {}
        self.load_config(config)

        self.t5uid1.setup_shutdown_msg(self.build_shutdown_msg(config))

        self.gui_version = 0
        self.os_version = 0
        self.version_maj = 0
        self.version_min = 0
        self.version_pat = 0

        self.ready = False
        self.page = None
        self.menu = (None, {})

        self.pending_message = None
        self.update_request_pending = False
        self.update_time = 0.
        self.update_timer = self.reactor.register_timer(self.update_cb)
        self.message_timer = self.reactor.register_timer(self.message_cb)

        self.M117_registered = False
        self.M117_original = None
        self.M300_registered = False
        self.M300_original = None

        self.register_commands(self.display)
        if self.display == "default":
            self.register_commands(None)

        self.gcode.register_output_handler(self.output_cb)

        self.printer.register_event_handler("klippy:ready", self.handle_ready)
        self.printer.register_event_handler("klippy:disconnect",
                                            self.handle_disconnect)
        self.printer.register_event_handler("klippy:shutdown",
                                            self.handle_disconnect)
        self.printer.register_event_handler("gcode:request_restart",
                                            self.handle_restart)

    def load_config(self, config):
        pconfig = self.printer.lookup_object("configfile")
        control_file = os.path.join(os.path.dirname(__file__), "control.cfg")
        try:
            control_config = pconfig.read_config(control_file)
        except:
            raise config.error("Cannot load config '%s'" % control_file)
        display = m_control.parse_display_controls(control_config)
        touch = m_control.parse_touch_controls(control_config)

        page_file = os.path.join(os.path.dirname(__file__), "page.cfg")
        try:
            page_config = pconfig.read_config(page_file)
        except:
            raise config.error("Cannot load config '%s'" % page_file)
        self.pages = m_page.parse_pages(page_config, display, touch)

        menu_file = os.path.join(os.path.dirname(__file__), "menu.cfg")
        try:
            menu_config = pconfig.read_config(menu_file)
        except:
            raise config.error("Cannot load config '%s'" % menu_file)
        home_menu = m_menu.parse_home_menu(config, self, display=self.display)
        if home_menu is None:
            home_menu = m_menu.parse_home_menu(menu_config, self)
            if home_menu is None:
                raise config.error("Missing 'home' menu")
        self.menus = m_menu.parse_menus(config, self, display=self.display)
        if len(self.menus) < 1:
            self.menus = m_menu.parse_menus(menu_config, self)
        self.menus[home_menu.name] = home_menu
        self.update_main_menu()

    def update_main_menu(self):
        main_menu = None
        for name in self.menus.keys():
            if name == "home":
                continue
            if name == "main":
                main_menu = "main"
                break
            if main_menu is None:
                main_menu = name
        if main_menu is None:
            raise self.printer.config_error("Missing 'main' menu")
        self.main_menu = main_menu

    def build_shutdown_msg(self, config):
        dcs = self.pages["error"].display
        text = config.get("shutdown_text", "Printer is shutdown!")

        msg_line1 = dcs["line1"].set_value("", update=False)
        msg_line2 = dcs["line2"].set_value(text, update=False)
        msg_line3 = dcs["line3"].set_value("", update=False)
        msg_line4 = dcs["line4"].set_value("", update=False)

        msg_page = lib.set_page(3)
        msg_sound = lib.play_sound()

        return (msg_line1 + msg_line2 + msg_line3 + msg_line4
                + msg_page + msg_sound)

    def get_status(self, eventtime):
        gui_version = None
        os_version = None
        version = None
        if self.ready:
            gui_version = str(self.gui_version)
            os_version = str(self.os_version)
            version = "%d.%d.%d" % (self.version_maj, self.version_min,
                                    self.version_pat)
        return {
            "gui_version": gui_version,
            "os_version": os_version,
            "version": version
        }

    def update_cb(self, eventtime):
        if not self.ready:
            return self.reactor.NEVER
        if self.update_request_pending:
            self.update_request_pending = False
            self.update_time = eventtime + UPDATE_MIN_TIME
        menu, params = self.get_current_menu()
        if menu is not None:
            try:
                menu.update(**params)
            except Exception as e:
                logging.error("DPM: Error during '%s' menu update: %s"
                              % (menu.name, str(e)))
        return eventtime + UPDATE_TIME

    def request_update(self):
        if not self.ready or self.update_request_pending:
            return
        self.update_request_pending = True
        self.reactor.update_timer(self.update_timer, self.update_time)

    receive_lock = threading.Lock()
    def receive(self, address, data):
        logging.debug("DPM: Rx *%04x = %s", address, data.hex())
        if not self.receive_lock.acquire(False):
            return
        try:
            menu, params = self.get_current_menu()
            if menu is None or menu.page is None or menu.page not in self.pages:
                return
            for name, control in self.pages[menu.page].touch.items():
                if control.vp == address:
                    logging.debug("DPM: rx update %s : %s", menu.name, name)
                    try:
                        cdata = control.receive(data)
                    except control.error as e:
                        logging.error(
                            "DPM: Failed to parse data for control '%s': %s"
                            % (name, str(e)))
                        continue
                    try:
                        menu.receive(name, cdata, **params)
                    except Exception as e:
                        logging.error(
                            "DPM: Error during '%s' menu receive '%s': %s"
                            % (menu.name, name, str(e)))
        finally:
            self.receive_lock.release()

    def handle_ready(self):
        self.ready = False

        if not self.M117_registered:
            self.M117_original = self.gcode.register_command("M117", None)
            self.gcode.register_command("M117", self.cmd_M117)
            self.M117_registered = True

        if not self.M300_registered:
            self.M300_original = self.gcode.register_command("M300", None)
            self.gcode.register_command("M300", self.cmd_M300)
            self.M300_registered = True

        try:
            self.set_page("boot")
        except self.t5uid1.error as e:
            logging.warn("DPM: Initialization failed: %s", str(e))
            self.set_page("core_update", wait=False)
            self.t5uid1.play_sound()
            return

        self.os_version = self.t5uid1.get_version()

        self.t5uid1.play_sound(10)

        ready_at = self.reactor.monotonic() + BOOT_TIME
        self.reactor.register_callback(self.ready_cb, ready_at)

    def ready_cb(self, eventtime):
        self.ready = True
        try:
            for menu in reversed(self.menus.values()):
                menu.ready()
            if not self.set_menu("home"):
                raise Exception("Failed to switch to 'home' menu")
        except Exception as e:
            logging.error("DPM: Error during ready callback: %s" % str(e))
        self.pending_message = None
        self.reactor.update_timer(self.update_timer, eventtime + UPDATE_TIME)
        self.reactor.update_timer(self.message_timer, self.reactor.NOW)

    output_ignore_r = [
        re.compile(r"^ok(\s|$)"),
        re.compile(r"^action:.+"),
        re.compile(r"^(B|T\d+):\d+(\.\d+)?\s*\/\s*\d+(\.\d+)?")
    ]
    def output_cb(self, msg):
        if msg.startswith("echo:"):
            msg = msg[5:].strip()
        elif msg.startswith("//") or msg.startswith("!!"):
            msg = msg[2:].strip()
        else:
            msg = msg.strip()
        for r in self.output_ignore_r:
            if r.search(msg):
                return
        self.set_message(msg)

    def handle_restart(self, print_time):
        try:
            self.set_page("boot", wait=False)
        except Exception:
            pass
        self.handle_disconnect()

    def handle_disconnect(self):
        self.ready = False
        self.page = None
        self.menu = (None, {})
        self.pending_message = None
        self.reactor.update_timer(self.update_timer, self.reactor.NEVER)
        self.reactor.update_timer(self.message_timer, self.reactor.NEVER)

    def set_page(self, name, wait=True):
        if name not in self.pages:
            raise self.printer.command_error("Invalid page")
        if name == self.page:
            return
        self.t5uid1.set_page(self.pages[name].id, wait=wait)
        self.page = name

    def get_current_menu(self):
        if not self.ready:
            return (None, {})
        name, params = self.menu
        if name is None or name not in self.menus:
            return (None, {})
        return (self.menus[name], params)

    def set_menu(self, name, **kwargs):
        if not self.ready:
            raise self.printer.command_error("Screen is not ready")
        if name == "main":
            name = self.main_menu
        if name is None or name not in self.menus:
            raise self.printer.command_error("Invalid menu")
        params = {}
        params.update(**kwargs)
        target_menu = (name, params)
        current_menu = self.menu
        if target_menu == current_menu:
            return False
        self.menu = (None, {})
        menu = self.menus[name]
        if menu.page is not None and self.page == menu.page:
            self.set_page("blank")
        try:
            success = menu.setup(**params)
            if success:
                success = False
                menu.update(**params)
                success = True
        except Exception as e:
            success = False
            logging.error("DPM: Error during '%s' menu setup: %s"
                          % (name, str(e)))
        if self.menu[0] is not None:
            return True
        if not success or menu.page is None:
            if self.ready and self.page == "blank":
                old_name, old_params = current_menu
                if old_name is not None:
                    self.set_menu(old_name, **old_params)
                else:
                    self.set_menu("home")
            else:
                self.menu = current_menu
            return False
        self.set_page(menu.page)
        self.menu = target_menu
        return True

    def get_display_control(self, page_name, name):
        if page_name not in self.pages:
            raise self.printer.command_error("Invalid page")
        control = self.pages[page_name].display.get(name, None)
        if control is None:
            raise self.printer.command_error("Invalid control")
        return control.value

    def set_display_control(self, page_name, name, *args, **kwargs):
        if page_name not in self.pages:
            raise self.printer.command_error("Invalid page")
        control = self.pages[page_name].display.get(name, None)
        if control is None:
            raise self.printer.command_error("Invalid control")
        content = control.set_value(*args, **kwargs)
        if content is not None:
            self.t5uid1.write(content)

    def set_touch_control(self, page_name, name, *args, **kwargs):
        if page_name not in self.pages:
            raise self.printer.command_error("Invalid page")
        control = self.pages[page_name].touch.get(name, None)
        if control is None:
            raise self.printer.command_error("Invalid control")
        set_value = getattr(control, "set_value", None)
        if not callable(set_value):
            raise self.printer.command_error("'%s' cannot be set"
                                             % (type(control).__name__))
        content = set_value(*args, **kwargs)
        if content is not None:
            self.t5uid1.write(content)

    def set_message(self, message):
        if not self.ready:
            return
        self.pending_message = message
        time = self.reactor.monotonic() + 0.5
        self.reactor.update_timer(self.message_timer, time)

    message_lock = threading.Lock()
    def message_cb(self, eventtime):
        if not self.message_lock.acquire(False):
            return self.reactor.NEVER
        try:
            message = ""
            timeout = self.message_timeout
            if self.pending_message is not None:
                message = self.pending_message
                if len(message) > 32:
                    self.pending_message = message[32:]
                    message = message[0:32]
                    timeout = min(timeout, 10.)
                else:
                    self.pending_message = None
            try:
                self.set_display_control("global", "message", message)
            except Exception as e:
                logging.error("DPM: Error during message callback: %s" % str(e))
            if message:
                return eventtime + timeout
            return self.reactor.NEVER
        finally:
            self.message_lock.release()

    def get_menu_children(self, name):
        if name is None:
            return []
        children = []
        for menu in self.menus.values():
            if menu.name == name or menu.previous != name:
                continue
            children.append(menu)
        return children

    def queue_gcode(self, script):
        if not script:
            return
        if not self.gcode_queue:
            self.reactor.register_callback(self.dispatch_cb)
        self.gcode_queue.append(script)

    def dispatch_cb(self, eventtime):
        while self.gcode_queue:
            script = self.gcode_queue[0]
            try:
                self.gcode.run_script(script)
            except Exception as e:
                logging.error("DPM: Error during script execution")
            self.gcode_queue.pop(0)

    def cmd_M117(self, gcmd):
        msg = gcmd.get_commandline()
        umsg = msg.upper()
        if not umsg.startswith("M117"):
            start = umsg.find("M117")
            end = msg.rfind("*")
            if end >= 0:
                msg = msg[:end]
            msg = msg[start:]
        if len(msg) > 5:
            msg = msg[5:]
        else:
            msg = ""
        self.set_message(msg)
        if self.M117_original is not None:
            self.M117_original(gcmd)

    def _m300_sync_callback(self, print_time, duration):
        # previous command has finished computing expected print_time
        #FIXME: keep the beep going for durations over 2.55s
        # move that code into a lower layer though
        # self.reactor.register_timer(self._resend_m300, self.reactor.NOW)
        msremaining = int(duration * 1000)
        self.t5uid1.play_sound(min(msremaining, 2550), print_time)

    def cmd_M300(self, gcmd):
        duration = gcmd.get_int("P", 100) / 1000.

        # get this into the command queue so it doesn't play too early
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.register_lookahead_callback(
            lambda print_time: self._m300_sync_callback(print_time, duration))

        if self.M300_original is not None:
            self.M300_original(gcmd)

    def register_commands(self, display):
        self.t5uid1.register_base_commands(display)

        cmds = ["DGUS_REQUEST_UPDATE", "DGUS_SET_MENU", "DGUS_SET_MESSAGE"]
        gcode = self.printer.lookup_object("gcode")
        for cmd in cmds:
            gcode.register_mux_command(
                cmd, "DISPLAY", display, getattr(self, "cmd_" + cmd),
                desc=getattr(self, "cmd_" + cmd + "_help", None))

    cmd_DGUS_REQUEST_UPDATE_help = "Request the screen to be updated"
    def cmd_DGUS_REQUEST_UPDATE(self, gcmd):
        if not self.ready:
            raise gcmd.error("Screen is not ready")
        self.request_update()

    cmd_DGUS_SET_MENU_help = "Switch to a menu"
    def cmd_DGUS_SET_MENU(self, gcmd):
        if not self.ready:
            raise gcmd.error("Screen is not ready")
        menu = gcmd.get("MENU")
        if menu == "main":
            menu = self.main_menu
        if menu is None or menu not in self.menus:
            raise gcmd.error("Invalid MENU")
        params = {}
        for key, value in gcmd.get_command_parameters().items():
            if not key.startswith("PARAM_"):
                continue
            pname = key[6:].strip().lower()
            if not pname:
                continue
            try:
                literal = ast.literal_eval(value)
            except ValueError:
                raise gcmd.error("Unable to parse '%s' as a literal" % value)
            params[pname] = literal
        try:
            self.set_menu(menu, **params)
        except self.t5uid1.error as e:
            raise gcmd.error(str(e))

    cmd_DGUS_SET_MESSAGE_help = "Set a status message"
    def cmd_DGUS_SET_MESSAGE(self, gcmd):
        if not self.ready:
            raise gcmd.error("Screen is not ready")
        message = gcmd.get("MESSAGE")
        self.set_message(message)
