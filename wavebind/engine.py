"""Gesture -> fire decisions. Pure logic, times in seconds, no camera."""
from collections import deque


class Engine:
    """Hold the arm gesture `arm_hold` s to arm. While armed, each bound gesture seen `stable_frames`
    frames in a row fires (swipes fire immediately), followed by `cooldown` s of nothing
    (`swipe_cooldown` after a swipe, so moving the hand back is not read as the opposite swipe).
    Gestures chain without re-arming; each fire keeps it armed for another `armed_for` s.
    A gesture that just fired must be released (another shape for `stable_frames` frames) before it
    fires again, so holding a fist toggles play/pause once. Gestures listed in `repeat`
    ({gesture: seconds}) instead re-fire every that many seconds while held, like a held key.
    Nothing fires unless armed."""

    def __init__(
        self, gestures, repeat=None, arm_gesture="Open_Palm", arm_hold=0.5, armed_for=3.0,
        cooldown=0.5, swipe_cooldown=1.0, stable_frames=3,
    ):
        self.gestures, self.repeat, self.arm_gesture = set(gestures), repeat or {}, arm_gesture
        self.arm_hold, self.armed_for, self.stable_frames = arm_hold, armed_for, stable_frames
        self.cooldown, self.swipe_cooldown = cooldown, swipe_cooldown
        self.palm_since = self.armed_until = self.cooldown_until = None
        self.last, self.streak, self.held, self.away = None, 0, None, 0
        self.next_repeat = 0.0

    @property
    def armed(self):
        return self.armed_until is not None

    def cooling(self, now):
        return self.cooldown_until is not None and now < self.cooldown_until

    def state(self, now):
        if self.armed:
            return "cooldown" if self.cooling(now) else "armed"
        return "arming" if self.palm_since is not None else "idle"

    def update(self, label, now):
        """Feed one frame's label (or a swipe event). Returns the gesture to fire, or None."""
        if not self.armed:
            if label != self.arm_gesture:
                self.palm_since = None
            elif self.palm_since is None:
                self.palm_since = now
            elif now - self.palm_since >= self.arm_hold:
                self.armed_until, self.palm_since, self.cooldown_until = now + self.armed_for, None, None
                self.last, self.streak, self.held, self.away = None, 0, None, 0
            return None
        if now > self.armed_until:
            self.armed_until = None
            return None
        if self.held is not None:  # release tracking runs during cooldown too
            self.away = 0 if label == self.held else self.away + 1
            if self.away >= self.stable_frames:
                self.held = None
        if label is not None and label == self.held and label in self.repeat and now >= self.next_repeat:
            self.next_repeat = now + self.repeat[label]
            self.armed_until = now + self.armed_for
            return label
        if self.cooling(now) or label not in self.gestures or label in (self.arm_gesture, self.held):
            self.last, self.streak = None, 0
            return None
        self.streak = self.streak + 1 if label == self.last else 1
        self.last = label
        swipe = label.startswith("swipe_")
        if not swipe and self.streak < self.stable_frames:
            return None
        self.armed_until = now + self.armed_for
        self.cooldown_until = now + (self.swipe_cooldown if swipe else self.cooldown)
        self.last, self.streak, self.held, self.away = None, 0, (None if swipe else label), 0
        self.next_repeat = now + max(self.repeat.get(label, 0), self.cooldown)  # first repeat waits a bit, like a held key
        return label


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
