"""Camera -> MediaPipe GestureRecognizer -> (label, wrist (x, y), frame, landmarks) per frame."""
import contextlib
import os
import sys
import time

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision


@contextlib.contextmanager
def quiet_stderr():
    """Mute fd 2. MediaPipe's C++ logging and Qt's startup warnings ignore env vars and Python's sys.stderr."""
    sys.stderr.flush()
    saved, devnull = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)


def stream(model, camera=0, fps=15, idle_fps=5):
    """Yield (now, label, wrist, frame, landmarks) at most `fps` times per second, `idle_fps` while
    no hand is visible. wrist is (x, y) in 0..1 image coordinates. label/wrist/landmarks are None with no hand. Skipped frames are grabbed,
    not decoded, to save CPU."""
    with quiet_stderr():  # OpenCV logs its own errors for a busy camera; ours below is clearer
        cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        raise RuntimeError(f"can't open camera {camera} (in use by another app?)")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    options = vision.GestureRecognizerOptions(
        base_options=BaseOptions(model_asset_path=str(model)), running_mode=vision.RunningMode.VIDEO
    )
    last_ms, next_at, interval = 0, 0.0, 1 / idle_fps
    with quiet_stderr():  # model load and first inference are where all the log spam happens
        rec = vision.GestureRecognizer.create_from_options(options)
        rec.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((480, 640, 3), np.uint8)), 0)
    with rec:
        try:
            while True:
                if not cap.grab():
                    raise RuntimeError(f"camera {camera} stopped delivering frames")
                now = time.monotonic()
                if now < next_at:
                    continue
                next_at = max(next_at + interval, now)
                ok, frame = cap.retrieve()
                if not ok:
                    continue
                ms = last_ms = max(int(now * 1000), last_ms + 1)  # VIDEO mode needs strictly increasing timestamps
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                r = rec.recognize_for_video(image, ms)
                interval = 1 / (fps if r.hand_landmarks else idle_fps)
                if not r.hand_landmarks:
                    yield now, None, None, frame, None
                    continue
                hand = r.hand_landmarks[0]
                label = r.gestures[0][0].category_name if r.gestures and r.gestures[0] else None
                yield now, label or None, (hand[0].x, hand[0].y), frame, hand
        finally:
            cap.release()


COLORS = {"idle": (160, 160, 160), "arming": (0, 200, 255), "armed": (0, 220, 0), "cooldown": (255, 120, 0),
          "dragging": (255, 0, 255)}


def draw(frame, hand, label, state, fired=None):
    """Mirrored (selfie) view with landmarks, label and engine state."""
    img = cv2.flip(frame, 1)
    h, w = img.shape[:2]
    color = COLORS[state]
    for p in hand or []:
        cv2.circle(img, (int((1 - p.x) * w), int(p.y * h)), 3, color, -1)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), color, 6)
    cv2.putText(img, f"{state}  {label or '-'}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if fired:  # big, right under the state line, so one-frame events like swipes are visible
        cv2.putText(img, f"fired: {fired}", (12, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.3, color, 3)
    return img
