from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from voicium.audio import AudioError, AudioInputDevice, StreamingRecorder, list_input_devices
from voicium.config import AppConfig, RuntimeMode, load_config, save_config
from voicium.history import HistoryStore
from voicium.paste import PasteMode, PasteResult, insert_or_copy, start_detached_command
from voicium.postprocess import postprocess_russian
from voicium.transcription import TranscriptionError, TranscriptionRequest, transcribe


class DaemonError(RuntimeError):
    pass


class DaemonState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


class DaemonCommand(StrEnum):
    START_RECORDING = "start_recording"
    STOP_RECORDING = "stop_recording"
    STATUS = "status"
    RELOAD_CONFIG = "reload_config"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class HotkeyEvent:
    pressed: bool
    key_code: str | None = None


@dataclass(frozen=True, slots=True)
class DaemonResponse:
    ok: bool
    state: DaemonState
    message: str
    transcript: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "ok": self.ok,
            "state": self.state.value,
            "message": self.message,
        }
        if self.transcript is not None:
            data["transcript"] = self.transcript
        return data


@dataclass(frozen=True, slots=True)
class TrayEvent:
    state: DaemonState
    message: str
    transcript: str | None = None


HotkeyListener = Callable[[str], Iterator[HotkeyEvent]]
RecorderFactory = Callable[[Path], StreamingRecorder]
Transcriber = Callable[[TranscriptionRequest], str]
PasteInserter = Callable[[str], PasteResult]
HistoryWriter = Callable[[str, str | None, PasteResult], None]
TrayStarter = Callable[[queue.Queue[TrayEvent]], object]
ConfigLoader = Callable[[], AppConfig]


def default_runtime_dir() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "voicium"
    return Path(tempfile.gettempdir()) / f"voicium-{os.getuid()}"


def default_socket_path() -> Path:
    return default_runtime_dir() / "daemon.sock"


class DaemonService:
    def __init__(
        self,
        *,
        config: AppConfig | None = None,
        socket_path: Path | None = None,
        recorder_factory: RecorderFactory | None = None,
        transcriber: Transcriber | None = None,
        paste_inserter: PasteInserter | None = None,
        history_writer: HistoryWriter | None = None,
        hotkey_listener: HotkeyListener | None = None,
        tray_starter: TrayStarter | None = None,
        config_loader: ConfigLoader | None = None,
    ) -> None:
        self.config_loader = config_loader or load_config
        self.config = config or self.config_loader()
        self.socket_path = socket_path or default_socket_path()
        self.recorder_factory = recorder_factory or self._default_recorder_factory
        self.transcriber = transcriber or transcribe
        self.paste_inserter = paste_inserter or self._default_paste_inserter
        self.history_writer = history_writer or self._default_history_writer
        self.hotkey_listener = hotkey_listener or listen_evdev_hotkey
        self.tray_starter = tray_starter or start_status_icon
        self.state = DaemonState.IDLE
        self.last_error: str | None = None
        self.last_transcript: str | None = None
        self._tray_events: queue.Queue[TrayEvent] = queue.Queue()
        self._recorder: StreamingRecorder | None = None
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()

    def start_recording(self) -> DaemonResponse:
        started_at = time.perf_counter()
        with self._lock:
            self.config = self.config_loader()
            if self.state == DaemonState.RECORDING:
                return DaemonResponse(True, self.state, "Recording already active.")
            if self.state != DaemonState.IDLE:
                message = f"Cannot start recording while {self.state}."
                return DaemonResponse(False, self.state, message)

            audio_path = default_runtime_dir() / "recording.wav"
            self._recorder = self.recorder_factory(audio_path)
            try:
                self._recorder.start()
            except AudioError as error:
                self._recorder = None
                return self._fail(str(error))

            self.state = DaemonState.RECORDING
            self.last_error = None
            self._notify_tray(DaemonState.RECORDING, "Recording started.")
            log_timing("start_recording", started_at)
            return DaemonResponse(True, self.state, "Recording started.")

    def stop_recording(self) -> DaemonResponse:
        started_at = time.perf_counter()
        with self._lock:
            if self.state != DaemonState.RECORDING or self._recorder is None:
                return DaemonResponse(True, self.state, "No active recording.")

            recorder = self._recorder
            self._recorder = None
            self.state = DaemonState.PROCESSING
            self._notify_tray(DaemonState.PROCESSING, "Transcribing audio.")

        try:
            stop_started_at = time.perf_counter()
            audio_path = recorder.stop()
            log_timing("stop_recording.audio_stop", stop_started_at)
        except AudioError as error:
            with self._lock:
                return self._fail(str(error))

        threading.Thread(target=self._process_recording, args=(audio_path,), daemon=True).start()
        log_timing("stop_recording.return", started_at)
        return DaemonResponse(True, self.state, "Recording stopped; transcription started.")

    def _process_recording(self, audio_path: Path) -> None:
        total_started_at = time.perf_counter()
        try:
            self.config = self.config_loader()
            transcribe_started_at = time.perf_counter()
            raw_transcript = self._transcribe_audio(audio_path)
            log_timing("pipeline.transcribe", transcribe_started_at)
            postprocess_started_at = time.perf_counter()
            transcript = postprocess_russian(
                raw_transcript,
                replacements=self.config.russian.replacements,
            )
            log_timing("pipeline.postprocess", postprocess_started_at)
            copy_started_at = time.perf_counter()
            paste_result = self.paste_inserter(transcript)
            log_timing("pipeline.copy_to_clipboard", copy_started_at)
        except (AudioError, TranscriptionError, OSError, RuntimeError) as error:
            with self._lock:
                self._fail(str(error))
                log_timing("pipeline.failed_total", total_started_at)
                return

        with self._lock:
            self.state = DaemonState.IDLE
            self.last_error = None
            self.last_transcript = transcript
            message = f"Transcription completed; paste mode={paste_result.mode.value}."
            self._notify_tray(DaemonState.IDLE, message, transcript)

        history_started_at = time.perf_counter()
        self.history_writer(transcript, raw_transcript, paste_result)
        log_timing("pipeline.history", history_started_at)
        log_timing("pipeline.total", total_started_at)

    def status(self) -> DaemonResponse:
        with self._lock:
            message = (
                "Daemon is running. "
                f"hotkey={self.config.hotkey.key}, "
                f"runtime_mode={self.config.transcription.runtime_mode}."
            )
            if self.last_error is not None:
                message = self.last_error
            return DaemonResponse(True, self.state, message, self.last_transcript)

    def shutdown(self) -> DaemonResponse:
        self._stop_requested.set()
        self._notify_tray(DaemonState.IDLE, "Shutdown requested.")
        return DaemonResponse(True, self.state, "Shutdown requested.")

    def handle_command(self, command: str) -> DaemonResponse:
        match command:
            case DaemonCommand.START_RECORDING:
                return self.start_recording()
            case DaemonCommand.STOP_RECORDING:
                return self.stop_recording()
            case DaemonCommand.STATUS:
                return self.status()
            case DaemonCommand.RELOAD_CONFIG:
                return self.reload_config()
            case DaemonCommand.SHUTDOWN:
                return self.shutdown()
            case command if command.startswith("set_runtime_mode:"):
                return self.set_runtime_mode(command.partition(":")[2])
            case command if command.startswith("set_hotkey:"):
                return self.set_hotkey(command.partition(":")[2])
            case command if command.startswith("set_audio_input:"):
                return self.set_audio_input(command.partition(":")[2])
            case command if command.startswith("set_auto_paste:"):
                return self.set_auto_paste(command.partition(":")[2])
            case _:
                return DaemonResponse(False, self.state, f"Unknown command: {command}")

    def reload_config(self) -> DaemonResponse:
        with self._lock:
            self.config = self.config_loader()
            message = (
                "Config reloaded. "
                f"hotkey={self.config.hotkey.key}, "
                f"runtime_mode={self.config.transcription.runtime_mode}. "
                "Restart daemon to apply hotkey listener changes."
            )
            self._notify_tray(DaemonState.IDLE, message)
            return DaemonResponse(True, self.state, message)

    def set_runtime_mode(self, runtime_mode: str) -> DaemonResponse:
        try:
            RuntimeMode(runtime_mode)
        except ValueError:
            return DaemonResponse(False, self.state, f"Unknown runtime mode: {runtime_mode}")

        with self._lock:
            self.config = self.config.with_runtime_mode(runtime_mode)
            save_config(self.config)
            message = f"Runtime mode set to {runtime_mode}."
            self._notify_tray(DaemonState.IDLE, message)
            return DaemonResponse(True, self.state, message)

    def set_hotkey(self, key: str) -> DaemonResponse:
        if not key.startswith("KEY_"):
            return DaemonResponse(False, self.state, f"Invalid evdev key code: {key}")

        with self._lock:
            self.config = self.config.with_hotkey(key)
            save_config(self.config)
            message = f"Hotkey set to {key}."
            self._notify_tray(DaemonState.IDLE, message)
            return DaemonResponse(True, self.state, message)

    def set_audio_input(self, input_device: str) -> DaemonResponse:
        device = input_device.strip() or None
        with self._lock:
            self.config = self.config.with_audio_input_device(device)
            save_config(self.config)
            message = f"Audio input set to {device or 'default'}."
            self._notify_tray(DaemonState.IDLE, message)
            return DaemonResponse(True, self.state, message)

    def set_auto_paste(self, enabled: str) -> DaemonResponse:
        value = enabled.strip().lower()
        if value not in {"true", "false"}:
            return DaemonResponse(False, self.state, f"Invalid auto-paste value: {enabled}")

        auto_paste = value == "true"
        with self._lock:
            self.config = self.config.with_auto_paste(auto_paste)
            save_config(self.config)
            message = f"Auto-paste {'enabled' if auto_paste else 'disabled'}."
            self._notify_tray(DaemonState.IDLE, message)
            return DaemonResponse(True, self.state, message)

    def serve_forever(self) -> int:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        listener_thread = threading.Thread(target=self._run_hotkey_listener, daemon=True)
        listener_thread.start()
        tray_thread = threading.Thread(target=self._run_status_icon, daemon=True)
        tray_thread.start()

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.socket_path))
            server.listen(8)
            server.settimeout(0.2)

            while not self._stop_requested.is_set():
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    response = self._handle_socket_command(connection)
                    try:
                        connection.sendall(json.dumps(response.to_dict()).encode() + b"\n")
                    except BrokenPipeError:
                        continue

        self.socket_path.unlink(missing_ok=True)
        return 0

    def _run_hotkey_listener(self) -> None:
        try:
            for event in self.hotkey_listener(""):
                if self._stop_requested.is_set():
                    return
                self._handle_hotkey_event(event)
        except DaemonError as error:
            with self._lock:
                self._fail(str(error))

    def _handle_hotkey_event(self, event: HotkeyEvent) -> DaemonResponse | None:
        if event.key_code is not None and event.key_code != self.config.hotkey.key:
            return None
        if event.pressed:
            return self.start_recording()
        return self.stop_recording()

    def _run_status_icon(self) -> None:
        try:
            self.tray_starter(self._tray_events)
        except DaemonError as error:
            warnings.warn(str(error), RuntimeWarning, stacklevel=2)

    def _handle_socket_command(self, connection: socket.socket) -> DaemonResponse:
        try:
            return self.handle_command(_read_command(connection))
        except Exception as error:
            with self._lock:
                return self._fail(str(error))

    def _default_recorder_factory(self, audio_path: Path) -> StreamingRecorder:
        return StreamingRecorder(audio_path, device=self.config.audio.input_device)

    def _default_paste_inserter(self, text: str) -> PasteResult:
        return insert_or_copy(text, config=self.config.paste)

    def _default_history_writer(
        self,
        text: str,
        raw_text: str | None,
        paste_result: PasteResult,
    ) -> None:
        if not self.config.general.history_enabled:
            return
        try:
            HistoryStore().add(
                text=text,
                raw_text=raw_text,
                model=self.config.transcription.model_profile,
                backend=self.config.transcription.backend,
                pasted=paste_result.mode == PasteMode.PASTED,
            )
        except Exception:
            return

    def _transcribe_audio(self, audio_path: Path) -> str:
        request = TranscriptionRequest(
            audio_path=audio_path,
            language=self.config.general.language,
            profile_name=self.config.transcription.model_profile,
            backend=self.config.transcription.backend,
        )
        try:
            return self.transcriber(request)
        except TranscriptionError as error:
            if not should_fallback_to_quality(str(error), self.config.transcription.model_profile):
                raise
            return self.transcriber(
                TranscriptionRequest(
                    audio_path=audio_path,
                    language=self.config.general.language,
                    profile_name="russian",
                    backend="auto",
                )
            )

    def _fail(self, message: str) -> DaemonResponse:
        self.state = DaemonState.IDLE
        self.last_error = message
        self._notify_tray(DaemonState.IDLE, message)
        return DaemonResponse(False, self.state, message)

    def _notify_tray(
        self,
        state: DaemonState,
        message: str,
        transcript: str | None = None,
    ) -> None:
        self._tray_events.put(TrayEvent(state=state, message=message, transcript=transcript))


def send_command(
    command: str,
    *,
    socket_path: Path | None = None,
    timeout: float = 2.0,
) -> DaemonResponse:
    path = socket_path or default_socket_path()
    if not path.exists():
        raise DaemonError(f"Daemon socket not found: {path}")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(str(path))
            client.sendall(command.encode() + b"\n")
            payload = client.recv(64 * 1024)
        except TimeoutError as error:
            raise DaemonError(
                f"Daemon did not respond within {timeout:g} seconds. "
                "The request may still be processing."
            ) from error

    if not payload:
        raise DaemonError("Daemon returned empty response.")
    data = json.loads(payload.decode())
    return DaemonResponse(
        ok=bool(data["ok"]),
        state=DaemonState(str(data["state"])),
        message=str(data["message"]),
        transcript=str(data["transcript"]) if "transcript" in data else None,
    )


def should_fallback_to_quality(error_message: str, profile_name: str) -> bool:
    if profile_name == "russian":
        return False
    return "whisper.cpp binary not found" in error_message


def listen_evdev_hotkey(_key_code: str) -> Iterator[HotkeyEvent]:
    try:
        import evdev
    except ImportError as error:
        raise DaemonError(
            "python-evdev is not installed. Install it to use global push-to-talk hotkeys."
        ) from error

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = [device for device in devices if _device_has_keys(device)]
    if not keyboards:
        raise DaemonError("No readable input devices with key events found.")

    events: queue.Queue[HotkeyEvent] = queue.Queue()
    for device in keyboards:
        threading.Thread(
            target=_read_evdev_device,
            args=(device, events, evdev),
            daemon=True,
        ).start()

    while True:
        yield events.get()


def _read_evdev_device(
    device: object,
    events: queue.Queue[HotkeyEvent],
    evdev_module: object,
) -> None:
    try:
        for event in device.read_loop():  # type: ignore[attr-defined]
            if event.type != evdev_module.ecodes.EV_KEY:  # type: ignore[attr-defined]
                continue
            key_event = evdev_module.categorize(event)  # type: ignore[attr-defined]
            key_code = _first_keycode(key_event.keycode)
            if key_code is None:
                continue
            if key_event.keystate == key_event.key_hold:
                continue
            events.put(
                HotkeyEvent(
                    pressed=key_event.keystate == key_event.key_down,
                    key_code=key_code,
                )
            )
    except OSError:
        return


def _keycode_matches(actual: object, expected: str) -> bool:
    if isinstance(actual, str):
        return actual == expected
    if isinstance(actual, (list, tuple, set)):
        return expected in actual
    return False


def _first_keycode(actual: object) -> str | None:
    if isinstance(actual, str):
        return actual
    if isinstance(actual, (list, tuple)):
        for item in actual:
            if isinstance(item, str):
                return item
    return None


def start_status_icon(events: queue.Queue[TrayEvent]) -> None:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ImportError, ValueError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as error:
        raise DaemonError(
            "Status icon backend is unavailable. Install gir1.2-ayatanaappindicator3-0.1 "
            "and python3-gi to show Voicium in the top bar."
        ) from error

    indicator = AppIndicator.Indicator.new(
        "voicium",
        "audio-input-microphone-symbolic",
        AppIndicator.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_title("Voicium")
    indicator.set_attention_icon_full("media-record-symbolic", "Voicium recording")
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    indicator.set_menu(_build_status_icon_menu(Gtk))
    _watch_tray_events(events, indicator, AppIndicator, GLib)
    Gtk.main()


def _build_status_icon_menu(gtk: object) -> object:
    config = load_config()
    menu = gtk.Menu()
    status_item = gtk.MenuItem(label="Voicium is running")
    status_item.set_sensitive(False)
    menu.append(status_item)
    menu.append(gtk.SeparatorMenuItem())
    _append_hotkey_menu(gtk, menu, config)
    _append_audio_input_menu(gtk, menu, config)
    _append_runtime_mode_menu(gtk, menu, config)
    _append_paste_menu(gtk, menu, config)
    menu.show_all()
    return menu


def _append_hotkey_menu(gtk: object, menu: object, config: AppConfig) -> None:
    submenu = gtk.Menu()
    group = None
    for key in ("KEY_RIGHTCTRL", "KEY_LEFTCTRL", "KEY_F8", "KEY_PAUSE", "KEY_RIGHTALT"):
        item = _choice_menu_item(gtk, key, group=group, selected=key == config.hotkey.key)
        group = group or item
        item.connect(
            "activate", lambda _item, selected=key: _send_tray_command(f"set_hotkey:{selected}")
        )
        submenu.append(item)
    parent = gtk.MenuItem(label="Hotkey")
    parent.set_submenu(submenu)
    menu.append(parent)


def _append_audio_input_menu(gtk: object, menu: object, config: AppConfig) -> None:
    submenu = gtk.Menu()
    group = None
    default_item = _choice_menu_item(
        gtk,
        "System default",
        group=group,
        selected=config.audio.input_device is None,
    )
    group = default_item
    default_item.connect("activate", lambda _item: _send_tray_command("set_audio_input:"))
    submenu.append(default_item)

    try:
        devices = list_input_devices()
    except AudioError as error:
        item = gtk.MenuItem(label=f"Unavailable: {error}")
        item.set_sensitive(False)
        submenu.append(item)
    else:
        _append_audio_input_devices(gtk, submenu, devices, config, group)

    parent = gtk.MenuItem(label="Microphone")
    parent.set_submenu(submenu)
    menu.append(parent)


def _append_audio_input_devices(
    gtk: object,
    submenu: object,
    devices: list[AudioInputDevice],
    config: AppConfig,
    group: object,
) -> None:
    if not devices:
        item = gtk.MenuItem(label="No input devices found")
        item.set_sensitive(False)
        submenu.append(item)
        return

    for device in devices:
        label = device.description if device.description != device.name else device.name
        item = _choice_menu_item(
            gtk,
            label,
            group=group,
            selected=device.name == config.audio.input_device,
        )
        item.connect(
            "activate",
            lambda _item, selected=device.name: _send_tray_command(f"set_audio_input:{selected}"),
        )
        submenu.append(item)


def _append_runtime_mode_menu(gtk: object, menu: object, config: AppConfig) -> None:
    submenu = gtk.Menu()
    labels = {
        RuntimeMode.QUALITY.value: "Quality - Transformers",
        RuntimeMode.FAST.value: "Fast - whisper.cpp small",
        RuntimeMode.BALANCED.value: "Balanced - whisper.cpp medium",
    }
    group = None
    for runtime_mode, label in labels.items():
        item = _choice_menu_item(
            gtk,
            label,
            group=group,
            selected=runtime_mode == config.transcription.runtime_mode,
        )
        group = group or item
        item.connect(
            "activate",
            lambda _item, selected=runtime_mode: _send_tray_command(f"set_runtime_mode:{selected}"),
        )
        submenu.append(item)
    parent = gtk.MenuItem(label="Transcription Mode")
    parent.set_submenu(submenu)
    menu.append(parent)


def _append_paste_menu(gtk: object, menu: object, config: AppConfig) -> None:
    submenu = gtk.Menu()
    auto_paste_item = gtk.CheckMenuItem(label="Auto-paste")
    auto_paste_item.set_active(config.paste.auto_paste)
    auto_paste_item.connect(
        "activate",
        lambda item: _send_tray_command(f"set_auto_paste:{str(item.get_active()).lower()}"),
    )
    submenu.append(auto_paste_item)
    parent = gtk.MenuItem(label="Paste")
    parent.set_submenu(submenu)
    menu.append(parent)


def _choice_menu_item(gtk: object, label: str, *, group: object | None, selected: bool) -> object:
    item = gtk.RadioMenuItem.new_with_label_from_widget(group, label)
    item.set_active(selected)
    return item


def _send_tray_command(command: str) -> None:
    try:
        send_command(command, timeout=2.0)
    except DaemonError as error:
        show_transcript_notification(str(error))


def _watch_tray_events(
    events: queue.Queue[TrayEvent],
    indicator: object,
    app_indicator: object,
    glib: object,
) -> None:
    def poll() -> bool:
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                return True
            _apply_tray_event(event, indicator, app_indicator)

    glib.timeout_add(200, poll)


def _apply_tray_event(
    event: TrayEvent,
    indicator: object,
    app_indicator: object,
) -> None:
    if event.state == DaemonState.RECORDING:
        indicator.set_icon_full("media-record-symbolic", "Voicium recording")
        indicator.set_status(app_indicator.IndicatorStatus.ATTENTION)
        return

    if event.state == DaemonState.PROCESSING:
        indicator.set_icon_full("audio-input-microphone-symbolic", "Voicium transcribing")
        indicator.set_status(app_indicator.IndicatorStatus.ACTIVE)
        return

    indicator.set_icon_full("audio-input-microphone-symbolic", "Voicium")
    indicator.set_status(app_indicator.IndicatorStatus.ACTIVE)
    if event.transcript:
        show_transcript_notification(event.transcript)


def show_transcript_notification(text: str) -> None:
    if shutil.which("notify-send") is None:
        return
    start_detached_command(["notify-send", "Voicium transcription", text])


def log_timing(stage: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    print(f"[voicium timing] {stage}: {elapsed_ms:.1f} ms", flush=True)


def _device_has_keys(device: object) -> bool:
    try:
        capabilities = device.capabilities(verbose=True)  # type: ignore[attr-defined]
    except OSError:
        return False
    return bool(capabilities.get(("EV_KEY", 1), []))


def _read_command(connection: socket.socket) -> str:
    return connection.recv(4096).decode().strip()
