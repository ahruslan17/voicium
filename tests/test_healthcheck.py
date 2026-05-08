from __future__ import annotations

from collections.abc import Sequence

from voicium.healthcheck import (
    CheckResult,
    CheckStatus,
    CommandResult,
    check_desktop,
    check_nvidia,
    check_session,
    has_failures,
    render_results,
)


def test_session_detects_wayland() -> None:
    result = check_session({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"})

    assert result.status == CheckStatus.OK
    assert "Wayland" in result.message


def test_desktop_warns_for_non_gnome() -> None:
    result = check_desktop({"XDG_CURRENT_DESKTOP": "sway"})

    assert result.status == CheckStatus.WARN
    assert "MVP target" in result.message


def test_nvidia_reports_driver_failure(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/nvidia-smi")

    def runner(_args: Sequence[str]) -> CommandResult:
        return CommandResult(returncode=1, stdout="", stderr="driver unavailable")

    result = check_nvidia(runner)

    assert result.status == CheckStatus.FAIL
    assert result.message == "driver unavailable"


def test_nvidia_reports_gpu_details(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: "/usr/bin/nvidia-smi")

    def runner(_args: Sequence[str]) -> CommandResult:
        return CommandResult(returncode=0, stdout="RTX 4090, 24564 MiB, 550.1, 12.4", stderr="")

    result = check_nvidia(runner)

    assert result.status == CheckStatus.OK
    assert "RTX 4090" in result.message


def test_render_results_includes_hints() -> None:
    output = render_results(
        [
            CheckResult(
                name="NVIDIA",
                status=CheckStatus.FAIL,
                message="driver unavailable",
                hint="Fix NVIDIA driver installation.",
            )
        ]
    )

    assert "[FAIL] NVIDIA: driver unavailable" in output
    assert "hint: Fix NVIDIA driver installation." in output


def test_has_failures_detects_fail_result() -> None:
    result = check_session({})

    assert result.status == CheckStatus.FAIL
    assert has_failures([result]) is True
