"""Press keys on the desktop. One backend per session type, picked by detect()."""
import ctypes
import itertools
import os
import shutil
import subprocess

ALIASES = {"super": "Super_L", "ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L"}


def parse_keys(spec):
    """'super+Page_Down' -> ['Super_L', 'Page_Down'] (X keysym names)."""
    return [ALIASES.get(k.strip().lower(), k.strip()) for k in spec.split("+")]


def keysym(name):
    xkb = ctypes.CDLL("libxkbcommon.so.0")
    xkb.xkb_keysym_from_name.restype = ctypes.c_uint32
    # exact case first ("Print"), then case-insensitive ("print")
    sym = xkb.xkb_keysym_from_name(name.encode(), 0) or xkb.xkb_keysym_from_name(name.encode(), 1)
    if not sym:
        raise ValueError(f"unknown key name: {name}")
    return sym


class Xdotool:
    name = "xdotool (X11)"

    def press(self, keys):
        subprocess.run(["xdotool", "key", "+".join(keys)], check=True)


class Wtype:
    name = "wtype (wlroots virtual keyboard)"

    def press(self, keys):
        args = [a for k in keys for a in ("-P", k)] + [a for k in reversed(keys) for a in ("-p", k)]
        subprocess.run(["wtype", *args], check=True)


class Portal:
    """xdg-desktop-portal RemoteDesktop (GNOME, KDE). Asks permission once, then reuses a restore token."""

    name = "xdg-desktop-portal RemoteDesktop"
    IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, token_file, timeout=60):
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        self.conn = open_dbus_connection(bus="SESSION")
        self.addr = DBusAddress("/org/freedesktop/portal/desktop", "org.freedesktop.portal.Desktop", self.IFACE)
        self.token_file, self.timeout = token_file, timeout
        self.tokens = itertools.count()
        self.session = None

    def available(self):
        from jeepney import Properties

        try:
            return self.conn.send_and_get_reply(Properties(self.addr).get("version")).body[0][1] >= 2
        except Exception:
            return False

    def _request(self, method, sig, *args, **opts):
        """Call a portal method and wait (bounded) for its Request.Response signal."""
        from jeepney import MatchRule, message_bus, new_method_call
        from jeepney.io.blocking import Proxy

        token = f"wavebind{next(self.tokens)}"
        path = f"/org/freedesktop/portal/desktop/request/{self.conn.unique_name[1:].replace('.', '_')}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        Proxy(message_bus, self.conn).AddMatch(rule)
        opts["handle_token"] = ("s", token)
        with self.conn.filter(rule) as queue:
            self.conn.send_and_get_reply(new_method_call(self.addr, method, sig, (*args, opts)))
            try:
                code, results = self.conn.recv_until_filtered(queue, timeout=self.timeout).body
            except TimeoutError:
                raise TimeoutError(f"no answer to the portal {method} within {self.timeout} s (permission dialog not clicked?)")
        if code != 0:
            raise PermissionError(f"portal {method} refused (response code {code})")
        return {k: v[1] for k, v in results.items()}

    def start(self):
        res = self._request("CreateSession", "a{sv}", session_handle_token=("s", "wavebind"))
        self.session = res["session_handle"]
        opts = {"types": ("u", 1), "persist_mode": ("u", 2)}  # 1 = keyboard, 2 = remember until revoked
        if self.token_file.exists():
            opts["restore_token"] = ("s", self.token_file.read_text().strip())
        self._request("SelectDevices", "oa{sv}", self.session, **opts)
        res = self._request("Start", "osa{sv}", self.session, "")
        if "restore_token" in res:  # tokens are single-use, save the fresh one every time
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(res["restore_token"])

    def press(self, keys):
        from jeepney import new_method_call

        if self.session is None:
            self.start()
        syms = [keysym(k) for k in keys]
        for state, order in ((1, syms), (0, reversed(syms))):
            for s in order:
                msg = new_method_call(self.addr, "NotifyKeyboardKeysym", "oa{sv}iu", (self.session, {}, s, state))
                self.conn.send_and_get_reply(msg)


def detect(token_file):
    """Return the best key backend for this session, or None."""
    if os.environ.get("XDG_SESSION_TYPE") == "x11" or not os.environ.get("WAYLAND_DISPLAY"):
        return Xdotool() if shutil.which("xdotool") else None
    portal = Portal(token_file)
    if portal.available():
        return portal
    return Wtype() if shutil.which("wtype") else None
