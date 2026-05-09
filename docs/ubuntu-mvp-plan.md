# Voicium Ubuntu MVP Plan

## 1. Цель MVP

Сделать GitLab-репозиторий с исходниками приложения `voicium`, из которого полностью собирается Ubuntu-приложение для быстрой русской диктовки.

Основной пользовательский сценарий:

1. Пользователь удерживает push-to-talk клавишу.
2. Приложение сразу начинает запись микрофона.
3. Пользователь говорит фразу на русском.
4. Пользователь отпускает клавишу.
5. Приложение локально распознает речь через Whisper.
6. Приложение вставляет текст в активное поле ввода.
7. Если автопаста невозможна, текст остается в clipboard и показывается понятная диагностика.

Фокус MVP:

- очень быстрый UX;
- русский язык;
- локальная транскрибация;
- NVIDIA CUDA при доступности;
- CPU fallback;
- GNOME Wayland как основной target;
- сборка из GitLab-исходников;
- OpenAI-compatible enhancement как опция, выключенная по умолчанию.

## 2. Текущая целевая среда

Проверенная локальная среда:

- OS: Ubuntu 24.04.4 LTS;
- kernel: Linux 6.8;
- desktop: GNOME;
- session: Wayland;
- display variables: `WAYLAND_DISPLAY=wayland-0`, `DISPLAY=:0`;
- GPU vendor: NVIDIA по требованию пользователя;
- `nvidia-smi`: не работает на момент проверки, ошибка `couldn't communicate with the NVIDIA driver`.

Следствия для MVP:

- нельзя предполагать, что CUDA работает;
- healthcheck должен явно показывать состояние NVIDIA-драйвера;
- приложение обязано работать на CPU fallback;
- Wayland-интеграция должна иметь fallback, потому что GNOME Wayland ограничивает глобальные хоткеи и эмуляцию клавиш.

## 3. Что входит в MVP

Обязательно:

- GitLab-ready репозиторий;
- воспроизводимая сборка из исходников;
- GitLab CI pipeline;
- CLI `voicium`;
- user daemon для push-to-talk;
- запись микрофона;
- локальная транскрибация русской речи через `whisper.cpp`;
- фиксированный язык `ru`, без autodetect по умолчанию;
- NVIDIA CUDA backend при доступности;
- CPU fallback;
- clipboard integration;
- попытка auto-paste;
- graceful fallback в clipboard-only mode;
- история транскрипций в SQLite;
- русский post-processing;
- пользовательские замены слов;
- OpenAI-compatible enhancement как опция;
- `.deb` пакет;
- systemd user service;
- healthcheck CLI.

Не входит в MVP:

- полноценный аналог VoiceInk Power Mode;
- browser URL detection;
- чтение контекста экрана;
- AI assistant/chat mode;
- идеальная поддержка всех Linux DE/WM;
- Flatpak;
- автообновления;
- полноценный графический интерфейс уровня VoiceInk;
- синхронизация истории;
- хранение аудио по умолчанию;
- AMD ROCm;
- Intel GPU acceleration.

## 4. Ключевые продуктовые требования

### 4.1 Скорость

Целевые показатели:

- старт записи после key down: до 100 ms;
- stop recording после key up: до 100 ms;
- транскрибация 5 секунд русской речи на NVIDIA GPU: до 1-2 секунд;
- транскрибация 5 секунд русской речи на CPU: приемлемый fallback, целевой диапазон 2-6 секунд в зависимости от модели;
- вставка после готового текста: до 100 ms;
- приложение не должно блокировать UI/daemon во время inference.

Технические требования для скорости:

- держать daemon постоянно запущенным;
- prewarm модели при старте daemon, если включен performance mode;
- не делать language autodetect;
- использовать `-l ru`;
- использовать persistent worker или минимизировать запуск тяжелых процессов;
- не сохранять аудио на диск без необходимости;
- использовать короткий pipeline `record -> transcribe -> postprocess -> paste`;
- fallback API enhancement не должен блокировать вставку, если timeout превышен.

### 4.2 Удобство

Минимально удобный UX:

- пользователь один раз настраивает push-to-talk клавишу;
- дальше диктовка работает без окон и подтверждений;
- если auto-paste невозможна, текст все равно оказывается в clipboard;
- ошибки показываются понятным сообщением, а не только stack trace;
- `voicium healthcheck` объясняет, что нужно установить или настроить;
- история позволяет вернуть последнюю транскрипцию.

### 4.3 Приватность

Default behavior:

- транскрибация локальная;
- аудио не сохраняется;
- OpenAI-compatible enhancement выключен;
- текстовая история включается явно или сопровождается понятным предупреждением;
- логи не содержат полный текст транскрипций по умолчанию.

## 5. Рекомендуемый стек

### 5.1 Язык и tooling

Backend MVP:

- Python;
- `src/` layout;
- `uv` как менеджер зависимостей;
- `ruff` для lint/format;
- `pytest` для тестов;
- строгая типизация публичных интерфейсов;
- `mypy` можно добавить после базового MVP, но типы в публичных API писать сразу.

Причины выбора Python для MVP:

- быстрее собрать рабочую версию;
- проще системная интеграция через subprocess;
- проще healthcheck;
- проще CI и packaging;
- проще итерации по аудио/STT pipeline.

Позже можно вынести latency-critical части в Rust.

### 5.2 Основные зависимости

Предварительный набор:

- `typer` для CLI;
- `pydantic-settings` для конфигурации;
- `sounddevice` или другой PortAudio/PipeWire-compatible слой для аудио;
- `numpy` для аудио буферов;
- `httpx` для OpenAI-compatible API;
- `sqlite-utils` или прямой `sqlite3` для истории;
- `evdev` для push-to-talk input listener;
- `rich` для читаемого CLI output;
- `platformdirs` для путей конфигурации/данных.

Системные инструменты:

- `whisper.cpp` binary;
- `wl-clipboard` для Wayland clipboard;
- `ydotool` для Wayland paste fallback;
- `xdotool` для X11 paste;
- `xclip` или `xsel` для X11 clipboard;
- `libnotify-bin` для уведомлений;
- `nvidia-smi` для диагностики NVIDIA.

## 6. Архитектура

Высокоуровневая схема:

```text
CLI / systemd user daemon
  |
  | start / stop / toggle / healthcheck / settings
  v
Application Core
  |
  | recording state machine
  | transcription jobs
  | paste commands
  | history
  v
Linux Integration Layer
  |
  | evdev input listener
  | Wayland adapter
  | X11 adapter
  | clipboard adapter
  | notification adapter
  v
Transcription Runtime
  |
  | whisper.cpp CPU
  | whisper.cpp CUDA
  | model manager
  v
Storage
  |
  | config TOML
  | SQLite history
  | models cache
```

Runtime pipeline:

```text
PTT key down
  -> recording state: recording
  -> start microphone capture
PTT key up
  -> stop microphone capture
  -> validate duration
  -> resample/normalize audio
  -> transcribe via whisper.cpp with language=ru
  -> Russian post-processing
  -> optional OpenAI-compatible enhancement
  -> copy text to clipboard
  -> attempt paste
  -> restore clipboard optionally
  -> save history row
  -> notify success/fallback/error
```

## 7. Репозиторий

Целевая структура GitLab-репозитория:

```text
voicium/
  .gitlab-ci.yml
  pyproject.toml
  uv.lock
  README.md
  LICENSE
  docs/
    mvp.md
    install.md
    gpu.md
    wayland.md
    troubleshooting.md
  src/
    voicium/
      __init__.py
      cli.py
      config.py
      healthcheck.py
      logging.py
      audio/
        __init__.py
        device.py
        recorder.py
        wav.py
      daemon/
        __init__.py
        service.py
        state.py
      enhancement/
        __init__.py
        openai_compatible.py
        prompts.py
      hotkey/
        __init__.py
        evdev_listener.py
        keymap.py
      paste/
        __init__.py
        base.py
        clipboard.py
        wayland.py
        x11.py
      runtime/
        __init__.py
        paths.py
        subprocess.py
      storage/
        __init__.py
        history.py
        migrations.py
      transcription/
        __init__.py
        models.py
        postprocess_ru.py
        whisper_cpp.py
  tests/
    unit/
    integration/
    fixtures/
  scripts/
    build_whisper_cpp.py
    download_model.py
    package_deb.py
  packaging/
    debian/
    systemd/
      voicium.service
    desktop/
      voicium.desktop
```

## 8. CLI

Минимальные команды:

```bash
voicium healthcheck
voicium daemon
voicium start
voicium stop
voicium toggle
voicium devices
voicium record --output test.wav --duration 5
voicium transcribe test.wav --lang ru
voicium record-transcribe --duration 5
voicium models list
voicium models download balanced
voicium history list
voicium history copy <id>
voicium history repeat <id>
voicium config show
voicium config set <key> <value>
```

Команды `start`, `stop`, `toggle` должны общаться с daemon, если он запущен. Если daemon не запущен, они должны дать понятную ошибку и подсказать `systemctl --user start voicium.service` или `voicium daemon`.

## 9. Конфигурация

Путь:

```text
~/.config/voicium/config.toml
```

Пример:

```toml
[general]
language = "ru"
mode = "push_to_talk"
history_enabled = true
save_audio = false

[hotkey]
backend = "evdev"
key = "KEY_RIGHTCTRL"

[audio]
input_device = "default"
sample_rate = 16000
channels = 1
max_recording_seconds = 60
min_recording_ms = 300

[transcription]
backend = "auto"
model_profile = "balanced"
runtime_mode = "balanced"
threads = "auto"
preload_model = true

[paste]
auto_paste = true
restore_clipboard = true
restore_delay_ms = 500
fallback_to_clipboard = true

[enhancement]
enabled = false
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
model = "gpt-4.1-mini"
timeout_seconds = 10
mode = "fix_punctuation"
```

## 10. Пути данных

```text
~/.config/voicium/config.toml
~/.local/share/voicium/models/
~/.local/share/voicium/history.sqlite
~/.cache/voicium/logs/
~/.cache/voicium/tmp/
```

Аудио по умолчанию не сохранять. Временные файлы удалять после обработки.

## 11. Push-To-Talk

Требование: режим удержания клавиши, не toggle как основной UX.

Основной backend для GNOME Wayland:

- `evdev` listener на `/dev/input/event*`;
- daemon отслеживает key down / key up;
- Wayland compositor не нужен для глобального key state;
- требуется доступ к input devices.

Права:

- MVP может начать с инструкции добавить пользователя в группу `input`;
- более безопасный вариант после MVP: udev rule с ограничением доступа;
- healthcheck должен проверять доступ к event devices и объяснять, что не так.

State machine:

```text
idle
  key down -> recording
recording
  key up -> processing
  timeout -> processing
processing
  success -> idle
  failure -> idle
```

Правила:

- key repeat игнорировать;
- запись меньше `min_recording_ms` игнорировать;
- повторный key down во время `processing` игнорировать или показывать busy notification;
- daemon должен переживать ошибки audio/STT/paste и возвращаться в `idle`.

## 12. Audio

Требования:

- список input devices;
- выбор input device;
- запись mono PCM;
- sample rate `16 kHz` для Whisper;
- max recording duration;
- min recording duration;
- отсутствие записи аудио на диск по умолчанию.

CLI проверки:

```bash
voicium devices
voicium record --output /tmp/voicium-test.wav --duration 5
voicium record-transcribe --duration 5
```

Ошибки должны быть прикладными:

- нет микрофона;
- нет прав на микрофон;
- устройство занято;
- запись слишком короткая;
- запись тише порога.

## 13. Whisper.cpp Runtime

### 13.1 Backend modes

```text
auto
cuda
cpu
```

`auto` алгоритм:

1. Проверить наличие CPU whisper binary.
2. Проверить наличие CUDA whisper binary.
3. Запустить `nvidia-smi`.
4. Проверить, что GPU и VRAM доступны.
5. Запустить короткий CUDA smoke-test.
6. Если CUDA работает, выбрать `cuda`.
7. Иначе выбрать `cpu` и записать причину fallback.

Для текущей машины ожидаемый результат:

```text
NVIDIA: FAIL nvidia-smi cannot communicate with the NVIDIA driver
Selected backend: cpu
Reason: CUDA backend unavailable
```

### 13.2 Model profiles

```text
fast:
  model: ggml-small-q5_1
  target: CPU / lowest latency

balanced:
  model: ggml-medium-q5_0
  target: default quality/speed balance

accurate:
  model: ggml-large-v3-turbo-q5_0
  target: NVIDIA GPU
```

Default selection:

- if CUDA available: `balanced` or `accurate` depending on VRAM;
- if CPU only: `fast` or `balanced` depending on benchmark;
- language always `ru` for MVP.

### 13.2.1 User-facing transcription modes

The tray settings menu must expose user-facing runtime modes instead of raw implementation details:

```text
quality:
  backend: Transformers
  profile: russian
  tradeoff: best Russian quality, slowest startup/inference

fast:
  backend: whisper.cpp
  profile: fast
  tradeoff: lowest latency, lower quality

balanced:
  backend: whisper.cpp
  profile: balanced
  tradeoff: medium latency/quality
```

Rules:

- `quality` uses the HuggingFace Russian model and should cache/preload the pipeline in daemon;
- `fast` and `balanced` use `whisper.cpp` and must work without Transformers dependencies;
- selected mode must be persisted in config;
- daemon must apply the selected mode to the next transcription;
- if the selected mode is unavailable, errors must explain which model/tool is missing and how to install it;
- healthcheck must report readiness for the currently selected mode.

### 13.3 Whisper flags

Baseline flags:

```text
-l ru
--no-translate
--print-progress false
--print-timestamps false
```

Performance considerations:

- no language autodetect;
- model prewarm in daemon;
- avoid model reload on every utterance if possible;
- use quantized models;
- expose benchmark command later if needed.

## 14. NVIDIA CUDA Support

MVP must support discrete NVIDIA GPU when driver/CUDA are installed.

Healthcheck should report:

- `nvidia-smi` status;
- GPU name;
- VRAM;
- driver version;
- CUDA version;
- selected whisper backend;
- whether CUDA smoke-test passed.

If NVIDIA is broken:

- do not crash;
- use CPU fallback;
- show exact reason;
- suggest checking driver installation.

GitLab CI implications:

- normal CI does not require NVIDIA;
- GPU smoke test is separate/manual and tagged for NVIDIA runner;
- unit tests mock `nvidia-smi` output.

## 15. Paste Integration

### 15.1 Wayland / GNOME

Default environment is GNOME Wayland. It restricts global input automation.

MVP strategy:

1. Copy recognized text using `wl-copy` or native clipboard adapter.
2. Attempt paste using `ydotool`, if installed and configured.
3. If paste fails, keep text in clipboard.
4. Show notification: text copied, press `Ctrl+V`.
5. Healthcheck explains missing tools/permissions.

Required tools for best UX:

- `wl-clipboard`;
- `ydotool`;
- `ydotoold` service or equivalent permissions.

### 15.2 X11

X11 strategy:

- copy via `xclip`/`xsel` or native X11 adapter;
- paste via `xdotool key ctrl+v`;
- better auto-paste support than Wayland.

### 15.3 Clipboard restoration

Optional behavior:

1. Snapshot current clipboard if possible.
2. Put transcription into clipboard.
3. Paste.
4. Wait `restore_delay_ms`.
5. Restore previous clipboard.

If restoration fails, do not lose the transcription without warning.

## 16. Russian Post-Processing

MVP post-processing:

- trim leading/trailing spaces;
- collapse repeated whitespace;
- remove obvious Whisper artifacts;
- support spoken punctuation commands;
- apply user replacements.

Initial punctuation commands:

```text
новая строка -> \n
точка -> .
запятая -> ,
двоеточие -> :
точка с запятой -> ;
вопросительный знак -> ?
восклицательный знак -> !
открой скобку -> (
закрой скобку -> )
```

Initial replacements examples:

```text
опенкод -> OpenCode
гитлаб -> GitLab
докер -> Docker
кубернетис -> Kubernetes
пайтон -> Python
постгрес -> Postgres
```

User replacements must be configurable without code changes.

## 17. OpenAI-Compatible Enhancement

Optional, disabled by default.

Requirements:

- supports OpenAI-compatible `/chat/completions`;
- configurable `base_url`;
- API key from environment variable, not plain config by default;
- timeout;
- fallback to local raw text on API error;
- no enhancement if disabled.

Enhancement modes for MVP:

```text
fix_punctuation:
  исправить пунктуацию и распознавание, не менять смысл

message:
  привести к виду короткого сообщения, не добавлять новых фактов

raw_cleanup:
  минимально очистить текст, сохранить стиль диктовки
```

Failure behavior:

- if API timeout/error: log warning, paste local text;
- never drop transcription because enhancement failed.

## 18. History

SQLite database:

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

CLI:

```bash
voicium history list
voicium history copy <id>
voicium history repeat <id>
```

Default:

- store text history if enabled;
- do not store audio;
- logs should not include full transcriptions unless debug mode explicitly enables it.

## 19. Healthcheck

Command:

```bash
voicium healthcheck
```

Checks:

- Ubuntu version;
- session type: Wayland/X11;
- desktop environment;
- microphone access;
- audio backend;
- input device permissions;
- configured push-to-talk key;
- Wayland clipboard tools;
- X11 clipboard tools if relevant;
- paste tools;
- notification tools;
- `whisper.cpp` CPU binary;
- `whisper.cpp` CUDA binary;
- model files;
- NVIDIA driver via `nvidia-smi`;
- GPU name/VRAM if available;
- OpenAI-compatible config if enabled;
- systemd user service status.

Example expected output for current local state:

```text
OS: Ubuntu 24.04.4 LTS OK
Session: GNOME Wayland OK
Audio: pending check
Input listener: pending check
Clipboard: check wl-clipboard
Paste: check ydotool
NVIDIA: FAIL nvidia-smi cannot communicate with NVIDIA driver
GPU backend: unavailable
Selected backend: CPU fallback
Whisper CPU: pending check
Whisper CUDA: unavailable
Russian model: pending check
```

## 20. GitLab CI

Pipeline stages:

```yaml
stages:
  - lint
  - test
  - build
  - package
  - gpu
```

Jobs:

- `ruff-check`: `uv run ruff check`;
- `ruff-format-check`: `uv run ruff format --check`;
- `pytest`: `uv run pytest`;
- `build-wheel`: `uv build`;
- `build-whisper-cpu`: build or validate CPU whisper runtime;
- `package-deb`: produce `.deb` artifact;
- `gpu-smoke-test`: manual/tagged NVIDIA runner only.

Artifact requirements:

- Python wheel;
- `.deb` package;
- checksums;
- CI logs.

CUDA CI policy:

- standard CI must not require GPU;
- CUDA tests are manual or run only on tagged GPU runner;
- GPU detection tests use mocks in normal CI.

## 21. Build From Source

Expected developer flow:

```bash
git clone <gitlab-url>/voicium.git
cd voicium
uv sync
uv run ruff check
uv run ruff format --check
uv run pytest
uv run python scripts/download_model.py balanced
uv run python scripts/build_whisper_cpp.py --backend cpu
uv run python scripts/build_whisper_cpp.py --backend cuda
uv run voicium healthcheck
```

Package flow:

```bash
uv run python scripts/package_deb.py
```

CPU build must work even when CUDA is unavailable.

## 22. Installation Flow

Expected user flow from `.deb`:

```bash
sudo apt install ./voicium_0.1.0_amd64.deb
voicium healthcheck
voicium models download balanced
systemctl --user enable --now voicium.service
```

If input permissions are missing, healthcheck should show exact remediation.

Potential initial remediation:

```bash
sudo usermod -aG input "$USER"
```

This is simple but broad. A narrower udev-based approach should be preferred after MVP.

## 23. Packaging

MVP package:

- `.deb` for Ubuntu 24.04;
- install CLI as `voicium`;
- install systemd user service template;
- install desktop entry if minimal UI/tray appears later;
- do not bundle huge models inside `.deb`;
- model downloader fetches models into user data dir.

Package should not require CUDA to install. CUDA is runtime-detected.

## 24. Milestones

### M0: Repository and CI

Deliverables:

- GitLab repository skeleton;
- `pyproject.toml`;
- `uv.lock`;
- CLI skeleton;
- healthcheck skeleton;
- GitLab CI;
- README with source build instructions.

Acceptance:

- CI is green;
- wheel builds;
- `.deb` packaging skeleton works or has placeholder artifact job.

### M1: Whisper CPU Transcription

Deliverables:

- model downloader;
- whisper.cpp CPU runtime integration;
- `voicium transcribe file.wav --lang ru`.

Acceptance:

- Russian WAV fixture produces non-empty Russian text;
- unit tests cover command construction and error handling.

### M2: Audio Recording

Deliverables:

- list input devices;
- record microphone;
- save temporary WAV;
- `voicium record-transcribe`.

Acceptance:

- user can record 5 seconds and receive transcription in terminal.

### M3: NVIDIA CUDA Runtime

Deliverables:

- CUDA whisper.cpp build script;
- backend detection;
- `auto/cuda/cpu` modes;
- healthcheck GPU section.

Acceptance:

- CPU fallback works when `nvidia-smi` fails;
- CUDA mode works on a configured NVIDIA runner/machine;
- GPU detection unit tests mock success/failure cases.

### M4: Push-To-Talk Daemon

Deliverables:

- systemd user daemon;
- evdev key listener;
- key down starts recording;
- key up stops recording and transcribes.

Acceptance:

- holding configured key performs one complete dictation cycle;
- daemon recovers after errors;
- short accidental presses are ignored.

### M5: Clipboard and Paste

Deliverables:

- Wayland clipboard adapter;
- X11 clipboard adapter;
- ydotool paste attempt;
- xdotool paste attempt;
- clipboard-only fallback;
- optional clipboard restore.

Acceptance:

- text is inserted automatically where supported;
- otherwise text remains in clipboard and notification explains fallback.

### M6: Russian Polish and History

Deliverables:

- Russian punctuation commands;
- replacement dictionary;
- SQLite history;
- history CLI.

Acceptance:

- common spoken punctuation works;
- last transcriptions are retrievable and copyable.

### M7: OpenAI-Compatible Enhancement

Deliverables:

- OpenAI-compatible client;
- prompt templates;
- config;
- timeout/fallback behavior.

Acceptance:

- enhancement works when enabled and configured;
- failed API does not block local transcription insertion.

### M7.5: Tray Settings and Runtime Modes

Deliverables:

- daemon status icon menu;
- hotkey settings entry;
- transcription mode settings entry;
- config persistence for selected hotkey and transcription mode;
- runtime mode implementation for:
  - Transformers quality mode;
  - whisper.cpp fast mode;
  - whisper.cpp balanced mode;
- daemon config reload/apply path.

Acceptance:

- clicking the daemon icon opens a menu;
- user can change hotkey from the menu;
- user can switch between quality/fast/balanced transcription modes from the menu;
- next dictation uses the selected hotkey and selected transcription mode;
- missing dependencies/models produce actionable diagnostics.

### M8: Release Packaging

Deliverables:

- `.deb` package;
- install docs;
- troubleshooting docs;
- tray settings and transcription mode docs;
- GitLab artifacts;
- basic release checklist.

Acceptance:

- clean Ubuntu 24.04 machine can install package and pass healthcheck with expected warnings.

## 25. Acceptance Criteria for MVP

MVP is complete when:

- GitLab repository contains all source code and build scripts;
- GitLab CI builds package artifacts from source;
- Ubuntu 24.04 GNOME Wayland is supported as primary environment;
- `voicium healthcheck` reports system state and actionable errors;
- push-to-talk with key hold works via daemon;
- Russian speech is transcribed locally;
- NVIDIA CUDA is used when driver and CUDA runtime work;
- CPU fallback works when NVIDIA is unavailable;
- recognized text is auto-pasted when possible;
- when auto-paste is blocked, text is copied to clipboard;
- OpenAI-compatible enhancement is available but disabled by default;
- history is available through CLI;
- audio is not persisted by default;
- README documents source build, install, GPU setup, Wayland limitations.

## 26. Main Risks

### 26.1 GNOME Wayland restrictions

Risk:

- global shortcuts and synthetic paste are restricted.

Mitigation:

- use evdev for push-to-talk;
- use clipboard-first strategy;
- support `ydotool` for paste;
- provide clipboard-only fallback.

### 26.2 NVIDIA driver unavailable

Risk:

- user expects GPU but driver is broken.

Mitigation:

- healthcheck detects exact issue;
- CPU fallback is first-class;
- CUDA job is optional/manual in CI.

### 26.3 Latency too high on CPU

Risk:

- CPU fallback may feel slow with large models.

Mitigation:

- default CPU model profile should be `fast`;
- expose model profiles;
- preload model;
- benchmark locally.

### 26.4 Input device permissions

Risk:

- evdev requires permissions.

Mitigation:

- healthcheck validates permissions;
- provide setup docs;
- after MVP replace broad `input` group guidance with narrower udev rule.

### 26.5 Packaging CUDA

Risk:

- bundling CUDA dependencies is brittle.

Mitigation:

- do not hard-require CUDA package install;
- ship/use separate CPU and CUDA binaries if possible;
- runtime detection decides backend;
- package remains installable without NVIDIA.

## 27. First Implementation Step

Start with M0 and M1:

1. Create GitLab-ready Python project with `uv`.
2. Add `voicium` CLI.
3. Add `healthcheck` with current environment checks.
4. Add GitLab CI.
5. Add `whisper.cpp` CPU integration.
6. Add model downloader for Russian MVP profiles.
7. Add first WAV transcription command.

This creates a working base before touching the hard Wayland push-to-talk and paste problems.
