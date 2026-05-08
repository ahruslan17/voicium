from __future__ import annotations

import socket
import threading
from pathlib import Path

from voicium.audio import StreamingRecorder
from voicium.daemon import DaemonCommand, DaemonService, DaemonState, send_command
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

    def recorder_factory(path: Path) -> StreamingRecorder:
        def process_factory(_args: list[str]) -> FakeProcess:
            path.write_bytes(b"wav")
            return FakeProcess()

        return StreamingRecorder(path, process_factory=process_factory)

    def transcriber(request: TranscriptionRequest) -> str:
        requests.append(request)
        return "привет"

    service = DaemonService(recorder_factory=recorder_factory, transcriber=transcriber)

    start = service.handle_command(DaemonCommand.START_RECORDING.value)
    stop = service.handle_command(DaemonCommand.STOP_RECORDING.value)

    assert start.ok is True
    assert start.state == DaemonState.RECORDING
    assert stop.ok is True
    assert stop.state == DaemonState.IDLE
    assert stop.transcript == "привет"
    assert len(requests) == 1


def test_daemon_ignores_stop_without_recording() -> None:
    service = DaemonService()

    response = service.handle_command(DaemonCommand.STOP_RECORDING.value)

    assert response.ok is True
    assert response.state == DaemonState.IDLE
    assert response.message == "No active recording."


def test_daemon_socket_status(tmp_path: Path) -> None:
    socket_path = tmp_path / "daemon.sock"
    service = DaemonService(socket_path=socket_path, hotkey_listener=lambda _key: iter(()))
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
