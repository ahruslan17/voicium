from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class BackendError(RuntimeError):
    pass


class BackendName(StrEnum):
    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class NvidiaGpu:
    name: str
    memory_total: str
    driver_version: str
    cuda_version: str


@dataclass(frozen=True, slots=True)
class BackendSelection:
    backend: BackendName
    reason: str
    binary_path: Path | None = None
    gpu: NvidiaGpu | None = None


CommandRunner = Callable[[Sequence[str]], CommandResult]


def parse_nvidia_smi_csv(output: str) -> NvidiaGpu:
    line = first_non_empty_line(output)
    if line is None:
        raise BackendError("nvidia-smi returned no GPU data")

    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 4:
        raise BackendError(f"Unable to parse nvidia-smi output: {line}")

    return NvidiaGpu(
        name=parts[0],
        memory_total=parts[1],
        driver_version=parts[2],
        cuda_version=parts[3],
    )


def detect_nvidia_gpu(*, command_runner: CommandRunner | None = None) -> NvidiaGpu:
    if shutil.which("nvidia-smi") is None:
        raise BackendError("nvidia-smi not found; CUDA backend unavailable")

    runner = command_runner or run_command
    result = runner(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,cuda_version",
            "--format=csv,noheader",
        ]
    )
    if result.returncode != 0:
        details = result.stderr or result.stdout or "nvidia-smi failed"
        raise BackendError(details)
    return parse_nvidia_smi_csv(result.stdout)


def discover_whisper_binary(
    backend: BackendName,
    *,
    explicit_binary: Path | None = None,
) -> Path:
    if explicit_binary is not None:
        if explicit_binary.exists():
            return explicit_binary
        raise BackendError(f"whisper.cpp binary not found: {explicit_binary}")

    packaged_binary = packaged_whisper_binary()
    if backend == BackendName.CPU and packaged_binary.exists():
        return packaged_binary

    for command in whisper_binary_candidates(backend):
        found = shutil.which(command)
        if found is not None:
            return Path(found)

    if backend == BackendName.CUDA:
        raise BackendError(
            "CUDA whisper.cpp binary not found. Build whisper.cpp with CUDA and expose "
            "whisper-cuda or pass --whisper-bin."
        )
    raise BackendError(
        "whisper.cpp binary not found. Install whisper.cpp and expose whisper-cli in PATH, "
        "or pass --whisper-bin."
    )


def whisper_binary_candidates(backend: BackendName) -> tuple[str, ...]:
    if backend == BackendName.CUDA:
        return ("whisper-cuda", "whisper-cli-cuda", "whisper-cli")
    return ("whisper-cli", "whisper.cpp", "main")


def packaged_whisper_binary() -> Path:
    return Path("/usr/lib/voicium/bin/whisper-cli")


def select_backend(
    requested_backend: str,
    *,
    explicit_binary: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> BackendSelection:
    try:
        requested = BackendName(requested_backend)
    except ValueError as error:
        raise BackendError("Backend must be one of: auto, cpu, cuda.") from error

    if requested == BackendName.CPU:
        return BackendSelection(
            backend=BackendName.CPU,
            reason="CPU backend requested",
            binary_path=discover_whisper_binary(BackendName.CPU, explicit_binary=explicit_binary),
        )

    if requested == BackendName.CUDA:
        gpu = detect_nvidia_gpu(command_runner=command_runner)
        return BackendSelection(
            backend=BackendName.CUDA,
            reason="CUDA backend requested and NVIDIA is available",
            binary_path=discover_whisper_binary(BackendName.CUDA, explicit_binary=explicit_binary),
            gpu=gpu,
        )

    try:
        gpu = detect_nvidia_gpu(command_runner=command_runner)
        return BackendSelection(
            backend=BackendName.CUDA,
            reason="NVIDIA is available",
            binary_path=discover_whisper_binary(BackendName.CUDA, explicit_binary=explicit_binary),
            gpu=gpu,
        )
    except BackendError as cuda_error:
        return BackendSelection(
            backend=BackendName.CPU,
            reason=f"CUDA backend unavailable: {cuda_error}",
            binary_path=discover_whisper_binary(BackendName.CPU, explicit_binary=explicit_binary),
        )


def run_cuda_smoke_test(
    *,
    binary_path: Path | None = None,
    command_runner: CommandRunner | None = None,
) -> BackendSelection:
    selection = select_backend(
        BackendName.CUDA.value,
        explicit_binary=binary_path,
        command_runner=command_runner,
    )
    runner = command_runner or run_command
    result = runner([str(selection.binary_path), "--help"])
    if result.returncode != 0:
        details = result.stderr or result.stdout or "CUDA smoke-test failed"
        raise BackendError(details)
    return selection


def run_command(args: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def first_non_empty_line(value: str) -> str | None:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
