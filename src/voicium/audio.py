from __future__ import annotations

import shutil
import subprocess
import time
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
ProcessFactory = Callable[[Sequence[str]], subprocess.Popen[bytes]]

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
    result = runner(["pactl", "list", "sources"])
    if result.returncode != 0:
        details = result.stderr or result.stdout or "pactl failed"
        raise AudioError(f"Unable to list input devices: {details}")

    return parse_pactl_sources(result.stdout)


def parse_pactl_sources(output: str) -> list[AudioInputDevice]:
    devices: list[AudioInputDevice] = []
    current_name: str | None = None
    current_description: str | None = None

    def append_current() -> None:
        if current_name is None or current_name.endswith(".monitor"):
            return
        devices.append(
            AudioInputDevice(
                name=current_name,
                description=current_description or current_name,
            )
        )

    for line in output.splitlines():
        if line.startswith("Source #"):
            append_current()
            current_name = None
            current_description = None
            continue

        stripped = line.strip()
        if stripped.startswith("Name:"):
            current_name = stripped.partition(":")[2].strip()
        elif stripped.startswith("Description:"):
            current_description = stripped.partition(":")[2].strip()

    append_current()
    if devices:
        return devices

    # Older PulseAudio-compatible implementations can return the short table even for tests or
    # stripped environments. Keep parsing it as a fallback, but prefer detailed descriptions above.
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[1]
        if name.endswith(".monitor"):
            continue
        devices.append(AudioInputDevice(name=name, description=name))
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


@dataclass(slots=True)
class StreamingRecorder:
    output_path: Path
    device: str | None = None
    process_factory: ProcessFactory | None = None
    process: subprocess.Popen[bytes] | None = None
    started_at: float | None = None

    def start(self) -> None:
        if self.process is not None:
            raise AudioError("Recording is already active.")
        if shutil.which("ffmpeg") is None:
            raise AudioError("ffmpeg not found. Install ffmpeg to record microphone audio.")

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        factory = self.process_factory or start_process
        self.process = factory(build_stream_record_command(self.output_path, device=self.device))
        self.started_at = time.monotonic()

    def stop(self) -> Path:
        process = self.process
        if process is None:
            raise AudioError("Recording is not active.")

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            self.process = None

        if not self.output_path.exists():
            raise AudioError(f"Recording did not produce WAV file: {self.output_path}")
        return self.output_path

    def is_recording(self) -> bool:
        return self.process is not None


def build_stream_record_command(output_path: Path, *, device: str | None = None) -> list[str]:
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


def start_process(args: Sequence[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
