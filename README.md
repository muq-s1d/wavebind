# wavebind
Bind webcam hand gestures to desktop actions. Linux first (Wayland-native), Windows and macOS later.

Everything runs on your machine. No video leaves it, there is no telemetry, and after the one-time
`wavebind setup` it never touches the network.

> Status: early. Milestone 1 (setup + environment check) works; the gesture loop is next.

## Install
Needs Python 3.11+ on a glibc distro (MediaPipe has no musl/Alpine wheels).

```sh
git clone https://github.com/muq-s1d/wavebind && cd wavebind
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/wavebind setup    # one-time 8.4 MB model download, checksum verified
.venv/bin/wavebind check    # model, camera, key backend
```

`wavebind check --type` also sends one Shift press. On GNOME and KDE the desktop asks permission the
first time; wavebind remembers the grant, so later runs don't ask again.

## How keys get pressed
| Session | Backend | Desktops |
|---|---|---|
| Wayland | xdg-desktop-portal RemoteDesktop | GNOME, KDE Plasma |
| Wayland, no portal | `wtype` | Sway, Hyprland, other wlroots |
| X11 | `xdotool` | any |

While the portal session is open, GNOME shows a remote-control indicator in the top bar.

## Development
```sh
.venv/bin/python -m unittest discover -s tests
```

## Model
The gesture model is Google's MediaPipe `gesture_recognizer.task`, downloaded from Google by
`wavebind setup` and not redistributed here.
