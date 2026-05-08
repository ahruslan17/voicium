from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class AudioInputDevice:
    name: str
    description: str


CommandRunner = Callable[[Sequence[str]], CommandResult]

MIN_DURATION_SECONDS = 1
MAX_DURATION_SECONDS = 300


def validate_duration(duration_seconds: int) -> None:
    if duration_seconds < MIN_DURATION_SECONDS or duration_seconds > MAX_DURATION_SECONDS:
        raise AudioError(
            f"Duration must be between {MIN_DURATION_SECONDS} and {MAX_DURATION_SECONDS} seconds."
        )


def list_input_devices(*, command_runner: CommandRunner | None = None) -> list[AudioInputDevice]:
    if shutil.which("pactl") is None:
        raise AudioError(
            "pactl not found. Install PulseAudio/PipeWire tools to list input devices."
        )

    runner = command_runner or run_command
    result = runner(["pactl", "list", "short", "sources"])
    if result.returncode != 0:
        details = result.stderr or result.stdout or "pactl failed"
        raise AudioError(f"Unable to list input devices: {details}")

    devices: list[AudioInputDevice] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor"):
            continue
        description = parts[2] if len(parts) > 2 else name
        devices.append(AudioInputDevice(name=name, description=description))
    return devices


def build_record_command(
    output_path: Path,
    *,
    duration_seconds: int,
    device: str | None = None,
) -> list[str]:
    validate_duration(duration_seconds)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "pulse",
    ]
    if device is not None:
        command.extend(["-i", device])
    else:
        command.extend(["-i", "default"])
    command.extend(
        [
            "-t",
            str(duration_seconds),
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-y",
            str(output_path),
        ]
    )
    return command


def record_wav(
    output_path: Path,
    *,
    duration_seconds: int,
    device: str | None = None,
    command_runner: CommandRunner | None = None,
) -> Path:
    if shutil.which("ffmpeg") is None:
        raise AudioError("ffmpeg not found. Install ffmpeg to record microphone audio.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner = command_runner or run_command
    result = runner(
        build_record_command(output_path, duration_seconds=duration_seconds, device=device)
    )
    if result.returncode != 0:
        details = result.stderr or result.stdout or "ffmpeg failed"
        raise AudioError(f"Recording failed: {details}")
    if not output_path.exists():
        raise AudioError(f"Recording did not produce WAV file: {output_path}")
    return output_path


def run_command(args: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=MAX_DURATION_SECONDS + 10,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )
