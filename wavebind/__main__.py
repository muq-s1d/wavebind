import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

os.environ.setdefault("GLOG_minloglevel", "2")  # silence MediaPipe's startup chatter

DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "wavebind"
MODEL = DATA / "gesture_recognizer.task"
PORTAL_TOKEN = DATA / "portal_token"
# Pinned version "1" of Google's model, so the checksum stays valid.
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task"
MODEL_SHA256 = "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup(args):
    """The only network access wavebind ever does: fetch the model once."""
    if MODEL.exists() and sha256(MODEL) == MODEL_SHA256:
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
    model_ok = MODEL.exists() and sha256(MODEL) == MODEL_SHA256
    print(f"model       {MODEL} {'ok' if model_ok else 'MISSING, run: wavebind setup'}")
    cap = cv2.VideoCapture(args.camera)
    ok, frame = cap.read()
    cap.release()
    print(f"camera {args.camera}    {'%dx%d' % (frame.shape[1], frame.shape[0]) if ok else 'FAILED to read a frame'}")
    backend = inject.detect(PORTAL_TOKEN)
    print(f"keys        {backend.name if backend else 'NONE: install xdotool (X11) or wtype (wlroots)'}")
    if args.type and backend:
        print("pressing Shift once (GNOME/KDE may ask for permission the first time)...")
        try:
            backend.press(["Shift_L"])
        except (TimeoutError, PermissionError) as e:
            sys.exit(f"key press failed: {e}")
        print("key sent")
    if not (model_ok and ok and backend):
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(prog="wavebind", description="Bind webcam hand gestures to desktop actions.")
    sub = p.add_subparsers(required=True)
    sub.add_parser("setup", help="download the gesture model (one time)").set_defaults(func=setup)
    c = sub.add_parser("check", help="test model, camera and key backend")
    c.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    c.add_argument("--type", action="store_true", help="also send one harmless key press")
    c.set_defaults(func=check)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
