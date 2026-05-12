# Voicium Ubuntu Advanced Architecture

## 1. Product Definition

Voicium is a local-first Ubuntu dictation daemon for Russian push-to-talk voice input.

The MVP must feel like a system-level productivity tool, not a recorder app:

1. Hold a configured key.
2. Speak Russian.
3. Release the key.
4. Text appears in the focused field as fast as possible.

Primary target:

- Ubuntu 24.04 LTS;
- GNOME Wayland;
- NVIDIA GPU acceleration when the driver works;
- CPU fallback when GPU is unavailable;
- local `whisper.cpp` transcription;
- optional OpenAI-compatible text enhancement.

The architecture must avoid two common Linux desktop traps:

- relying on Wayland APIs that cannot provide global key up/down reliably;
- making CUDA a hard install-time dependency.

## 2. Architecture Principles

### 2.1 Local-first core

The critical path must work offline:

```text
input key -> mic audio -> local STT -> clipboard/paste
```

Network calls are optional and never required for insertion.

### 2.2 Fast path is separate from nice-to-have path

The path from key release to inserted text should do only necessary work.

Critical path:

- stop audio capture;
- run local transcription;
- apply deterministic Russian post-processing;
- paste/copy result.

Non-critical path:

- history persistence;
- metrics;
- notifications;
- debug logs;
- optional AI enhancement if enabled and within timeout.

### 2.3 Runtime feature detection

Ubuntu desktop and GPU state vary by machine. The app should detect capabilities at runtime:

- session type;
- compositor/desktop;
- clipboard backend;
- paste backend;
- input permissions;
- NVIDIA driver;
- CUDA binary availability;
- model availability.

No silent degradation. Every fallback must be explainable by `voicium healthcheck`.

### 2.4 Daemon-first UX

The product is a background user service. CLI and UI are control surfaces, not the core runtime.

Main process:

```text
systemd --user voicium.service -> voicium daemon
```

CLI commands talk to the daemon when they affect runtime state.

### 2.5 Strict module boundaries

Desktop integration, audio, transcription, and enhancement should be isolated. This keeps Wayland, CUDA, and API-provider complexity out of the state machine.

## 3. Process Model

### 3.1 Main processes

```text
voicium daemon
  long-lived user process
  owns hotkey listener, recording state, transcription queue, paste flow

voicium CLI
  short-lived command process
  controls daemon, runs healthcheck, downloads models, transcribes files

whisper.cpp worker
  external binary or persistent subprocess
  CPU or CUDA backend

ydotoold
  optional external daemon for Wayland paste automation
```

### 3.2 Recommended MVP process layout

For MVP, use a single Python daemon and an external `whisper.cpp` binary.

```text
voicium daemon
  |-- EvdevHotkeyListener
  |-- AudioRecorder
  |-- TranscriptionRunner
  |-- PasteManager
  |-- HistoryWriter
  |-- NotificationManager
```

Avoid building a separate internal microservice layer in MVP. Keep IPC minimal.

### 3.3 CLI-to-daemon communication

Use a Unix domain socket in the user runtime directory:

```text
$XDG_RUNTIME_DIR/voicium/daemon.sock
```

Initial commands:

```text
start_recording
stop_recording
toggle_recording
status
reload_config
shutdown
```

Why socket instead of signals/files:

- structured responses;
- easier status/debug;
- supports future tray UI;
- no dependency on D-Bus knowledge for MVP.

D-Bus can be added later for desktop integration.

## 4. Component Map

```text
src/voicium/
  cli.py
  config.py
  healthcheck.py
  daemon/
    service.py
    state.py
    ipc.py
    events.py
  hotkey/
    evdev_listener.py
    keymap.py
  audio/
    recorder.py
    device.py
    resample.py
    vad.py
  transcription/
    whisper_cpp.py
    model_manager.py
    backend_selector.py
    postprocess_ru.py
  paste/
    manager.py
    clipboard.py
    wayland.py
    x11.py
  tray/
    indicator.py
    settings_menu.py
  enhancement/
    openai_compatible.py
    prompts.py
  storage/
    history.py
    metrics.py
  desktop/
    notify.py
    session.py
  runtime/
    paths.py
    commands.py
    locking.py
```

## 5. Data Flow

### 5.1 Dictation flow

```text
Evdev key down
  -> Daemon receives HotkeyPressed
  -> State: idle -> recording
  -> AudioRecorder.start()
  -> Optional start sound/notification

Evdev key up
  -> Daemon receives HotkeyReleased
  -> AudioRecorder.stop()
  -> State: recording -> processing
  -> Audio buffer validated
  -> TranscriptionRunner.transcribe(audio, lang="ru")
  -> RussianPostProcessor.apply(raw_text)
  -> OptionalEnhancer.enhance(text) if enabled
  -> PasteManager.insert_or_copy(final_text)
  -> HistoryWriter.persist_async(...)
  -> State: processing -> idle
```

### 5.2 Failure flow

```text
Any step fails
  -> classify error
  -> choose fallback if available
  -> notify user if actionable
  -> record diagnostic event
  -> return daemon to idle
```

Failure classes:

- `InputPermissionError`;
- `AudioDeviceError`;
- `RecordingTooShort`;
- `TranscriptionBackendUnavailable`;
- `ModelMissing`;
- `PasteUnavailable`;
- `EnhancementTimeout`;
- `EnhancementProviderError`.

### 5.3 Enhancement flow

Enhancement is optional and must never make dictation unusable.

```text
postprocessed_text
  -> if enhancement disabled: return postprocessed_text
  -> if enabled: call OpenAI-compatible endpoint with timeout
  -> if success: return enhanced_text
  -> if failure: return postprocessed_text and warn
```

MVP default timeout: 3-5 seconds for interactive dictation. Longer timeouts belong to a future non-interactive mode.

## 6. Daemon State Machine

```text
idle
  on key_down -> recording
  on cli_start -> recording

recording
  on key_up -> processing
  on cli_stop -> processing
  on max_duration -> processing
  on audio_error -> error -> idle

processing
  on transcription_success -> inserting
  on transcription_error -> error -> idle

inserting
  on paste_success -> idle
  on paste_fallback_copy -> idle
  on paste_error -> error -> idle

error
  emit diagnostic
  cleanup resources
  -> idle
```

Rules:

- only one active recording at a time;
- key repeat is ignored;
- key up without active recording is ignored;
- recording below minimum duration is discarded;
- daemon must release audio resources after every attempt;
- state transitions must be logged without leaking full transcript text by default.

## 7. Hotkey Architecture

### 7.1 Why evdev

GNOME Wayland does not provide universal global key down/up APIs for arbitrary apps. Push-to-talk requires both down and up events. Therefore MVP uses Linux input devices via `evdev`.

Pros:

- works under Wayland;
- provides real key down/up;
- independent of compositor shortcuts.

Cons:

- requires input device permissions;
- must handle multiple keyboards;
- key events are low-level.

### 7.2 Hotkey listener

Responsibilities:

- enumerate keyboard-like `/dev/input/event*` devices;
- filter configured key code;
- ignore repeats;
- emit `HotkeyPressed` and `HotkeyReleased` events;
- reconnect if keyboard device disappears;
- expose permission errors to healthcheck.

### 7.3 Permission model

MVP simple path:

```bash
sudo usermod -aG input "$USER"
```

Better post-MVP path:

- udev rule for selected input devices;
- local group with narrower access;
- setup helper that validates only required permissions.

Healthcheck must not merely say `permission denied`; it must print the exact event device and remediation.

## 8. Audio Architecture

### 8.1 Capture requirements

- mono output;
- 16 kHz target sample rate;
- 16-bit PCM or float32 internal buffer;
- no persisted audio by default;
- support default microphone and explicit input device;
- short startup latency.

### 8.2 Recorder interface

```python
class AudioRecorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> AudioClip: ...
    def is_recording(self) -> bool: ...
```

`AudioClip`:

```python
@dataclass(frozen=True)
class AudioClip:
    samples: bytes
    sample_rate: int
    channels: int
    duration_ms: int
```

### 8.3 MVP backend

Use a PortAudio-compatible Python library first. If latency or device reliability is insufficient, replace with a PipeWire-native backend later without changing daemon state logic.

### 8.4 Validation

Before transcription:

- duration >= configured minimum;
- duration <= configured maximum;
- buffer not empty;
- optional RMS threshold to catch muted microphone.

## 9. Transcription Architecture

### 9.1 Backend selector

Inputs:

- config backend: `auto`, `cpu`, `cuda`;
- available binaries;
- `nvidia-smi` status;
- CUDA smoke-test result;
- model availability.

Output:

```python
@dataclass(frozen=True)
class SelectedBackend:
    name: Literal["cpu", "cuda"]
    binary_path: Path
    reason: str
    warnings: tuple[str, ...]
```

### 9.2 Whisper runner

Initial MVP can invoke `whisper.cpp` CLI per transcription. If this is too slow, optimize to persistent worker.

CLI-per-call advantages:

- simpler;
- easier crash isolation;
- easier logs;
- good enough for first validation.

Persistent worker advantages:

- avoids model reload;
- much faster repeated dictation;
- better UX.

Recommended MVP implementation path:

1. Implement CLI-per-call runner.
2. Measure latency.
3. Add persistent worker before MVP release if model reload dominates.

### 9.3 Model profiles

```text
fast
  ggml-small-q5_1
  CPU-friendly

balanced
  ggml-medium-q5_0
  default

accurate
  ggml-large-v3-turbo-q5_0
  GPU-preferred
```

The model manager owns:

- list profiles;
- check local files;
- download models;
- verify checksums;
- expose model path to runner.

### 9.4 Russian defaults

Whisper options:

```text
-l ru
--no-translate
--print-progress false
--print-timestamps false
```

Do not run language detection in MVP.

## 10. NVIDIA CUDA Architecture

CUDA is a runtime capability, not a package requirement.

### 10.1 Detection layers

```text
Layer 1: nvidia-smi exists
Layer 2: nvidia-smi returns GPU data
Layer 3: CUDA whisper binary exists
Layer 4: CUDA smoke-test succeeds
Layer 5: selected model fits VRAM heuristically
```

### 10.2 Failure handling

If any layer fails in `auto` mode:

- select CPU;
- store warning in healthcheck diagnostics;
- continue normal operation.

If `backend=cuda` explicitly and CUDA fails:

- fail with clear error;
- suggest `backend=auto` or driver fix.

### 10.3 Current local expectation

On the checked machine, `nvidia-smi` fails, so expected result is:

```text
NVIDIA: unavailable
Reason: nvidia-smi cannot communicate with NVIDIA driver
Selected backend: cpu
```

## 11. Paste Architecture

### 11.1 Paste manager API

```python
class PasteManager(Protocol):
    def insert_or_copy(self, text: str) -> PasteResult: ...
```

`PasteResult`:

```python
@dataclass(frozen=True)
class PasteResult:
    mode: Literal["pasted", "copied", "failed"]
    message: str
```

### 11.2 Backend selection

```text
if XDG_SESSION_TYPE == "wayland":
  use WaylandClipboard + YdotoolPaste if available
elif XDG_SESSION_TYPE == "x11":
  use X11Clipboard + XdotoolPaste
else:
  clipboard-only fallback
```

### 11.3 Wayland strategy

Wayland path:

1. copy text via `wl-copy`;
2. attempt paste via `ydotool`;
3. if paste fails, leave text in clipboard;
4. notify user.

Wayland must be treated as best-effort for synthetic paste.

### 11.4 X11 strategy

X11 path:

1. snapshot clipboard if restoration enabled;
2. copy text;
3. run `xdotool key ctrl+v`;
4. restore clipboard after configured delay.

## 12. OpenAI-Compatible Enhancement Architecture

### 12.1 Provider contract

```python
class TextEnhancer(Protocol):
    async def enhance(self, text: str, mode: EnhancementMode) -> str: ...
```

### 12.2 API request

Use OpenAI-compatible chat completions:

```http
POST {base_url}/chat/completions
Authorization: Bearer {api_key}
```

### 12.3 Prompt constraints

The prompt must instruct the model to:

- preserve meaning;
- not add facts;
- keep Russian language;
- fix punctuation and obvious recognition errors;
- return only final text.

### 12.4 Timeout and fallback

Interactive dictation cannot wait indefinitely.

Default MVP behavior:

- enhancement timeout: 3-5 seconds;
- if timeout/error: paste local postprocessed text;
- log provider error without transcript by default.

## 13. Storage Architecture

### 13.1 Config

Config path:

```text
~/.config/voicium/config.toml
```

Config should be loaded once at daemon start and reloadable via CLI.

### 13.2 History

SQLite path:

```text
~/.local/share/voicium/history.sqlite
```

Minimum schema:

```sql
CREATE TABLE transcriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  text TEXT NOT NULL,
  raw_text TEXT,
  duration_ms INTEGER,
  inference_ms INTEGER,
  model TEXT,
  backend TEXT,
  pasted INTEGER NOT NULL DEFAULT 0
);
```

History writes should not block insertion. Use fire-and-forget with error logging.

### 13.3 Metrics

Store performance metrics separately or inside history:

- recording duration;
- preprocessing duration;
- inference duration;
- enhancement duration;
- paste mode;
- backend;
- model.

These metrics are required to optimize perceived speed.

## 14. Healthcheck Architecture

`voicium healthcheck` is part of the product, not a debug afterthought.

Checks:

- OS version;
- session type;
- desktop environment;
- audio devices;
- microphone access;
- input permissions;
- configured hotkey device availability;
- clipboard tools;
- paste tools;
- notification tools;
- `whisper.cpp` CPU binary;
- `whisper.cpp` CUDA binary;
- model files;
- NVIDIA driver/GPU/CUDA;
- OpenAI-compatible settings if enabled;
- daemon status;
- systemd user service status.

Output levels:

```text
OK
WARN
FAIL
SKIP
```

Every `FAIL` should include an actionable fix.

## 15. GitLab Build Architecture

### 15.1 CI stages

```text
lint
test
build
package
gpu
```

### 15.2 Required jobs

```text
ruff-check
ruff-format-check
pytest
build-wheel
build-whisper-cpu
package-deb
```

### 15.3 Optional GPU job

```text
gpu-smoke-test
  tags: [nvidia]
  when: manual
```

Normal CI must not require an NVIDIA runner.

### 15.4 Artifact strategy

Artifacts:

- Python wheel;
- `.deb` package;
- SHA256 checksums;
- test report;
- coverage report.

Models should not be bundled into the package. They are downloaded by `voicium models download`.

## 16. Packaging Architecture

MVP package type:

- `.deb` for Ubuntu 24.04.

Installed files:

```text
/usr/bin/voicium
/usr/lib/voicium/...
/usr/share/applications/voicium.desktop optional
/usr/share/doc/voicium/...
```

User service:

```text
~/.config/systemd/user/voicium.service
```

or package-provided service copied/enabled by install instructions.

The `.deb` must install without CUDA.

## 17. Security and Privacy

### 17.1 Sensitive data

Sensitive:

- audio buffers;
- transcript text;
- OpenAI-compatible API key;
- history database;
- logs if debug mode includes text.

Rules:

- do not store audio by default;
- do not log full text by default;
- do not store API key in plain config by default;
- prefer API key from environment variable;
- make history opt-in or clearly documented.

### 17.2 Input access

Evdev access is powerful. MVP may use `input` group for speed, but docs must explain the tradeoff.

Post-MVP should implement narrower udev permissions.

## 18. MVP Development Plan

### Phase 0: Repository Foundation

Goal: make the repository buildable and CI-controlled.

Tasks:

- create Python `src/` project;
- configure `uv`;
- configure `ruff`;
- configure `pytest`;
- add typed config module;
- add CLI skeleton;
- add GitLab CI;
- add README build instructions.

Acceptance:

- `uv sync` works;
- `uv run ruff check` passes;
- `uv run pytest` passes;
- GitLab pipeline runs;
- `voicium --help` works.

### Phase 1: Healthcheck First

Goal: turn environment complexity into visible diagnostics before implementing features.

Tasks:

- detect Ubuntu version;
- detect GNOME/Wayland/X11;
- detect NVIDIA via `nvidia-smi`;
- detect clipboard/paste tools;
- detect input permissions;
- detect audio devices;
- print actionable output.

Acceptance:

- current local machine reports GNOME Wayland;
- current local machine reports NVIDIA unavailable with exact reason;
- missing tools are reported as WARN/FAIL with install hints.

### Phase 2: Whisper CPU Path

Goal: transcribe Russian WAV files locally.

Tasks:

- add model profile definitions;
- add model downloader;
- add whisper.cpp CPU build script or binary discovery;
- implement `voicium transcribe`;
- add Russian flags;
- add fixture-based tests for command construction.

Acceptance:

- `voicium transcribe sample.wav --lang ru` returns text;
- missing model produces actionable error;
- CPU backend works without NVIDIA.

### Phase 3: Audio Recording

Goal: record from microphone and transcribe one utterance.

Tasks:

- list audio input devices;
- implement recorder;
- write WAV or in-memory clip;
- validate duration;
- implement `voicium record`;
- implement `voicium record-transcribe`.

Acceptance:

- user can record 5 seconds from default mic;
- recorded audio can be transcribed;
- no audio is retained unless explicitly requested.

### Phase 4: CUDA Backend

Goal: use NVIDIA GPU when available and fall back to CPU otherwise.

Tasks:

- implement backend selector;
- implement `nvidia-smi` parser;
- add CUDA binary build/discovery;
- add CUDA smoke-test command;
- add explicit `--backend cpu|cuda|auto`.

Acceptance:

- on current machine, `auto` chooses CPU and reports broken NVIDIA driver;
- on configured NVIDIA machine, `auto` chooses CUDA;
- `backend=cuda` fails clearly if CUDA unavailable.

### Phase 5: Daemon and Push-To-Talk

Goal: hold key to record, release key to transcribe.

Tasks:

- implement daemon loop;
- implement Unix socket IPC;
- implement evdev listener;
- implement state machine;
- implement systemd user service;
- add `voicium start`, `stop`, `status`.

Acceptance:

- configured key down starts recording;
- key up stops recording;
- daemon transcribes after release;
- daemon returns to idle after success/failure.

### Phase 6: Clipboard and Paste

Goal: insert text or copy fallback.

Tasks:

- implement session-aware paste backend selection;
- implement Wayland clipboard via `wl-copy`;
- implement Wayland paste via `ydotool`;
- implement X11 paste via `xdotool`;
- implement clipboard-only fallback;
- implement notifications.

Acceptance:

- on supported setup, text appears in focused field;
- if paste unavailable, text remains in clipboard;
- user gets a clear notification.

### Phase 7: Russian Quality Layer

Goal: make Russian dictation usable for real messages.

Tasks:

- implement punctuation commands;
- implement replacement dictionary;
- add config for custom replacements;
- add tests for Russian post-processing;
- add history database.

Acceptance:

- common punctuation phrases are converted;
- replacement dictionary works;
- history CLI can list/copy/repeat entries.

### Phase 8: OpenAI-Compatible Enhancement

Goal: optional text cleanup via compatible API.

Status: deferred for the first release candidate. Local transcription and insertion remain the release-critical path.

Tasks:

- implement provider client;
- implement prompt templates;
- add config;
- add timeout/fallback;
- add tests with mocked HTTP.

Acceptance:

- enhancement works when enabled;
- API failure still inserts local transcription;
- API key is not required for default local mode.

### Phase 8.5: Tray Settings and Runtime Modes

Goal: make core runtime settings discoverable from the daemon status icon.

Tasks:

- implement AppIndicator menu opened from the daemon icon;
- add menu item for hotkey settings;
- add menu item for transcription mode settings;
- persist selected settings to `~/.config/voicium/config.toml`;
- add config reload path from tray settings to daemon runtime;
- implement hotkey rebinding without daemon restart where possible;
- implement transcription runtime mode selection:
  - `quality`: Transformers Russian model, best quality, slower startup/inference;
  - `fast`: `whisper.cpp` `fast` profile, lower latency, lower quality;
  - `balanced`: `whisper.cpp` `balanced` profile, medium latency/quality;
- ensure model download errors for selected mode are actionable;
- add tests for config persistence, mode selection, and daemon reload behavior.

Acceptance:

- clicking the daemon icon opens a menu;
- menu exposes Hotkey and Transcription Mode entries;
- user can choose between Transformers quality mode and whisper.cpp fast/balanced modes;
- changed hotkey is used for the next push-to-talk attempt;
- changed transcription mode affects the next transcription without losing clipboard fallback;
- healthcheck reports missing tools/models for the selected mode.

### Phase 9: Packaging and Release Candidate

Goal: installable MVP.

Tasks:

- build `.deb`;
- add systemd user service installation docs;
- add model download docs;
- add GPU troubleshooting docs;
- add Wayland troubleshooting docs;
- document tray settings, hotkey setup, and transcription mode tradeoffs;
- add release checklist.

Current RC scope note: live hotkey rebinding without daemon restart is deferred. The selected hotkey is persisted immediately, but the evdev listener applies it after daemon restart.

Acceptance:

- clean Ubuntu 24.04 install can install package;
- `voicium healthcheck` works after install;
- user can complete push-to-talk dictation after documented setup.

## 19. MVP Release Gate

Do not call it MVP until all are true:

- source builds in GitLab CI;
- package artifact is produced;
- local Russian transcription works offline;
- push-to-talk hold mode works;
- GNOME Wayland has a documented working path;
- NVIDIA CUDA works on a configured NVIDIA machine;
- current broken NVIDIA state falls back to CPU with clear diagnostics;
- paste fallback never loses recognized text;
- OpenAI-compatible enhancement is optional and safe to disable;
- healthcheck explains common setup problems.

## 20. Post-MVP Architecture Upgrades

Likely next improvements:

- persistent Whisper worker to avoid model reload;
- small tray UI;
- native PipeWire audio backend;
- narrower udev input permissions;
- browser extension for URL/context;
- local LLM punctuation mode via Ollama;
- streaming partial transcription;
- VAD auto-stop option;
- Flatpak/AppImage packaging;
- D-Bus control API;
- per-application profiles.
