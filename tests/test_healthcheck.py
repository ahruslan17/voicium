from __future__ import annotations

from collections.abc import Sequence

from voicium.healthcheck import (
    CheckResult,
    CheckStatus,
    CommandResult,
    check_audio_tools,
    check_clipboard_tools,
    check_daemon_socket,
    check_desktop,
    check_input_permissions,
    check_nvidia,
    check_paste_tools,
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


def test_nvidia_reports_missing_driver_tool(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    result = check_nvidia(lambda _args: CommandResult(returncode=0, stdout="", stderr=""))

    assert result.status == CheckStatus.WARN
    assert "nvidia-smi not found" in result.message
    assert result.hint is not None


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


def test_audio_tools_warn_when_common_tools_are_missing(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    result = check_audio_tools()

    assert result.status == CheckStatus.WARN
    assert "No common audio diagnostic tools found" in result.message
    assert result.hint is not None


def test_wayland_clipboard_reports_missing_tools(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    result = check_clipboard_tools({"XDG_SESSION_TYPE": "wayland"})

    assert result.status == CheckStatus.WARN
    assert "wl-copy" in result.message
    assert "wl-paste" in result.message
    assert result.hint is not None


def test_wayland_paste_reports_missing_tool(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)

    result = check_paste_tools({"XDG_SESSION_TYPE": "wayland"})

    assert result.status == CheckStatus.WARN
    assert "ydotool not found" in result.message
    assert result.hint is not None


def test_input_permissions_fail_when_input_directory_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    result = check_input_permissions()

    assert result.status == CheckStatus.FAIL
    assert "/dev/input does not exist" in result.message
    assert result.hint is not None


def test_daemon_socket_hint_references_current_daemon_command(tmp_path) -> None:
    result = check_daemon_socket({"XDG_RUNTIME_DIR": str(tmp_path)})

    assert result.status == CheckStatus.WARN
    assert result.hint is not None
    assert "voicium daemon" in result.hint
