from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from voicium.audio import AudioError, StreamingRecorder
from voicium.config import AppConfig
from voicium.paste import PasteResult, insert_or_copy
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
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class HotkeyEvent:
    pressed: bool


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


HotkeyListener = Callable[[str], Iterator[HotkeyEvent]]
RecorderFactory = Callable[[Path], StreamingRecorder]
Transcriber = Callable[[TranscriptionRequest], str]
PasteInserter = Callable[[str], PasteResult]


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
        hotkey_listener: HotkeyListener | None = None,
    ) -> None:
        self.config = config or AppConfig.default()
        self.socket_path = socket_path or default_socket_path()
        self.recorder_factory = recorder_factory or self._default_recorder_factory
        self.transcriber = transcriber or transcribe
        self.paste_inserter = paste_inserter or self._default_paste_inserter
        self.hotkey_listener = hotkey_listener or listen_evdev_hotkey
        self.state = DaemonState.IDLE
        self.last_error: str | None = None
        self.last_transcript: str | None = None
        self._recorder: StreamingRecorder | None = None
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()

    def start_recording(self) -> DaemonResponse:
        with self._lock:
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
            return DaemonResponse(True, self.state, "Recording started.")

    def stop_recording(self) -> DaemonResponse:
        with self._lock:
            if self.state != DaemonState.RECORDING or self._recorder is None:
                return DaemonResponse(True, self.state, "No active recording.")

            recorder = self._recorder
            self._recorder = None
            self.state = DaemonState.PROCESSING

        try:
            audio_path = recorder.stop()
            transcript = self.transcriber(
                TranscriptionRequest(
                    audio_path=audio_path,
                    language=self.config.general.language,
                    profile_name=self.config.transcription.model_profile,
                    backend=self.config.transcription.backend,
                )
            )
            paste_result = self.paste_inserter(transcript)
        except (AudioError, TranscriptionError) as error:
            with self._lock:
                return self._fail(str(error))

        with self._lock:
            self.state = DaemonState.IDLE
            self.last_error = None
            self.last_transcript = transcript
            message = f"Transcription completed; paste mode={paste_result.mode.value}."
            return DaemonResponse(True, self.state, message, transcript)

    def status(self) -> DaemonResponse:
        with self._lock:
            message = "Daemon is running."
            if self.last_error is not None:
                message = self.last_error
            return DaemonResponse(True, self.state, message, self.last_transcript)

    def shutdown(self) -> DaemonResponse:
        self._stop_requested.set()
        return DaemonResponse(True, self.state, "Shutdown requested.")

    def handle_command(self, command: str) -> DaemonResponse:
        match command:
            case DaemonCommand.START_RECORDING:
                return self.start_recording()
            case DaemonCommand.STOP_RECORDING:
                return self.stop_recording()
            case DaemonCommand.STATUS:
                return self.status()
            case DaemonCommand.SHUTDOWN:
                return self.shutdown()
            case _:
                return DaemonResponse(False, self.state, f"Unknown command: {command}")

    def serve_forever(self) -> int:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()

        listener_thread = threading.Thread(target=self._run_hotkey_listener, daemon=True)
        listener_thread.start()

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
                    response = self.handle_command(_read_command(connection))
                    try:
                        connection.sendall(json.dumps(response.to_dict()).encode() + b"\n")
                    except BrokenPipeError:
                        continue

        self.socket_path.unlink(missing_ok=True)
        return 0

    def _run_hotkey_listener(self) -> None:
        try:
            for event in self.hotkey_listener(self.config.hotkey.key):
                if self._stop_requested.is_set():
                    return
                if event.pressed:
                    self.start_recording()
                else:
                    self.stop_recording()
        except DaemonError as error:
            with self._lock:
                self._fail(str(error))

    def _default_recorder_factory(self, audio_path: Path) -> StreamingRecorder:
        return StreamingRecorder(audio_path)

    def _default_paste_inserter(self, text: str) -> PasteResult:
        return insert_or_copy(text, config=self.config.paste)

    def _fail(self, message: str) -> DaemonResponse:
        self.state = DaemonState.IDLE
        self.last_error = message
        return DaemonResponse(False, self.state, message)


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
        client.connect(str(path))
        client.sendall(command.encode() + b"\n")
        payload = client.recv(64 * 1024)

    if not payload:
        raise DaemonError("Daemon returned empty response.")
    data = json.loads(payload.decode())
    return DaemonResponse(
        ok=bool(data["ok"]),
        state=DaemonState(str(data["state"])),
        message=str(data["message"]),
        transcript=str(data["transcript"]) if "transcript" in data else None,
    )


def listen_evdev_hotkey(key_code: str) -> Iterator[HotkeyEvent]:
    try:
        import evdev
    except ImportError as error:
        raise DaemonError(
            "python-evdev is not installed. Install it to use global push-to-talk hotkeys."
        ) from error

    devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
    keyboards = [device for device in devices if _device_supports_key(device, key_code)]
    if not keyboards:
        raise DaemonError(f"No readable input device supports {key_code}.")

    for device in keyboards:
        for event in device.read_loop():
            if event.type != evdev.ecodes.EV_KEY:
                continue
            key_event = evdev.categorize(event)
            if key_event.keycode != key_code or key_event.keystate == key_event.key_hold:
                continue
            yield HotkeyEvent(pressed=key_event.keystate == key_event.key_down)


def _device_supports_key(device: object, key_code: str) -> bool:
    try:
        capabilities = device.capabilities(verbose=True)  # type: ignore[attr-defined]
    except OSError:
        return False
    key_capabilities = capabilities.get(("EV_KEY", 1), [])
    return any(entry[0] == key_code for entry in key_capabilities)


def _read_command(connection: socket.socket) -> str:
    return connection.recv(4096).decode().strip()
