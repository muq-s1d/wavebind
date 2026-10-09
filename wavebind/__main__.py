import argparse
import hashlib
import os
import sys
import tomllib
import urllib.request
from pathlib import Path

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "wavebind"
MODEL = DATA / "gesture_recognizer.task"
PORTAL_TOKEN = DATA / "portal_token"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wavebind" / "config.toml"
DEFAULT_CONFIG = Path(__file__).with_name("default.toml")
# Pinned version "1" of Google's model, so the checksum stays valid.
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
MODEL_SHA256 = "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_ok():
    return MODEL.exists() and sha256(MODEL) == MODEL_SHA256


def load_config():
    path = CONFIG if CONFIG.exists() else DEFAULT_CONFIG
    return path, tomllib.loads(path.read_text())


def actions(cfg):
    from . import inject

    remember = cfg.get("remember_key_permission", True)
    return inject.Actions(cfg["bindings"], PORTAL_TOKEN, remember, pointer="pinch" in cfg or "pointer" in cfg)


def setup(args):
    """The only network access wavebind ever does: fetch the model once."""
    if model_ok():
        print(f"model already installed: {MODEL}")
        return
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = MODEL.with_suffix(".part")
    print(f"downloading model (8.4 MB) to {MODEL}")
    with urllib.request.urlopen(MODEL_URL, timeout=30) as r:
        tmp.write_bytes(r.read())
    if sha256(tmp) != MODEL_SHA256:
        tmp.unlink()
        sys.exit("checksum mismatch, model not installed")
    tmp.replace(MODEL)
    print("done, wavebind now runs fully offline")


def check(args):
    import cv2
    import mediapipe

    from . import inject

    print(f"python      {sys.version.split()[0]}")
    print(f"mediapipe   {mediapipe.__version__}   opencv {cv2.__version__}")
    print(f"session     {os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    path, cfg = load_config()
    print(f"config      {path}   bindings: {', '.join(cfg['bindings'])}")
    ok_model = model_ok()
    print(f"model       {MODEL} {'ok' if ok_model else 'MISSING or corrupt, run: wavebind setup'}")
    cap = cv2.VideoCapture(args.camera)
    ok, frame = cap.read()
    cap.release()
    print(f"camera {args.camera}    {'%dx%d' % (frame.shape[1], frame.shape[0]) if ok else 'FAILED to read a frame'}")
    backend = inject.detect(PORTAL_TOKEN, cfg.get("remember_key_permission", True))
    print(f"keys        {backend.name if backend else 'NONE: install xdotool (X11) or wtype (wlroots)'}")
    if args.type and backend:
        print("pressing Shift once (GNOME/KDE may ask for permission the first time)...")
        try:
            backend.press(["Shift_L"])
        except (TimeoutError, PermissionError) as e:
            sys.exit(f"key press failed: {e}")
        print("key sent")
    if not (ok_model and ok and backend):
        sys.exit(1)


def fire(args):
    """Run one binding by name, no camera. Handy for testing a config."""
    try:
        if not actions(load_config()[1]).fire(args.gesture):
            sys.exit(f"no binding for {args.gesture}")
    except Exception as e:
        sys.exit(f"{args.gesture} failed: {e}")
    print(f"{args.gesture} done")


def run(args):
    if args.preview:
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # pip OpenCV's Qt only ships the X11 plugin (XWayland)
    import cv2

    from . import engine, inject, vision

    if not model_ok():
        sys.exit("model missing or corrupt, run: wavebind setup")
    path, cfg = load_config()
    try:
        act = actions(cfg)
        thresholds = {k: v for k, v in cfg.get("pinch", {}).items() if k in ("on", "off", "frames")}
        pinch = drag = pointer = None
        if "pinch" in cfg:
            p = dict(cfg["pinch"])
            drag_keys = inject.parse_keys(p.pop("modifier", "super"))
            for k in thresholds:
                p.pop(k)
            pinch = engine.Pinch(**thresholds)
            drag = engine.Drag(**p)  # leftover (misspelled) keys raise TypeError
        if "pointer" in cfg:
            q = dict(cfg["pointer"])
            pointer_gesture, lost_after = q.pop("gesture", "Pointing_Up"), q.pop("lost_after", 0.5)
            pointer_fps = q.pop("fps", 30)
            click = engine.Pinch(q.pop("click_on", 0.3), q.pop("click_off", 0.45), frames=1)  # quick pinches count
            pointer = engine.Pointer(**q)
        gestures = list(cfg["bindings"]) + ([pointer_gesture] if pointer else [])
        repeat = {g: b["repeat"] for g, b in cfg["bindings"].items() if "repeat" in b}
        eng = engine.Engine(gestures, repeat, **cfg.get("engine", {}))
        swipe = engine.Swipe(**cfg.get("swipe", {}))
    except (TypeError, ValueError) as e:
        sys.exit(f"bad config {path}: {e}")
    print(f"config {path}\nhold an open palm to arm, then make a gesture. {'q in the window' if args.preview else 'Ctrl+C'} quits.")
    fired, fired_at, was_armed, window_open = None, 0, False, False
    dragging = pointing = pressed = False  # window drag / pointer mode / left button held in pointer mode
    seen_at = 0.0
    rates = {"fps": cfg.get("fps", 15), "idle_fps": cfg.get("idle_fps", 5)}
    tips = lambda h: ((h[4].x + h[8].x) / 2, (h[4].y + h[8].y) / 2)  # between thumb and index tip
    knuckle = lambda h: (h[5].x, h[5].y)  # index knuckle: stays put when the fingertip pinches

    def stop_pointing(why):
        nonlocal pointing, pressed
        if pressed:
            pressed = False
            act.input().drag([], False)
        pointing = False
        rates["fps"] = cfg.get("fps", 15)
        eng.disarm()
        print(f"pointer off ({why})")

    try:
        for now, label, wrist, frame, hand in vision.stream(MODEL, args.camera, rates):
            gesture, pinching = None, False
            if pointing:  # mouse follows the hand, pinch = left button; lower the hand to stop
                try:
                    if hand is None:
                        if now - seen_at > lost_after:
                            stop_pointing("hand out of view")
                    else:
                        seen_at = now
                        act.input().move(*pointer.update(knuckle(hand), now))
                        down = click.update(hand)
                        if down != pressed:
                            act.input().drag([], down)
                            pressed = down
                except Exception as e:
                    print(f"pointer failed: {e}", file=sys.stderr)
                    stop_pointing("error")
            else:
                closed = pinch.update(hand) if pinch else False
                pinching = closed and (dragging or label != "Closed_Fist")  # a fist's thumb rests near the index tip
                try:
                    if dragging and not closed:
                        dragging = False
                        act.input().drag(drag_keys, False)
                        eng.pause(now, eng.swipe_cooldown)  # lowering the hand after a drop is not a swipe
                        print("dropped")
                    elif dragging:
                        act.input().move(*drag.update(tips(hand)))
                    elif pinching and eng.armed and not eng.cooling(now):
                        act.input().drag(drag_keys, True)
                        dragging = True
                        drag.start(tips(hand))
                        print("dragging")
                except Exception as e:
                    print(f"pinch-drag failed: {e}", file=sys.stderr)
                    pinch = None  # stop trying; the finally below releases anything still held
                if dragging or pinching:  # a pinch is never a gesture or a swipe
                    if eng.armed:
                        eng.keep_armed(now)
                    swipe.update(wrist, now, active=False)
                else:
                    tracking = eng.armed and not eng.cooling(now)  # no swipes during cooldown: the hand is moving back
                    gesture = eng.update(swipe.update(wrist, now, tracking) or label, now)
            if eng.armed and not was_armed:
                print("armed")
            was_armed = eng.armed
            if gesture:
                fired, fired_at = gesture, now
                if pointer and gesture == pointer_gesture:
                    pointing, seen_at = True, now
                    rates["fps"] = pointer_fps  # full speed while pointing: smoother, and quick pinches aren't missed
                    click.update(None)  # start with the button up
                    pointer.start(knuckle(hand), now)
                    print("pointer on")
                else:
                    try:
                        act.fire(gesture)
                        print(f"fired {gesture}")
                    except Exception as e:
                        print(f"{gesture} failed: {e}", file=sys.stderr)
            if args.preview:
                shown = fired if now - fired_at < 1.5 else None
                state = "pointer" if pointing else "dragging" if dragging else eng.state(now)
                shown_label = ("click" if pressed else "move") if pointing else "pinch" if pinching else label
                image = vision.draw(frame, hand, shown_label, state, shown)
                if window_open:
                    cv2.imshow("wavebind", image)
                else:
                    with vision.quiet_stderr():  # Qt prints XWayland and missing-font warnings on window creation
                        cv2.imshow("wavebind", image)
                        cv2.waitKey(1)
                    window_open = True
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27) or cv2.getWindowProperty("wavebind", cv2.WND_PROP_VISIBLE) < 1:
                    break  # q, Esc, or the window was closed
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    finally:  # never leave Super or a mouse button held down
        if dragging:
            act.input().drag(drag_keys, False)
        if pressed:
            act.input().drag([], False)


def main():
    p = argparse.ArgumentParser(prog="wavebind", description="Bind webcam hand gestures to desktop actions.")
    sub = p.add_subparsers(required=True)
    r = sub.add_parser("run", help="watch the camera and fire bindings")
    r.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    r.add_argument("--preview", action="store_true", help="show the camera with landmarks and state")
    r.set_defaults(func=run)
    sub.add_parser("setup", help="download the gesture model (one time)").set_defaults(func=setup)
    c = sub.add_parser("check", help="test model, camera and key backend")
    c.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    c.add_argument("--type", action="store_true", help="also send one harmless key press")
    c.set_defaults(func=check)
    f = sub.add_parser("fire", help="run one binding by gesture name, e.g. Thumb_Up")
    f.add_argument("gesture")
    f.set_defaults(func=fire)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
