# wavebind
Bind webcam hand gestures to desktop actions. Linux first (Wayland-native), Windows and macOS later.

Everything runs on your machine. No video leaves it, there is no telemetry, and after the one-time
`wavebind setup` it never touches the network.

> Status: early. Built-in gestures and swipes work; recording your own gestures is next.

## Install
Needs Python 3.11+ on a glibc distro (MediaPipe has no musl/Alpine wheels).

```sh
git clone https://github.com/muq-s1d/wavebind && cd wavebind
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/wavebind setup    # one-time 8.4 MB model download, checksum verified
.venv/bin/wavebind check    # model, camera, key backend
```

## Use
```sh
wavebind run --preview   # camera window with landmarks and state; q quits
wavebind run             # headless; Ctrl+C quits
```

1. **Arm:** hold an open palm for 0.5 s (preview border turns green).
2. **Act:** make a gesture (held for 3 frames) or swipe your open palm left/right. Chain as many as
   you like; it disarms after 3 s without one. Nothing fires unless armed.
3. Holding a gesture fires it once; change hand shape to fire it again. After a swipe, swipes pause
   for 1 s so moving your hand back doesn't count as the opposite swipe.

Default bindings (GNOME shortcuts; all changeable in the config):

| Gesture (after arming) | Does |
|---|---|
| swipe left / right | next / previous workspace (phone-style) |
| swipe up / down | overview / minimize window |
| ✊ `Closed_Fist` | play / pause |
| 👍 `Thumb_Up` / 👎 `Thumb_Down` | volume up / down, repeats while held |
| ☝ `Pointing_Up` | maximize / restore |
| ✌ `Victory` | screenshot tool |
| 🤟 `ILoveYou` | show desktop |
| 🤏 pinch + move | drag the window under the mouse pointer; open your fingers to drop |

`Open_Palm` is the arm gesture.

CPU, measured on a 12-core laptop: about 25% of one core while no hand is in view (`idle_fps = 5`),
more while tracking a hand (`fps = 15`). Lower either in the config to trade reaction time for CPU.

## Config
Defaults live in [`wavebind/default.toml`](wavebind/default.toml). Put your changes in
`~/.config/wavebind/config.toml`; it is merged on top of the defaults, so it only needs what you
change. `Victory = false` under `[bindings]` unbinds a default; a top-level `pinch = false` turns
pinch-drag off. Each gesture binds to exactly one of:

```toml
[bindings]
Thumb_Up = { cmd = "wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+", repeat = 0.3 }  # command, no shell; repeats while held
Closed_Fist = { media = "PlayPause" }   # PlayPause / Next / Previous / Stop via MPRIS
swipe_right = { keys = "super+Page_Down" }  # press a key combo
```

Test a binding without the camera: `wavebind fire Thumb_Up`.

## Permissions
`cmd` and `media` bindings need no special permission. Only `keys` bindings press keys, and wavebind
asks for that the first time one fires, never earlier. The defaults use keys for swipes and for volume
(so GNOME shows its volume popup); swap volume to the `wpctl` command above to avoid key permission
for it.

- **GNOME / KDE:** the desktop shows a "Remote Desktop" dialog. Despite the name, nothing goes over the
  network and nothing can see your screen: wavebind asks for the keyboard, plus the mouse pointer only
  if the `[pinch]` section is in your config, through the local xdg-desktop-portal. GNOME shows an
  indicator in the top bar while it is active.
- `remember_key_permission = true` (default): allow once. The grant is saved to
  `~/.local/share/wavebind/portal_token` (readable only by you) so later launches don't ask.
  Delete that file to make wavebind ask again.
- `remember_key_permission = false`: you are asked every launch and nothing is stored.

`wavebind check --type` sends one Shift press to test this.

## How keys get pressed
| Session | Backend | Desktops |
|---|---|---|
| Wayland | xdg-desktop-portal RemoteDesktop | GNOME, KDE Plasma |
| Wayland, no portal | `wtype` | Sway, Hyprland, other wlroots |
| X11 | `xdotool` | any |

## Development
```sh
.venv/bin/python -m unittest discover -s tests
```

## Model
The gesture model is Google's MediaPipe `gesture_recognizer.task`, downloaded from Google by
`wavebind setup` and not redistributed here.
