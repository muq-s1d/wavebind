import tempfile
import tomllib
import unittest
from pathlib import Path

from wavebind import engine, inject


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
        self.assertTrue(cfg["remember_key_permission"])
        self.assertEqual(cfg["bindings"]["swipe_left"]["keys"], "super+Page_Down")  # phone-style: left -> next

    def test_invalid(self):
        for bad in ({}, {"cmd": "x", "keys": "a"}, {"run": "x"}, {"keys": "NotAKey"}, {"media": "Louder"},
                    {"repeat": 0.3}, {"cmd": "x", "repeat": 0}, {"cmd": "x", "repeat": "fast"}):
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



def feed(eng, frames, t=0.0, dt=0.1):
    """Feed labels one frame every dt s; return (fired gestures, end time)."""
    fired = []
    for label in frames:
        g = eng.update(label, t)
        if g:
            fired.append(g)
        t += dt
    return fired, t


class EngineTest(unittest.TestCase):
    # defaults: hold 0.5 s, armed 3 s, cooldown 0.5 s, swipe_cooldown 1 s, 3 stable frames; frames every 0.1 s
    def armed(self):
        eng = engine.Engine({"Closed_Fist", "Thumb_Up", "Thumb_Down", "swipe_left", "swipe_right"})
        _, t = feed(eng, ["Open_Palm"] * 6)  # t=0.0..0.5 -> armed at 0.5
        self.assertTrue(eng.armed)
        return eng, t

    def test_nothing_fires_unarmed(self):
        eng = engine.Engine({"Closed_Fist"})
        fired, _ = feed(eng, ["Closed_Fist"] * 20)
        self.assertEqual(fired, [])

    def test_short_palm_does_not_arm(self):
        eng = engine.Engine({"Closed_Fist"})
        feed(eng, ["Open_Palm"] * 5 + [None] + ["Open_Palm"] * 5)  # 0.4 s, gap, 0.4 s
        self.assertFalse(eng.armed)

    def test_needs_stable_frames(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Closed_Fist"] * 2 + [None], t)
        self.assertEqual(fired, [])

    def test_holding_fires_once(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Closed_Fist"] * 30, t)  # 3 s held
        self.assertEqual(fired, ["Closed_Fist"])

    def test_chain_without_rearming(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Thumb_Up"] * 8 + ["Thumb_Down"] * 8 + ["Closed_Fist"] * 8, t)
        self.assertEqual(fired, ["Thumb_Up", "Thumb_Down", "Closed_Fist"])

    def test_same_gesture_again_after_release(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Thumb_Up"] * 6 + [None] * 4 + ["Thumb_Up"] * 6, t)
        self.assertEqual(fired, ["Thumb_Up", "Thumb_Up"])

    def test_one_frame_flicker_is_not_a_release(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Thumb_Up"] * 6 + [None] + ["Thumb_Up"] * 10, t)
        self.assertEqual(fired, ["Thumb_Up"])

    def test_disarms_after_idle(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Thumb_Up"] * 4, t)
        fired, t = feed(eng, [None] * 32, t)  # 3.2 s with no gesture
        self.assertFalse(eng.armed)
        fired, t = feed(eng, ["Closed_Fist"] * 5, t)
        self.assertEqual(fired, [])

    def test_hold_repeats(self):
        eng = engine.Engine({"Thumb_Up", "Closed_Fist"}, {"Thumb_Up": 0.3})
        _, t = feed(eng, ["Open_Palm"] * 6)
        start = t
        times = []
        for i in range(21):  # thumb held 2 s, frames every 0.1 s
            if eng.update("Thumb_Up", t):
                times.append(round(t - start, 1))
            t += 0.1
        # first fire on frame 3, first repeat after 0.5 s (cooldown), then every 0.3 s
        self.assertEqual(times, [0.2, 0.7, 1.0, 1.3, 1.6, 1.9])
        self.assertTrue(eng.armed)  # repeats keep it armed

    def test_repeat_does_not_affect_others(self):
        eng = engine.Engine({"Thumb_Up", "Closed_Fist"}, {"Thumb_Up": 0.3})
        _, t = feed(eng, ["Open_Palm"] * 6)
        fired, t = feed(eng, ["Closed_Fist"] * 20, t)
        self.assertEqual(fired, ["Closed_Fist"])

    def test_unbound_ignored(self):
        eng, t = self.armed()
        fired, t = feed(eng, ["Victory"] * 5, t)
        self.assertEqual(fired, [])

    def test_swipe_then_no_swipe_during_cooldown(self):
        eng, t = self.armed()
        self.assertEqual(eng.update("swipe_left", t), "swipe_left")
        self.assertTrue(eng.cooling(t + 0.9))  # run() stops feeding the swipe tracker while cooling
        self.assertIsNone(eng.update("swipe_right", t + 0.5))
        self.assertEqual(eng.update("swipe_right", t + 1.1), "swipe_right")


class SwipeTest(unittest.TestCase):
    def test_directions(self):
        for xs, want in (([0.3, 0.45, 0.6], "swipe_left"), ([0.7, 0.55, 0.4], "swipe_right")):
            sw = engine.Swipe()
            got = [sw.update(x, i * 0.1) for i, x in enumerate(xs)]
            self.assertEqual(got[-1], want, xs)

    def test_slow_drift_is_not_a_swipe(self):
        sw = engine.Swipe()
        got = [sw.update(0.3 + i * 0.02, i * 0.1) for i in range(20)]  # 0.38 total over 2 s
        self.assertEqual([g for g in got if g], [])

    def test_hand_lost_resets(self):
        sw = engine.Swipe()
        sw.update(0.3, 0.0)
        sw.update(None, 0.1)
        self.assertIsNone(sw.update(0.6, 0.2))


if __name__ == "__main__":
    unittest.main()
