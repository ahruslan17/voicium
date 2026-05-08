from voicium.cli import main


def test_healthcheck_command_outputs_phase_zero_status(capsys) -> None:
    exit_code = main(["healthcheck"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Voicium healthcheck" in captured.out
    assert "Phase 0 skeleton OK" in captured.out


def test_config_show_outputs_default_language(capsys) -> None:
    exit_code = main(["config", "show"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[general]" in captured.out
    assert 'language = "ru"' in captured.out
