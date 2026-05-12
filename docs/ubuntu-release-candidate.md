# Voicium Ubuntu Release Candidate

## Build Package

Build the Python artifacts and Debian package from a clean checkout:

```bash
uv sync --frozen
uv run ruff check
uv run ruff format --check
uv run pytest
uv build
./scripts/build-deb.sh
```

The Debian artifact is written to `dist/deb/voicium_0.1.0_all.deb`.

## Install Package

Install the generated package on Ubuntu 24.04:

```bash
sudo apt install ./dist/deb/voicium_0.1.0_all.deb
voicium healthcheck
```

The package installs:

- CLI wrapper: `/usr/bin/voicium`;
- isolated Python runtime: `/opt/voicium`;
- systemd user unit: `/usr/lib/systemd/user/voicium.service`;
- documentation: `/usr/share/doc/voicium`.

## Enable User Service

Start the daemon for the current user:

```bash
systemctl --user daemon-reload
systemctl --user enable --now voicium.service
systemctl --user status voicium.service
```

If the service should start after login without an open terminal session, enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

## Input Permissions

Push-to-talk uses `evdev` for global key press/release events. On the MVP path the user must be able to read input devices.

Fast setup:

```bash
sudo usermod -aG input "$USER"
```

Log out and log back in after changing groups. This grants broad access to input devices; narrower udev rules are a post-MVP hardening task.

## Model Download

Quality mode uses the Russian Transformers profile:

```bash
voicium models download russian
```

Whisper.cpp runtime modes use GGML models:

```bash
voicium models download fast
voicium models download balanced
```

Models are stored under `~/.local/share/voicium/models` by default.

## Runtime Modes

Voicium supports three local runtime modes:

- `quality`: Russian Transformers model, best quality, heavier startup and inference;
- `fast`: whisper.cpp small quantized model, lower latency and lower quality;
- `balanced`: whisper.cpp medium quantized model, middle ground.

The tray menu can persist runtime mode changes to `~/.config/voicium/config.toml`. The next transcription uses the selected mode.

## Hotkey Setup

Default hotkey is `KEY_RIGHTCTRL`. Change it from the tray menu or config:

```toml
[hotkey]
backend = "evdev"
key = "KEY_F8"
```

Current MVP limitation: hotkey listener rebinding requires daemon restart after changing the key:

```bash
systemctl --user restart voicium.service
```

## Wayland Troubleshooting

GNOME Wayland limits global hotkeys and synthetic key presses. Voicium uses `evdev` for key events and clipboard-first insertion for reliability.

Install recommended tools:

```bash
sudo apt install wl-clipboard ydotool libnotify-bin
```

Expected behavior:

- if `wl-copy` is available, recognized text is copied to clipboard;
- if `ydotool` is configured, Voicium can attempt `Ctrl+V` auto-paste;
- if auto-paste fails, text remains in clipboard and a notification explains the fallback.

Run diagnostics:

```bash
voicium healthcheck
```

## GPU Troubleshooting

CUDA is optional. CPU fallback must continue to work when NVIDIA is unavailable.

Check driver state:

```bash
nvidia-smi
voicium backend select --backend auto
voicium backend cuda-smoke-test
```

Expected fallback on a broken NVIDIA setup:

- `backend auto` selects CPU or non-CUDA local mode;
- diagnostics include the exact `nvidia-smi` failure;
- `backend cuda` fails clearly instead of silently using CPU.

## Release Checklist


- `uv sync --frozen` passes;
- `uv run ruff check` passes;
- `uv run ruff format --check` passes;
- `uv run pytest` passes;
- `uv build` produces wheel and sdist;
- `./scripts/build-deb.sh` produces `dist/deb/voicium_0.1.0_all.deb`;
- clean Ubuntu 24.04 can install the `.deb` with `sudo apt install ./dist/deb/voicium_0.1.0_all.deb`;
- `voicium healthcheck` works after install;
- `voicium models download russian` succeeds or returns an actionable error;
- `voicium record-transcribe --duration 5` works with the default microphone;
- daemon starts with `systemctl --user enable --now voicium.service`;
- push-to-talk key down starts recording and key up starts transcription;
- recognized text is inserted or remains in clipboard if paste is unavailable;
- GNOME Wayland setup path is documented;
- NVIDIA unavailable state falls back with clear diagnostics;
- OpenAI-compatible enhancement is explicitly deferred.
