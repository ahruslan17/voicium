from pathlib import Path

import pytest

from voicium.backend import (
    BackendError,
    BackendName,
    CommandResult,
    detect_nvidia_gpu,
    discover_whisper_binary,
    parse_nvidia_smi_csv,
    run_cuda_smoke_test,
    select_backend,
)


def test_parse_nvidia_smi_csv_returns_gpu_details() -> None:
    gpu = parse_nvidia_smi_csv("RTX 4090, 24564 MiB, 550.1, 12.4")

    assert gpu.name == "RTX 4090"
    assert gpu.memory_total == "24564 MiB"
    assert gpu.driver_version == "550.1"
    assert gpu.cuda_version == "12.4"


def test_parse_nvidia_smi_csv_rejects_empty_output() -> None:
    with pytest.raises(BackendError, match="no GPU data"):
        parse_nvidia_smi_csv("")


def test_detect_nvidia_gpu_reports_driver_failure(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/nvidia-smi")

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=1, stdout="", stderr="driver unavailable")

    with pytest.raises(BackendError, match="driver unavailable"):
        detect_nvidia_gpu(command_runner=runner)


def test_discover_cuda_binary_prefers_cuda_specific_command(monkeypatch) -> None:
    def which(command: str) -> str | None:
        return "/usr/bin/whisper-cuda" if command == "whisper-cuda" else None

    monkeypatch.setattr("shutil.which", which)

    assert discover_whisper_binary(BackendName.CUDA) == Path("/usr/bin/whisper-cuda")


def test_discover_cpu_binary_prefers_packaged_binary(monkeypatch, tmp_path: Path) -> None:
    packaged_binary = tmp_path / "whisper-cli"
    packaged_binary.touch()
    monkeypatch.setattr("voicium.backend.packaged_whisper_binary", lambda: packaged_binary)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/whisper-cli")

    assert discover_whisper_binary(BackendName.CPU) == packaged_binary


def test_select_backend_auto_falls_back_to_cpu_when_nvidia_fails(monkeypatch) -> None:
    monkeypatch.setattr("voicium.backend.packaged_whisper_binary", lambda: Path("/missing"))

    def which(command: str) -> str | None:
        if command == "nvidia-smi":
            return "/usr/bin/nvidia-smi"
        if command == "whisper-cli":
            return "/usr/bin/whisper-cli"
        return None

    monkeypatch.setattr("shutil.which", which)

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=1, stdout="", stderr="driver unavailable")

    selection = select_backend("auto", command_runner=runner)

    assert selection.backend == BackendName.CPU
    assert selection.binary_path == Path("/usr/bin/whisper-cli")
    assert "driver unavailable" in selection.reason


def test_select_backend_auto_chooses_cuda_when_nvidia_works(monkeypatch) -> None:
    def which(command: str) -> str | None:
        if command == "nvidia-smi":
            return "/usr/bin/nvidia-smi"
        if command == "whisper-cuda":
            return "/usr/bin/whisper-cuda"
        return None

    monkeypatch.setattr("shutil.which", which)

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=0, stdout="RTX 4090, 24564 MiB, 550.1, 12.4", stderr="")

    selection = select_backend("auto", command_runner=runner)

    assert selection.backend == BackendName.CUDA
    assert selection.binary_path == Path("/usr/bin/whisper-cuda")
    assert selection.gpu is not None
    assert selection.gpu.name == "RTX 4090"


def test_select_backend_cuda_fails_when_nvidia_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    with pytest.raises(BackendError, match="nvidia-smi not found"):
        select_backend("cuda")


def test_cuda_smoke_test_runs_binary_help(monkeypatch) -> None:
    def which(command: str) -> str | None:
        if command == "nvidia-smi":
            return "/usr/bin/nvidia-smi"
        if command == "whisper-cuda":
            return "/usr/bin/whisper-cuda"
        return None

    monkeypatch.setattr("shutil.which", which)
    calls: list[list[str]] = []

    def runner(command: list[str]) -> CommandResult:
        calls.append(command)
        if command[0] == "nvidia-smi":
            return CommandResult(
                returncode=0,
                stdout="RTX 4090, 24564 MiB, 550.1, 12.4",
                stderr="",
            )
        return CommandResult(returncode=0, stdout="help", stderr="")

    selection = run_cuda_smoke_test(command_runner=runner)

    assert selection.backend == BackendName.CUDA
    assert calls[-1] == ["/usr/bin/whisper-cuda", "--help"]
