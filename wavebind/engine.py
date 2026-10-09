"""Gesture -> fire decisions. Pure logic, times in seconds, no camera."""
from collections import deque


class Engine:
    """Hold the arm gesture `arm_hold` s to arm. While armed (up to `armed_for` s), the first bound
    gesture seen for `stable_frames` frames in a row fires once (swipes fire immediately), then
    everything is ignored for `cooldown` s. Nothing fires unless armed, so stray hand movement is safe."""

    def __init__(self, gestures, arm_gesture="Open_Palm", arm_hold=0.5, armed_for=2.0, cooldown=1.0, stable_frames=3):
        self.gestures, self.arm_gesture = set(gestures), arm_gesture
        self.arm_hold, self.armed_for, self.cooldown, self.stable_frames = arm_hold, armed_for, cooldown, stable_frames
        self.palm_since = self.armed_until = self.cooldown_until = None
        self.last, self.streak = None, 0

    @property
    def armed(self):
        return self.armed_until is not None

    def state(self, now):
        if self.cooldown_until and now < self.cooldown_until:
            return "cooldown"
        return "armed" if self.armed else "arming" if self.palm_since is not None else "idle"

    def update(self, label, now):
        """Feed one frame's label (or a swipe event). Returns the gesture to fire, or None."""
        if self.cooldown_until and now < self.cooldown_until:
            return None
        if not self.armed:
            if label != self.arm_gesture:
                self.palm_since = None
            elif self.palm_since is None:
                self.palm_since = now
            elif now - self.palm_since >= self.arm_hold:
                self.armed_until, self.palm_since, self.last, self.streak = now + self.armed_for, None, None, 0
            return None
        if now > self.armed_until:
            self.armed_until = None
            return None
        if label not in self.gestures or label == self.arm_gesture:
            self.last, self.streak = None, 0
            return None
        self.streak = self.streak + 1 if label == self.last else 1
        self.last = label
        if label.startswith("swipe_") or self.streak >= self.stable_frames:
            self.armed_until, self.cooldown_until = None, now + self.cooldown
            return label
        return None


class Swipe:
    """Wrist moving `distance` (fraction of frame width) within `window` s -> swipe_left / swipe_right.
    Directions are from the user's point of view; the raw camera image is not mirrored."""

    def __init__(self, distance=0.25, window=0.4):
        self.distance, self.window = distance, window
        self.track = deque()

    def update(self, x, now):
        if x is None:
            self.track.clear()
            return None
        self.track.append((now, x))
        while now - self.track[0][0] > self.window:
            self.track.popleft()
        dx = x - self.track[0][1]
        if abs(dx) < self.distance:
            return None
        self.track.clear()
        return "swipe_left" if dx > 0 else "swipe_right"  # image x grows toward the user's left
