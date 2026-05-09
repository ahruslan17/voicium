from __future__ import annotations

import socket
import sys
import threading
import time
import types
import warnings
from pathlib import Path

import pytest

from voicium.audio import StreamingRecorder
from voicium.daemon import (
    DaemonCommand,
    DaemonError,
    DaemonService,
    DaemonState,
    TrayEvent,
    _apply_tray_event,
    listen_evdev_hotkey,
    send_command,
    show_transcript_notification,
)
from voicium.paste import PasteMode, PasteResult
from voicium.transcription import TranscriptionRequest


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

    assert start.ok is True
    assert start.state == DaemonState.RECORDING
    assert stop.ok is True
    assert stop.state == DaemonState.IDLE
    assert stop.transcript == "привет"
    assert "paste mode=pasted" in stop.message
    assert len(requests) == 1
    assert pasted == ["привет"]
    assert history == [("привет", "привет", PasteResult(PasteMode.PASTED, "pasted"))]


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

    assert response.transcript == "привет, OpenCode"
    assert pasted == ["привет, OpenCode"]


def test_daemon_ignores_stop_without_recording() -> None:
    service = DaemonService()

    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)

    assert response.ok is True
    assert response.state == DaemonState.IDLE
    assert response.message == "No active recording."


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

    assert response.ok is False
    assert response.state == DaemonState.IDLE
    assert response.message == "paste failed"


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
        keycode = "KEY_RIGHTCTRL"
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

        def read(self) -> list[FakeEvent]:
            return [FakeEvent()] if self.path == "/dev/input/event2" else []

    fake_evdev = types.SimpleNamespace(
        InputDevice=FakeDevice,
        list_devices=lambda: ["/dev/input/event1", "/dev/input/event2"],
        ecodes=types.SimpleNamespace(EV_KEY=1),
        categorize=lambda _event: FakeKeyEvent(),
    )
    monkeypatch.setitem(sys.modules, "evdev", fake_evdev)
    monkeypatch.setattr(
        "select.select",
        lambda devices, _write, _error: (
            [device for device in devices if device.path == "/dev/input/event2"],
            [],
            [],
        ),
    )

    event = next(listen_evdev_hotkey("KEY_RIGHTCTRL"))

    assert event.pressed is True


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


def _wait_for_socket(socket_path: Path) -> None:
    for _ in range(100):
        if socket_path.exists():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                try:
                    client.connect(str(socket_path))
                except OSError:
                    continue
                client.sendall(DaemonCommand.STATUS.value.encode() + b"\n")
                client.recv(4096)
            return
    raise AssertionError("socket was not created")
