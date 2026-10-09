"""Run gesture actions: shell commands, media control (MPRIS), key presses."""
import ctypes
import itertools
import os
import shlex
import shutil
import subprocess

ALIASES = {"super": "Super_L", "ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L"}
BTN_LEFT = 0x110  # linux/input-event-codes.h


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

    def drag(self, keys, down):
        """Hold keys + left button (down=True), or release them."""
        if down:
            args = [a for k in keys for a in ("keydown", k)] + ["mousedown", "1"]
        else:
            args = ["mouseup", "1"] + [a for k in reversed(keys) for a in ("keyup", k)]
        subprocess.run(["xdotool", *args], check=True)

    def move(self, dx, dy):
        subprocess.run(["xdotool", "mousemove_relative", "--", str(round(dx)), str(round(dy))], check=True)


class Wtype:
    name = "wtype (wlroots virtual keyboard)"

    def press(self, keys):
        args = [a for k in keys for a in ("-P", k)] + [a for k in reversed(keys) for a in ("-p", k)]
        subprocess.run(["wtype", *args], check=True)

    def drag(self, keys, down):
        raise RuntimeError("pinch-drag needs the pointer: GNOME/KDE (portal) or X11 (xdotool), wtype has no mouse")

    move = drag


class Portal:
    """xdg-desktop-portal RemoteDesktop (GNOME, KDE). Keyboard only unless pointer=True (pinch-drag).

    remember=False: GNOME/KDE asks every launch, nothing is stored.
    remember=True: the grant is saved as a restore token (mode 0600) and reused until revoked.
    """

    name = "xdg-desktop-portal RemoteDesktop"
    IFACE = "org.freedesktop.portal.RemoteDesktop"

    def __init__(self, token_file, remember=False, pointer=False, timeout=60):
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection

        self.conn = open_dbus_connection(bus="SESSION")
        self.addr = DBusAddress("/org/freedesktop/portal/desktop", "org.freedesktop.portal.Desktop", self.IFACE)
        self.token_file, self.remember, self.pointer, self.timeout = token_file, remember, pointer, timeout
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
        types = 1 | (2 if self.pointer else 0)  # 1 = keyboard, 2 = pointer
        opts = {"types": ("u", types), "persist_mode": ("u", 2 if self.remember else 0)}
        if not self.remember:
            self.token_file.unlink(missing_ok=True)
        elif self.token_file.exists():
            opts["restore_token"] = ("s", self.token_file.read_text().strip())
        self._request("SelectDevices", "oa{sv}", self.session, **opts)
        res = self._request("Start", "osa{sv}", self.session, "")
        if self.remember and "restore_token" in res:  # tokens are single-use, save the fresh one every time
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.touch(mode=0o600)
            self.token_file.chmod(0o600)  # touch() leaves an existing file's mode alone
            self.token_file.write_text(res["restore_token"])

    def _notify(self, method, sig, *args):
        from jeepney import new_method_call

        if self.session is None:
            self.start()
        self.conn.send_and_get_reply(new_method_call(self.addr, method, "oa{sv}" + sig, (self.session, {}, *args)))

    def _keys(self, syms, state):
        for s in syms:
            self._notify("NotifyKeyboardKeysym", "iu", s, state)

    def press(self, keys):
        syms = [keysym(k) for k in keys]
        self._keys(syms, 1)
        self._keys(reversed(syms), 0)

    def drag(self, keys, down):
        """Hold keys + left button (down=True), or release them."""
        syms = [keysym(k) for k in keys]
        if down:
            self._keys(syms, 1)
            self._notify("NotifyPointerButton", "iu", BTN_LEFT, 1)
        else:
            self._notify("NotifyPointerButton", "iu", BTN_LEFT, 0)
            self._keys(reversed(syms), 0)

    def move(self, dx, dy):
        self._notify("NotifyPointerMotion", "dd", float(dx), float(dy))


def detect(token_file, remember=False, pointer=False):
    """Return the best input backend for this session, or None."""
    if os.environ.get("XDG_SESSION_TYPE") == "x11" or not os.environ.get("WAYLAND_DISPLAY"):
        return Xdotool() if shutil.which("xdotool") else None
    portal = Portal(token_file, remember, pointer)
    if portal.available():
        return portal
    return Wtype() if shutil.which("wtype") else None


MEDIA = {"PlayPause", "Next", "Previous", "Stop"}


def media(method):
    """MPRIS over DBus: control the playing player, else the first one. No permission needed."""
    from jeepney import DBusAddress, Properties, message_bus, new_method_call
    from jeepney.io.blocking import Proxy, open_dbus_connection

    with open_dbus_connection(bus="SESSION") as conn:
        names = [n for n in Proxy(message_bus, conn).ListNames()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        if not names:
            raise RuntimeError("no media player running")
        player = lambda n: DBusAddress("/org/mpris/MediaPlayer2", n, "org.mpris.MediaPlayer2.Player")
        playing = lambda n: conn.send_and_get_reply(Properties(player(n)).get("PlaybackStatus")).body[0][1] == "Playing"
        target = next((n for n in names if playing(n)), names[0])
        conn.send_and_get_reply(new_method_call(player(target), method))


def validate(bindings):
    for gesture, b in bindings.items():
        if len(set(b) & {"cmd", "media", "keys"}) != 1 or not set(b) <= {"cmd", "media", "keys", "repeat"}:
            raise ValueError(f"binding {gesture}: needs exactly one of cmd, media, keys (plus optional repeat)")
        if "repeat" in b and not (isinstance(b["repeat"], (int, float)) and b["repeat"] > 0):
            raise ValueError(f"binding {gesture}: repeat must be a number of seconds > 0")
        if "keys" in b:
            for k in parse_keys(b["keys"]):
                keysym(k)
        if "media" in b and b["media"] not in MEDIA:
            raise ValueError(f"binding {gesture}: media must be one of {sorted(MEDIA)}")


class Actions:
    """Runs bindings. The key backend (and any permission prompt) is only touched when a `keys` binding fires."""

    def __init__(self, bindings, token_file, remember=False, pointer=False):
        validate(bindings)
        self.bindings, self.token_file, self.remember, self.pointer = bindings, token_file, remember, pointer
        self.backend = None

    def input(self):
        if self.backend is None:
            self.backend = detect(self.token_file, self.remember, self.pointer)
            if self.backend is None:
                raise RuntimeError("no key backend: install xdotool (X11) or wtype (wlroots)")
        return self.backend

    def fire(self, gesture):
        b = self.bindings.get(gesture)
        if b is None:
            return False
        if "cmd" in b:
            subprocess.run(shlex.split(b["cmd"]), check=True, timeout=5)
        elif "media" in b:
            media(b["media"])
        else:
            self.input().press(parse_keys(b["keys"]))
        return True
