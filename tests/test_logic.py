import tempfile
import tomllib
import unittest
from pathlib import Path

from wavebind import inject


class Keys(unittest.TestCase):
    def test_parse_aliases(self):
        self.assertEqual(inject.parse_keys("super+Page_Down"), ["Super_L", "Page_Down"])
        self.assertEqual(inject.parse_keys("Ctrl + alt+Left"), ["Control_L", "Alt_L", "Left"])

    def test_keysym(self):
        # values from xkbcommon-keysyms.h
        self.assertEqual(inject.keysym("Super_L"), 0xFFEB)
        self.assertEqual(inject.keysym("Page_Down"), 0xFF56)
        self.assertEqual(inject.keysym("XF86AudioPlay"), 0x1008FF14)
        self.assertEqual(inject.keysym("print"), 0xFF61)  # case-insensitive fallback
        with self.assertRaises(ValueError):
            inject.keysym("NotAKey")


class Bindings(unittest.TestCase):
    def test_default_config_is_valid(self):
        cfg = tomllib.loads(Path(inject.__file__).with_name("default.toml").read_text())
        inject.validate(cfg["bindings"])
        self.assertFalse(cfg["remember_key_permission"])

    def test_invalid(self):
        for bad in ({}, {"cmd": "x", "keys": "a"}, {"run": "x"}, {"keys": "NotAKey"}, {"media": "Louder"}):
            with self.assertRaises(ValueError, msg=bad):
                inject.validate({"g": bad})

    def test_keys_backend_only_touched_for_keys(self):
        pressed = []

        class Fake:
            def press(self, keys):
                pressed.append(keys)

        a = inject.Actions({"ok": {"cmd": "true"}, "k": {"keys": "super+Page_Up"}}, Path("/nonexistent"))
        self.assertTrue(a.fire("ok"))
        self.assertIsNone(a.backend)  # a cmd binding must not open the portal
        self.assertFalse(a.fire("unbound"))
        a.backend = Fake()
        a.fire("k")
        self.assertEqual(pressed, [["Super_L", "Page_Up"]])


class PortalToken(unittest.TestCase):
    def start(self, token, remember):
        p = inject.Portal.__new__(inject.Portal)  # skip __init__, no DBus
        p.token_file, p.remember, sent = token, remember, {}

        def request(method, sig, *args, **opts):
            sent[method] = opts
            return {"session_handle": "/s", "restore_token": "NEW"}

        p._request = request
        p.start()
        return sent["SelectDevices"]

    def test_forget(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("OLD")
            opts = self.start(token, remember=False)
            self.assertEqual(opts["persist_mode"], ("u", 0))
            self.assertNotIn("restore_token", opts)
            self.assertFalse(token.exists())  # an old grant is deleted

    def test_remember(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("OLD")
            token.chmod(0o644)
            opts = self.start(token, remember=True)
            self.assertEqual(opts["persist_mode"], ("u", 2))
            self.assertEqual(opts["restore_token"], ("s", "OLD"))
            self.assertEqual(token.read_text(), "NEW")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
