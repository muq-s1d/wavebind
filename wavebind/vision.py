"""Camera -> MediaPipe GestureRecognizer -> (label, wrist x, frame, landmarks) per frame."""
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision


def stream(model, camera=0, fps=15, idle_fps=5):
    """Yield (now, label, wrist_x, frame, landmarks) at most `fps` times per second, `idle_fps` while
    no hand is visible. label/wrist_x/landmarks are None with no hand. Skipped frames are grabbed,
    not decoded, to save CPU."""
    cap = cv2.VideoCapture(camera)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    options = vision.GestureRecognizerOptions(
        base_options=BaseOptions(model_asset_path=str(model)), running_mode=vision.RunningMode.VIDEO
    )
    last_ms, next_at, interval = -1, 0.0, 1 / idle_fps
    with vision.GestureRecognizer.create_from_options(options) as rec:
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
                yield now, label or None, hand[0].x, frame, hand
        finally:
            cap.release()


COLORS = {"idle": (160, 160, 160), "arming": (0, 200, 255), "armed": (0, 220, 0), "cooldown": (255, 120, 0)}


def draw(frame, hand, label, state, fired=None):
    """Mirrored (selfie) view with landmarks, label and engine state."""
    img = cv2.flip(frame, 1)
    h, w = img.shape[:2]
    color = COLORS[state]
    for p in hand or []:
        cv2.circle(img, (int((1 - p.x) * w), int(p.y * h)), 3, color, -1)
    cv2.rectangle(img, (0, 0), (w - 1, h - 1), color, 6)
    cv2.putText(img, f"{state}  {label or '-'}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if fired:
        cv2.putText(img, f"fired {fired}", (12, h - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    return img
