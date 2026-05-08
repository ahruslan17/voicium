from voicium.cli import main
from voicium.daemon import DaemonResponse, DaemonState
from voicium.transcription import TranscriptionError


def test_healthcheck_command_outputs_phase_zero_status(capsys) -> None:
    exit_code = main(["healthcheck"])

    captured = capsys.readouterr()

    assert exit_code in {0, 1}
    assert "Voicium healthcheck" in captured.out
    assert "Config path:" in captured.out


def test_config_show_outputs_default_language(capsys) -> None:
    exit_code = main(["config", "show"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[general]" in captured.out
    assert 'language = "ru"' in captured.out


def test_transcribe_command_reports_missing_file(capsys) -> None:
    exit_code = main(["transcribe", "missing.wav"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Audio file not found" in captured.out


def test_transcribe_command_defaults_to_russian_profile(capsys, monkeypatch) -> None:
    def fake_transcribe(request) -> str:
        raise TranscriptionError(f"profile={request.profile_name}")

    monkeypatch.setattr("voicium.cli.transcribe", fake_transcribe)

    exit_code = main(["transcribe", "sample.wav"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "profile=russian" in captured.out


def test_record_command_reports_invalid_duration(capsys, tmp_path) -> None:
    exit_code = main(["record", str(tmp_path / "recording.wav"), "--duration", "0"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Duration must be between" in captured.out


def test_record_transcribe_command_reports_invalid_duration(capsys) -> None:
    exit_code = main(["record-transcribe", "--duration", "0"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Duration must be between" in captured.out


def test_backend_select_reports_missing_cuda(capsys, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    exit_code = main(["backend", "select", "--backend", "cuda"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "nvidia-smi not found" in captured.out


def test_status_command_prints_daemon_status(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "voicium.cli.send_command",
        lambda command: DaemonResponse(True, DaemonState.IDLE, f"handled {command}"),
    )

    exit_code = main(["status"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "State: idle" in captured.out
    assert "handled status" in captured.out


def test_start_command_returns_failure_when_daemon_rejects(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        "voicium.cli.send_command",
        lambda _command: DaemonResponse(False, DaemonState.PROCESSING, "busy"),
    )

    exit_code = main(["start"])

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "State: processing" in captured.out
