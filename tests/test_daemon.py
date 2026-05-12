from __future__ import annotations

import socket
import sys
import threading
import time
import types
import warnings
from pathlib import Path
from typing import ClassVar

import pytest

from voicium.audio import StreamingRecorder
from voicium.config import AppConfig, save_config
from voicium.daemon import (
    DaemonCommand,
    DaemonError,
    DaemonService,
    DaemonState,
    HotkeyEvent,
    TrayEvent,
    _append_audio_input_menu,
    _append_hotkey_menu,
    _append_runtime_mode_menu,
    _apply_tray_event,
    listen_evdev_hotkey,
    send_command,
    should_fallback_to_quality,
    show_transcript_notification,
)
from voicium.paste import PasteMode, PasteResult
from voicium.transcription import TranscriptionError, TranscriptionRequest


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_daemon_start_stop_transcribes_and_returns_idle(tmp_path: Path) -> None:
    requests: list[TranscriptionRequest] = []
    pasted: list[str] = []
    history: list[tuple[str, str | None, PasteResult]] = []

    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    def transcriber(request: TranscriptionRequest) -> str:
        requests.append(request)
        return "привет"

    def paste_inserter(text: str) -> PasteResult:
        pasted.append(text)
        return PasteResult(PasteMode.PASTED, "pasted")

    service = DaemonService(
        recorder_factory=recorder_factory,
        transcriber=transcriber,
        paste_inserter=paste_inserter,
        history_writer=lambda text, raw, result: history.append((text, raw, result)),
    )

    start = service.handle_command(DaemonCommand.START_RECORDING.value)
    stop = service.handle_command(DaemonCommand.STOP_RECORDING.value)
    _wait_for_state(service, DaemonState.IDLE)

    assert start.ok is True
    assert start.state == DaemonState.RECORDING
    assert stop.ok is True
    assert stop.message == "Recording stopped; transcription started."
    assert service.last_transcript == "привет"
    assert len(requests) == 1
    assert pasted == ["привет"]
    assert history == [("привет", "привет", PasteResult(PasteMode.PASTED, "pasted"))]


def test_default_daemon_paste_inserter_disables_auto_paste(monkeypatch) -> None:
    calls: list[object] = []

    def fake_copy_to_clipboard(text: str) -> PasteResult:
        calls.append(text)
        return PasteResult(PasteMode.COPIED, "copied")

    monkeypatch.setattr("voicium.daemon.copy_to_clipboard", fake_copy_to_clipboard)

    result = DaemonService(config=AppConfig.default())._default_paste_inserter("привет")

    assert result.mode == PasteMode.COPIED
    assert calls == ["привет"]


def test_default_daemon_recorder_uses_configured_audio_device() -> None:
    config = AppConfig.default()
    config = type(config)(
        general=config.general,
        hotkey=config.hotkey,
        audio=type(config.audio)(input_device="alsa_input.test"),
        transcription=config.transcription,
        paste=config.paste,
        russian=config.russian,
    )

    recorder = DaemonService(config=config)._default_recorder_factory(Path("recording.wav"))

    assert recorder.device == "alsa_input.test"


def test_daemon_postprocesses_transcript_before_paste(tmp_path: Path) -> None:
    pasted: list[str] = []

    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    service = DaemonService(
        recorder_factory=recorder_factory,
        transcriber=lambda _request: "привет запятая опенкод",
        paste_inserter=lambda text: pasted.append(text) or PasteResult(PasteMode.PASTED, "pasted"),
        history_writer=lambda _text, _raw, _result: None,
    )

    service.handle_command(DaemonCommand.START_RECORDING.value)
    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)
    _wait_for_state(service, DaemonState.IDLE)

    assert response.ok is True
    assert service.last_transcript == "привет, OpenCode"
    assert pasted == ["привет, OpenCode"]


def test_daemon_ignores_stop_without_recording() -> None:
    service = DaemonService()

    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)

    assert response.ok is True
    assert response.state == DaemonState.IDLE
    assert response.message == "No active recording."


def test_daemon_status_includes_hotkey_and_runtime_mode() -> None:
    response = DaemonService(config=AppConfig.default()).status()

    assert "hotkey=KEY_RIGHTCTRL" in response.message
    assert "runtime_mode=fast" in response.message


def test_daemon_updates_runtime_mode_and_hotkey(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("voicium.config.default_config_path", lambda: config_path)
    service = DaemonService(config=AppConfig.default())

    mode_response = service.handle_command("set_runtime_mode:fast")
    hotkey_response = service.handle_command("set_hotkey:KEY_F8")

    assert mode_response.ok is True
    assert hotkey_response.ok is True
    assert service.config.transcription.model_profile == "fast"
    assert service.config.hotkey.key == "KEY_F8"
    assert config_path.exists()


def test_daemon_applies_hotkey_change_without_listener_restart(tmp_path: Path) -> None:
    paths: list[Path] = []

    def recorder_factory(path: Path) -> StreamingRecorder:
        paths.append(path)

        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    service = DaemonService(
        config=AppConfig.default(),
        recorder_factory=recorder_factory,
        transcriber=lambda _request: "привет",
        paste_inserter=lambda _text: PasteResult(PasteMode.COPIED, "copied"),
        history_writer=lambda _text, _raw, _result: None,
    )
    service.handle_command("set_hotkey:KEY_F8")

    service._handle_hotkey_event(HotkeyEvent(pressed=True, key_code="KEY_RIGHTCTRL"))
    ignored_stop = service._handle_hotkey_event(
        HotkeyEvent(pressed=False, key_code="KEY_RIGHTCTRL")
    )
    start = service._handle_hotkey_event(HotkeyEvent(pressed=True, key_code="KEY_F8"))
    stop = service._handle_hotkey_event(HotkeyEvent(pressed=False, key_code="KEY_F8"))
    _wait_for_state(service, DaemonState.IDLE)

    assert ignored_stop is None
    assert start is not None
    assert stop is not None
    assert len(paths) == 1


def test_daemon_updates_audio_input(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("voicium.config.default_config_path", lambda: config_path)
    service = DaemonService(config=AppConfig.default())

    response = service.handle_command("set_audio_input:alsa_input.test")

    assert response.ok is True
    assert service.config.audio.input_device == "alsa_input.test"
    assert 'input_device = "alsa_input.test"' in config_path.read_text(encoding="utf-8")


def test_daemon_resets_audio_input_to_default(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("voicium.config.default_config_path", lambda: config_path)
    service = DaemonService(config=AppConfig.default().with_audio_input_device("alsa_input.test"))

    response = service.handle_command("set_audio_input:")

    assert response.ok is True
    assert service.config.audio.input_device is None


def test_daemon_reloads_config(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("voicium.config.default_config_path", lambda: config_path)
    save_config(
        AppConfig.default().with_hotkey("KEY_F8").with_runtime_mode("balanced"), config_path
    )
    service = DaemonService(config=AppConfig.default())

    response = service.handle_command(DaemonCommand.RELOAD_CONFIG.value)

    assert response.ok is True
    assert service.config.hotkey.key == "KEY_F8"
    assert service.config.transcription.model_profile == "balanced"


def test_daemon_returns_error_when_paste_fails(tmp_path: Path) -> None:
    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    service = DaemonService(
        recorder_factory=recorder_factory,
        transcriber=lambda _request: "привет",
        paste_inserter=lambda _text: (_ for _ in ()).throw(RuntimeError("paste failed")),
        history_writer=lambda _text, _raw, _result: None,
    )

    service.handle_command(DaemonCommand.START_RECORDING.value)
    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)
    _wait_for_state(service, DaemonState.IDLE)

    assert response.ok is True
    assert response.message == "Recording stopped; transcription started."
    assert service.last_error == "paste failed"


def test_daemon_falls_back_to_quality_when_whisper_cpp_binary_is_missing(
    tmp_path: Path,
) -> None:
    requests: list[TranscriptionRequest] = []

    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    def transcriber(request: TranscriptionRequest) -> str:
        requests.append(request)
        if request.profile_name == "fast":
            raise TranscriptionError("whisper.cpp binary not found")
        return "fallback transcript"

    service = DaemonService(
        config=AppConfig.default().with_runtime_mode("fast"),
        recorder_factory=recorder_factory,
        transcriber=transcriber,
        paste_inserter=lambda _text: PasteResult(PasteMode.COPIED, "copied"),
        history_writer=lambda _text, _raw, _result: None,
    )

    service.handle_command(DaemonCommand.START_RECORDING.value)
    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)
    _wait_for_state(service, DaemonState.IDLE)

    assert response.ok is True
    assert service.last_transcript == "fallback transcript"
    assert [request.profile_name for request in requests] == ["fast", "russian"]


def test_fallback_to_quality_only_for_missing_whisper_cpp_binary() -> None:
    assert should_fallback_to_quality("whisper.cpp binary not found", "fast") is True
    assert should_fallback_to_quality("whisper.cpp binary not found", "russian") is False
    assert should_fallback_to_quality("other", "fast") is False


def test_daemon_socket_status(tmp_path: Path) -> None:
    socket_path = tmp_path / "daemon.sock"
    tray_started: list[bool] = []
    service = DaemonService(
        socket_path=socket_path,
        hotkey_listener=lambda _key: iter(()),
        tray_starter=lambda _events: tray_started.append(True),
    )
    thread = threading.Thread(target=service.serve_forever)
    thread.start()
    _wait_for_socket(socket_path)

    response = send_command(DaemonCommand.STATUS.value, socket_path=socket_path)
    shutdown = send_command(DaemonCommand.SHUTDOWN.value, socket_path=socket_path)
    thread.join(timeout=2)

    assert response.ok is True
    assert response.state == DaemonState.IDLE
    assert shutdown.ok is True
    assert thread.is_alive() is False
    assert tray_started == [True]


def test_daemon_ignores_missing_status_icon_backend(tmp_path: Path) -> None:
    socket_path = tmp_path / "daemon.sock"

    def fail_tray() -> None:
        raise DaemonError("missing tray backend")

    warnings.filterwarnings("ignore", message="missing tray backend", category=RuntimeWarning)

    service = DaemonService(
        socket_path=socket_path,
        hotkey_listener=lambda _key: iter(()),
        tray_starter=lambda _events: fail_tray(),
    )
    thread = threading.Thread(target=service.serve_forever)
    thread.start()
    _wait_for_socket(socket_path)

    response = send_command(DaemonCommand.STATUS.value, socket_path=socket_path)
    shutdown = send_command(DaemonCommand.SHUTDOWN.value, socket_path=socket_path)
    thread.join(timeout=2)

    assert response.ok is True
    assert shutdown.ok is True
    assert thread.is_alive() is False


def test_send_command_wraps_timeout(tmp_path: Path) -> None:
    socket_path = tmp_path / "daemon.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        server.listen(1)
        server.settimeout(1)

        def accept_without_response() -> None:
            connection, _ = server.accept()
            with connection:
                connection.recv(4096)
                time.sleep(0.1)

        thread = threading.Thread(target=accept_without_response)
        thread.start()

        with pytest.raises(DaemonError, match="Daemon did not respond"):
            send_command(DaemonCommand.STATUS.value, socket_path=socket_path, timeout=0.01)

        thread.join(timeout=1)


def test_evdev_listener_reads_from_all_matching_keyboards(monkeypatch) -> None:
    class FakeEvent:
        type = 1

    class FakeKeyEvent:
        keycode: ClassVar[list[str]] = ["KEY_RIGHTCTRL", "BTN_EXTRA"]
        keystate = 1
        key_hold = 2
        key_down = 1

    class FakeDevice:
        def __init__(self, path: str) -> None:
            self.path = path

        def capabilities(
            self, verbose: bool = False
        ) -> dict[tuple[str, int], list[tuple[str, int]]]:
            return {("EV_KEY", 1): [("KEY_RIGHTCTRL", 97)]}

        def read_loop(self):
            if self.path == "/dev/input/event2":
                yield FakeEvent()

    fake_evdev = types.SimpleNamespace(
        InputDevice=FakeDevice,
        list_devices=lambda: ["/dev/input/event1", "/dev/input/event2"],
        ecodes=types.SimpleNamespace(EV_KEY=1),
        categorize=lambda _event: FakeKeyEvent(),
    )
    monkeypatch.setitem(sys.modules, "evdev", fake_evdev)

    event = next(listen_evdev_hotkey("KEY_RIGHTCTRL"))

    assert event.pressed is True
    assert event.key_code == "KEY_RIGHTCTRL"


def test_daemon_emits_tray_events_for_recording_and_completion(tmp_path: Path) -> None:
    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    service = DaemonService(
        recorder_factory=recorder_factory,
        transcriber=lambda _request: "привет",
        paste_inserter=lambda _text: PasteResult(PasteMode.PASTED, "pasted"),
        history_writer=lambda _text, _raw, _result: None,
        tray_starter=lambda _events: None,
    )

    service.handle_command(DaemonCommand.START_RECORDING.value)
    service.handle_command(DaemonCommand.STOP_RECORDING.value)
    _wait_for_state(service, DaemonState.IDLE)

    events = [service._tray_events.get_nowait() for _ in range(3)]
    assert [event.state for event in events] == [
        DaemonState.RECORDING,
        DaemonState.PROCESSING,
        DaemonState.IDLE,
    ]
    assert events[-1].transcript == "привет"


def test_apply_tray_event_switches_indicator_attention(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeIndicator:
        def set_icon_full(self, icon: str, description: str) -> None:
            calls.append(("icon", icon))

        def set_status(self, status: object) -> None:
            calls.append(("status", status))

    class FakeAppIndicator:
        class IndicatorStatus:
            ACTIVE = "active"
            ATTENTION = "attention"

    notified: list[str] = []
    monkeypatch.setattr("voicium.daemon.show_transcript_notification", notified.append)

    _apply_tray_event(
        TrayEvent(DaemonState.RECORDING, "recording"),
        FakeIndicator(),
        FakeAppIndicator,
    )
    _apply_tray_event(
        TrayEvent(DaemonState.IDLE, "done", "готово"),
        FakeIndicator(),
        FakeAppIndicator,
    )

    assert ("status", "attention") in calls
    assert ("status", "active") in calls
    assert notified == ["готово"]


def test_show_transcript_notification_is_best_effort(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _command: "/usr/bin/notify-send")
    monkeypatch.setattr("voicium.daemon.start_detached_command", lambda args: calls.append(args))

    show_transcript_notification("готово")

    assert calls == [["notify-send", "Voicium transcription", "готово"]]


def test_append_audio_input_menu_lists_devices(monkeypatch) -> None:
    commands: list[str] = []
    monkeypatch.setattr("voicium.daemon._send_tray_command", commands.append)
    monkeypatch.setattr(
        "voicium.daemon.list_input_devices",
        lambda: [types.SimpleNamespace(name="alsa_input.test", description="Test Mic")],
    )

    menu = FakeGtk.Menu()
    _append_audio_input_menu(
        FakeGtk,
        menu,
        AppConfig.default().with_audio_input_device("alsa_input.test"),
    )

    parent = menu.items[0]
    submenu = parent.submenu
    submenu.items[0].activate()
    submenu.items[1].activate()

    assert parent.label == "Microphone"
    assert [item.label for item in submenu.items] == ["System default", "Test Mic"]
    assert [item.active for item in submenu.items] == [False, True]
    assert commands == ["set_audio_input:", "set_audio_input:alsa_input.test"]


def test_append_hotkey_menu_marks_selected_key() -> None:
    menu = FakeGtk.Menu()
    _append_hotkey_menu(FakeGtk, menu, AppConfig.default().with_hotkey("KEY_F8"))

    submenu = menu.items[0].submenu

    assert [item.label for item in submenu.items] == [
        "KEY_RIGHTCTRL",
        "KEY_LEFTCTRL",
        "KEY_F8",
        "KEY_PAUSE",
        "KEY_RIGHTALT",
    ]
    assert [item.active for item in submenu.items] == [False, False, True, False, False]


def test_append_runtime_mode_menu_marks_selected_mode() -> None:
    menu = FakeGtk.Menu()
    _append_runtime_mode_menu(FakeGtk, menu, AppConfig.default().with_runtime_mode("balanced"))

    submenu = menu.items[0].submenu

    assert [item.label for item in submenu.items] == [
        "Quality - Transformers",
        "Fast - whisper.cpp small",
        "Balanced - whisper.cpp medium",
    ]
    assert [item.active for item in submenu.items] == [False, False, True]


class FakeGtk:
    class Menu:
        def __init__(self) -> None:
            self.items: list[object] = []

        def append(self, item: object) -> None:
            self.items.append(item)

    class MenuItem:
        def __init__(self, label: str) -> None:
            self.label = label
            self.submenu: object | None = None
            self.callback = None
            self.sensitive = True

        def connect(self, _event: str, callback: object) -> None:
            self.callback = callback

        def set_sensitive(self, sensitive: bool) -> None:
            self.sensitive = sensitive

        def set_submenu(self, submenu: object) -> None:
            self.submenu = submenu

        def activate(self) -> None:
            self.callback(self)

    class RadioMenuItem(MenuItem):
        def __init__(self, label: str, group: object | None) -> None:
            super().__init__(label)
            self.group = group
            self.active = False

        @classmethod
        def new_with_label_from_widget(cls, group: object | None, label: str) -> object:
            return cls(label, group)

        def set_active(self, active: bool) -> None:
            self.active = active


def _wait_for_socket(socket_path: Path) -> None:
    for _ in range(500):
        if socket_path.exists():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                try:
                    client.connect(str(socket_path))
                except OSError:
                    time.sleep(0.01)
                    continue
                client.sendall(DaemonCommand.STATUS.value.encode() + b"\n")
                client.recv(4096)
            return
        time.sleep(0.01)
    raise AssertionError("socket was not created")


def _wait_for_state(service: DaemonService, state: DaemonState) -> None:
    for _ in range(100):
        if service.state == state:
            return
        time.sleep(0.01)
    raise AssertionError(f"daemon did not reach state {state}")
