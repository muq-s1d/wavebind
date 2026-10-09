import tempfile
import tomllib
import unittest
from pathlib import Path

from wavebind import __main__ as cli, engine, inject


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
    def start(self, token, remember, pointer=False, granted=None):
        p = inject.Portal.__new__(inject.Portal)  # skip __init__, no DBus
        p.token_file, p.remember, p.pointer, sent = token, remember, pointer, {}

        def request(method, sig, *args, **opts):
            sent[method] = opts
            types = opts.get("types", sent.get("SelectDevices", {}).get("types", ("u", 1)))[1]
            return {"session_handle": "/s", "restore_token": "NEW", "devices": types if granted is None else granted}

        p._request = request
        p.start()
        return sent["SelectDevices"]

    def test_forget(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("1 OLD")
            opts = self.start(token, remember=False)
            self.assertEqual(opts["persist_mode"], ("u", 0))
            self.assertNotIn("restore_token", opts)
            self.assertFalse(token.exists())  # an old grant is deleted

    def test_pointer_only_when_asked(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.start(Path(d) / "t", True)["types"], ("u", 1))  # keyboard only
            self.assertEqual(self.start(Path(d) / "t", True, pointer=True)["types"], ("u", 3))  # + pointer

    def test_remember(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("1 OLD")
            token.chmod(0o644)
            opts = self.start(token, remember=True)
            self.assertEqual(opts["persist_mode"], ("u", 2))
            self.assertEqual(opts["restore_token"], ("s", "OLD"))
            self.assertEqual(token.read_text(), "1 NEW")
            self.assertEqual(token.stat().st_mode & 0o777, 0o600)

    def test_keyboard_grant_not_reused_for_pointer(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("1 OLD")  # saved when only the keyboard was asked for
            opts = self.start(token, remember=True, pointer=True)
            self.assertNotIn("restore_token", opts)  # so the desktop asks again
            self.assertEqual(token.read_text(), "3 NEW")

    def test_old_format_token_not_reused(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            token.write_text("OLD")
            self.assertNotIn("restore_token", self.start(token, remember=True))

    def test_missing_device_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            token = Path(d) / "t"
            with self.assertRaisesRegex(PermissionError, "mouse pointer"):
                self.start(token, remember=True, pointer=True, granted=1)
            self.assertFalse(token.exists())


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
    def run_track(self, points, extra=2):
        """Hand already settled in view, then wrist points 0.1 s apart, then the last point held `extra` frames."""
        sw, got = engine.Swipe(), []
        for i, pt in enumerate([points[0]] * 4 + points + [points[-1]] * extra):
            got.append(sw.update(pt, i * 0.1))
        return [g for g in got if g]

    def test_four_directions(self):
        # raw camera image: x grows toward the user's left, y grows downward
        self.assertEqual(self.run_track([(0.3, 0.5), (0.45, 0.5), (0.6, 0.5)]), ["swipe_left"])
        self.assertEqual(self.run_track([(0.7, 0.5), (0.55, 0.5), (0.4, 0.5)]), ["swipe_right"])
        self.assertEqual(self.run_track([(0.5, 0.7), (0.5, 0.55), (0.5, 0.4)]), ["swipe_up"])
        self.assertEqual(self.run_track([(0.5, 0.3), (0.5, 0.45), (0.5, 0.6)]), ["swipe_down"])

    def test_dominant_axis_wins(self):
        self.assertEqual(self.run_track([(0.3, 0.5), (0.45, 0.55), (0.6, 0.6)]), ["swipe_left"])

    def test_waits_for_confirmation(self):
        self.assertEqual(self.run_track([(0.3, 0.5), (0.45, 0.5), (0.6, 0.5)], extra=1), [])

    def test_raising_hand_into_frame_is_not_a_swipe(self):
        sw = engine.Swipe()
        got = [sw.update(pt, i * 0.1) for i, pt in enumerate([None, (0.5, 0.9), (0.5, 0.7), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)])]
        self.assertEqual([g for g in got if g], [])

    def test_inactive_tracks_presence_only(self):
        sw = engine.Swipe()
        for i in range(4):
            sw.update((0.3, 0.5), i * 0.1, active=False)  # hand settled while e.g. arming
        got = [sw.update(pt, 0.4 + i * 0.1) for i, pt in enumerate([(0.3, 0.5), (0.45, 0.5), (0.6, 0.5), (0.6, 0.5), (0.6, 0.5)])]
        self.assertEqual([g for g in got if g], ["swipe_left"])  # no extra settle delay once active

    def test_hand_leaving_frame_is_not_a_swipe(self):
        sw = engine.Swipe()
        pts = [(0.5, 0.5)] * 4 + [(0.5, 0.65), (0.5, 0.8), None, None]
        got = [sw.update(pt, i * 0.1) for i, pt in enumerate(pts)]
        self.assertEqual([g for g in got if g], [])  # dropped hand: crossed the threshold, then vanished

    def test_slow_drift_is_not_a_swipe(self):
        self.assertEqual(self.run_track([(0.3 + i * 0.02, 0.5) for i in range(20)]), [])  # 0.38 over 2 s

    def test_hand_lost_resets(self):
        sw = engine.Swipe()
        sw.update((0.3, 0.5), 0.0)
        sw.update(None, 0.1)
        self.assertIsNone(sw.update((0.6, 0.5), 0.2))


def hand(thumb_index):
    """21 fake landmarks: palm size 0.2 (wrist to middle knuckle), thumb and index tips `thumb_index` apart."""
    from types import SimpleNamespace as P

    pts = [P(x=0.5, y=0.5) for _ in range(21)]
    pts[0], pts[9] = P(x=0.5, y=0.7), P(x=0.5, y=0.5)
    pts[4], pts[8] = P(x=0.5, y=0.4), P(x=0.5 + thumb_index, y=0.4)
    return pts


class PinchTest(unittest.TestCase):
    def test_hysteresis(self):
        p = engine.Pinch(frames=1)  # closes below 0.25 * palm (0.05), opens above 0.45 * palm (0.09)
        self.assertEqual([p.update(hand(d)) for d in (0.15, 0.04, 0.08, 0.08, 0.1, 0.06)],
                         [False, True, True, True, False, False])

    def test_needs_frames_in_a_row(self):
        p = engine.Pinch()  # frames=3
        self.assertEqual([p.update(hand(d)) for d in (0.04, 0.04, 0.15, 0.04, 0.04, 0.04)],
                         [False, False, False, False, False, True])

    def test_no_hand_opens(self):
        p = engine.Pinch()
        p.update(hand(0.02))
        self.assertFalse(p.update(None))


class DragTest(unittest.TestCase):
    def test_mirrored_and_scaled(self):
        d = engine.Drag(gain=1000, smooth=1.0, aspect=1.0)
        d.start((0.5, 0.5))
        dx, dy = d.update((0.4, 0.6))  # image x down = hand moved to the user's right; image y down = down
        self.assertAlmostEqual(dx, 100)
        self.assertAlmostEqual(dy, 100)

    def test_smoothing_converges(self):
        d = engine.Drag(gain=1000, smooth=0.5, aspect=1.0)
        d.start((0.5, 0.5))
        total = sum(d.update((0.4, 0.5))[0] for _ in range(30))
        self.assertAlmostEqual(total, 100, places=3)  # same total travel, just spread out


class ConfigMerge(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            old, cli.CONFIG = cli.CONFIG, Path(d) / "config.toml"
            try:
                cli.CONFIG.write_text(text)
                return cli.load_config()
            finally:
                cli.CONFIG = old

    def test_user_file_only_needs_changes(self):
        path, cfg = self.load('pinch = false\n[engine]\narm_hold = 0.8\n[bindings]\nVictory = false\nThumb_Up = { media = "Next" }\n')
        self.assertEqual(path.name, "config.toml")
        self.assertEqual(cfg["engine"]["arm_hold"], 0.8)
        self.assertEqual(cfg["engine"]["armed_for"], 3.0)  # other defaults in the table kept
        self.assertEqual(cfg["bindings"]["Thumb_Up"], {"media": "Next"})  # overridden
        self.assertIn("swipe_left", cfg["bindings"])  # untouched defaults kept
        self.assertNotIn("Victory", cfg["bindings"])  # unbound
        self.assertFalse(cfg["pinch"])
        inject.validate(cfg["bindings"])

    def test_no_user_file_means_defaults(self):
        old, cli.CONFIG = cli.CONFIG, Path("/nonexistent/wavebind/config.toml")
        try:
            path, cfg = cli.load_config()
        finally:
            cli.CONFIG = old
        self.assertEqual(path, cli.DEFAULT_CONFIG)
        self.assertIn("pinch", cfg)


if __name__ == "__main__":
    unittest.main()
