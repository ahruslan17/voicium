from pathlib import Path

import pytest

from voicium.audio import (
    AudioError,
    CommandResult,
    build_record_command,
    list_input_devices,
    parse_pactl_sources,
    record_wav,
    validate_duration,
)


def test_validate_duration_accepts_phase_three_default() -> None:
    validate_duration(5)


def test_validate_duration_rejects_too_short_duration() -> None:
    with pytest.raises(AudioError, match="Duration must be between"):
        validate_duration(0)


def test_list_input_devices_parses_pactl_sources(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/pactl")

    def runner(_command: list[str]) -> CommandResult:
        assert _command == ["pactl", "list", "sources"]
        return CommandResult(
            returncode=0,
            stdout=(
                "Source #55\n"
                "\tName: alsa_input.usb-Focusrite_Scarlett_Solo_USB-00.analog-stereo\n"
                "\tDescription: Scarlett Solo USB Analog Stereo\n"
                "\tDriver: PipeWire\n"
                "Source #56\n"
                "\tName: alsa_input.usb-Web-camera.analog-stereo\n"
                "\tDescription: Web-camera KQ4M3FA1 Analog Stereo\n"
                "\tDriver: PipeWire\n"
                "Source #70\n"
                "\tName: alsa_output.pci-0000_01_00.1.hdmi-stereo.monitor\n"
                "\tDescription: Monitor of HDA NVidia Digital Stereo (HDMI)\n"
            ),
            stderr="",
        )

    devices = list_input_devices(command_runner=runner)

    assert len(devices) == 2
    assert devices[0].name == "alsa_input.usb-Focusrite_Scarlett_Solo_USB-00.analog-stereo"
    assert devices[0].description == "Scarlett Solo USB Analog Stereo"
    assert devices[1].name == "alsa_input.usb-Web-camera.analog-stereo"
    assert devices[1].description == "Web-camera KQ4M3FA1 Analog Stereo"


def test_parse_pactl_sources_falls_back_to_short_table() -> None:
    devices = parse_pactl_sources(
        "1\talsa_input.pci-0000_00_1f.3.analog-stereo\tPipeWire\ts16le 2ch 48000Hz\n"
        "2\talsa_output.pci-0000_00_1f.3.analog-stereo.monitor\tPipeWire"
    )

    assert len(devices) == 1
    assert devices[0].name == "alsa_input.pci-0000_00_1f.3.analog-stereo"
    assert devices[0].description == "alsa_input.pci-0000_00_1f.3.analog-stereo"


def test_list_input_devices_reports_missing_pactl(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    with pytest.raises(AudioError, match="pactl not found"):
        list_input_devices()


def test_build_record_command_uses_default_pulse_input(tmp_path: Path) -> None:
    output_path = tmp_path / "recording.wav"

    command = build_record_command(output_path, duration_seconds=5)

    assert command == [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-i",
        "default",
        "-t",
        "5",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-y",
        str(output_path),
    ]


def test_build_record_command_uses_explicit_device(tmp_path: Path) -> None:
    output_path = tmp_path / "recording.wav"

    command = build_record_command(
        output_path,
        duration_seconds=5,
        device="alsa_input.usb-mic",
    )

    assert command[6:8] == ["-i", "alsa_input.usb-mic"]


def test_record_wav_creates_parent_and_returns_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/ffmpeg")
    output_path = tmp_path / "nested" / "recording.wav"

    def runner(_command: list[str]) -> CommandResult:
        output_path.write_bytes(b"wav")
        return CommandResult(returncode=0, stdout="", stderr="")

    path = record_wav(output_path, duration_seconds=5, command_runner=runner)

    assert path == output_path
    assert output_path.exists()


def test_record_wav_reports_ffmpeg_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/ffmpeg")

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=1, stdout="", stderr="no mic")

    with pytest.raises(AudioError, match="no mic"):
        record_wav(tmp_path / "recording.wav", duration_seconds=5, command_runner=runner)
