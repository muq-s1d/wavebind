# wavebind
Bind webcam hand gestures to desktop actions. Linux first (Wayland-native), Windows and macOS later.

Everything runs on your machine. No video leaves it, there is no telemetry, and after the one-time
`wavebind setup` it never touches the network.

> Status: early. Setup, environment check and the action runner work; the gesture loop is next.

## Install
Needs Python 3.11+ on a glibc distro (MediaPipe has no musl/Alpine wheels).

```sh
git clone https://github.com/muq-s1d/wavebind && cd wavebind
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/wavebind setup    # one-time 8.4 MB model download, checksum verified
.venv/bin/wavebind check    # model, camera, key backend
```

## Config
Defaults live in [`wavebind/default.toml`](wavebind/default.toml). To customize, copy it to
`~/.config/wavebind/config.toml`. Each gesture binds to exactly one of:

```toml
[bindings]
Thumb_Up = { cmd = "wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ 5%+" }  # run a command (no shell)
Closed_Fist = { media = "PlayPause" }   # PlayPause / Next / Previous / Stop via MPRIS
swipe_right = { keys = "super+Page_Down" }  # press a key combo
```

Test a binding without the camera: `wavebind fire Thumb_Up`.

## Permissions
`cmd` and `media` bindings need no special permission. Only `keys` bindings press keys, and wavebind
asks for that the first time one fires, never earlier:

- **GNOME / KDE:** the desktop shows a "Remote Desktop" dialog. Despite the name, nothing goes over the
  network and nothing can see your screen: wavebind asks for the keyboard only, through the local
  xdg-desktop-portal. GNOME shows an indicator in the top bar while it is active.
- `remember_key_permission = false` (default): you are asked every launch and nothing is stored.
- `remember_key_permission = true`: the grant is saved to `~/.local/share/wavebind/portal_token`
  (mode 0600) so later launches don't ask. Delete that file to make wavebind ask again.

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
