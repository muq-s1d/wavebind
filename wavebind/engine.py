"""Gesture -> fire decisions. Pure logic, times in seconds, no camera."""
import math
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

    def keep_armed(self, now):
        self.armed_until = now + self.armed_for

    def disarm(self):
        self.armed_until = self.palm_since = None

    def pause(self, now, seconds):
        self.cooldown_until = now + seconds

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
    """Wrist moving `distance` (fraction of frame width or height) within `window` s ->
    swipe_left / swipe_right / swipe_up / swipe_down, from the user's point of view (the raw camera
    image is not mirrored). The hand must be in view `settle` s before its motion counts (raising
    your hand into frame is not a swipe up) and still in view `confirm_frames` frames later
    (dropping it out of frame is not a swipe down)."""

    def __init__(self, distance=0.25, window=0.4, confirm_frames=2, settle=0.3):
        self.distance, self.window, self.confirm_frames, self.settle = distance, window, confirm_frames, settle
        self.track = deque()
        self.pending, self.seen, self.visible_since = None, 0, None

    def update(self, pos, now, active=True):
        """pos = (x, y) of the wrist, or None with no hand. active=False: note the hand, don't track it."""
        if pos is None:
            self.visible_since = None
        elif self.visible_since is None:
            self.visible_since = now
        if pos is None or not active or now - self.visible_since < self.settle:
            self.track.clear()
            self.pending = None
            return None
        if self.pending:
            self.seen += 1
            if self.seen < self.confirm_frames:
                return None
            direction, self.pending = self.pending, None
            return direction
        self.track.append((now, pos))
        while now - self.track[0][0] > self.window:
            self.track.popleft()
        (x0, y0), (x, y) = self.track[0][1], pos
        dx, dy = x - x0, y - y0
        if max(abs(dx), abs(dy)) < self.distance:
            return None
        self.track.clear()
        self.seen = 0
        if abs(dx) >= abs(dy):
            self.pending = "swipe_left" if dx > 0 else "swipe_right"  # image x grows toward the user's left
        else:
            self.pending = "swipe_up" if dy < 0 else "swipe_down"
        return None


class Pinch:
    """Thumb tip touching index tip, relative to palm size (wrist to middle knuckle) so it works at
    any distance from the camera. Closes after `frames` frames in a row below `on`, opens as soon as
    it is above `off`, so it neither triggers on a passing touch nor flickers."""

    def __init__(self, on=0.25, off=0.45, frames=3):
        self.on, self.off, self.frames = on, off, frames
        self.closed, self.count = False, 0

    def update(self, hand):
        if hand is None:
            self.closed, self.count = False, 0
            return False
        xy = lambda p: (p.x, p.y)
        ratio = math.dist(xy(hand[4]), xy(hand[8])) / max(math.dist(xy(hand[0]), xy(hand[9])), 1e-6)
        if self.closed:
            self.closed = ratio < self.off
        else:
            self.count = self.count + 1 if ratio < self.on else 0
            self.closed = self.count >= self.frames
        if self.closed:
            self.count = 0
        return self.closed


class Drag:
    """Pinch-hand motion -> pointer deltas in pixels. `gain` = pixels per full frame width of hand
    travel; x is mirrored so moving your hand right moves the pointer right. `smooth` (0..1) is how
    much of each new position is trusted: lower = steadier but laggier."""

    def __init__(self, gain=1500, smooth=0.5, aspect=640 / 480):
        self.gain, self.smooth, self.aspect = gain, smooth, aspect
        self.pos = None

    def start(self, pos):
        self.pos = pos

    def update(self, pos):
        x = self.pos[0] + self.smooth * (pos[0] - self.pos[0])
        y = self.pos[1] + self.smooth * (pos[1] - self.pos[1])
        dx, dy = (self.pos[0] - x) * self.gain, (y - self.pos[1]) * self.gain / self.aspect
        self.pos = (x, y)
        return dx, dy


class OneEuro:
    """One Euro filter (Casiez et al., CHI 2012) for a point: heavy smoothing when moving slowly
    (steady enough for small buttons), little when moving fast (no lag on big moves).
    min_cutoff (Hz): lower = steadier at rest. beta: higher = less lag when moving fast."""

    def __init__(self, min_cutoff=1.0, beta=0.007, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.prev = None

    def __call__(self, x, now):
        if self.prev is None:
            self.prev, self.speed, self.t = x, (0.0,) * len(x), now
            return x
        dt = max(now - self.t, 1e-3)
        self.t = now
        alpha = lambda cutoff: 1 / (1 + 1 / (2 * math.pi * cutoff * dt))
        a = alpha(self.d_cutoff)
        self.speed = tuple(a * (xi - pi) / dt + (1 - a) * si for xi, pi, si in zip(x, self.prev, self.speed))
        a = alpha(self.min_cutoff + self.beta * math.hypot(*self.speed))
        self.prev = tuple(a * xi + (1 - a) * pi for xi, pi in zip(x, self.prev))
        return self.prev


class Pointer:
    """Relative, like a trackpad with acceleration: slow hand movement moves the pointer precisely
    (`slow_gain`), a quick flick covers the screen (`fast_gain`), so corners are reachable without
    moving your hand out of view. The hand position is jitter-filtered first (One Euro).
    Gains are pointer pixels per full frame width of hand travel; speeds are frame widths per second."""

    def __init__(self, slow_gain=1200, fast_gain=6000, slow_speed=0.2, fast_speed=1.5,
                 min_cutoff=1.0, beta=0.02, frame=(640, 480)):
        self.slow_gain, self.fast_gain = slow_gain, fast_gain
        self.slow_speed, self.fast_speed = slow_speed, fast_speed
        self.min_cutoff, self.beta, self.frame = min_cutoff, beta, frame

    def px(self, hand):
        return hand[0] * self.frame[0], hand[1] * self.frame[1]  # filter in camera pixels

    def start(self, hand, now):
        self.filter = OneEuro(self.min_cutoff, self.beta)
        self.pos, self.t = self.filter(self.px(hand), now), now

    def update(self, hand, now):
        x, y = self.filter(self.px(hand), now)
        dx, dy = self.pos[0] - x, y - self.pos[1]  # mirrored: hand to your right = pointer right
        dt, self.pos, self.t = max(now - self.t, 1e-3), (x, y), now
        speed = math.hypot(dx, dy) / self.frame[0] / dt
        f = min(max((speed - self.slow_speed) / (self.fast_speed - self.slow_speed), 0.0), 1.0)
        gain = (self.slow_gain + f * (self.fast_gain - self.slow_gain)) / self.frame[0]
        return dx * gain, dy * gain
