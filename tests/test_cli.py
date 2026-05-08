from voicium.cli import main


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
