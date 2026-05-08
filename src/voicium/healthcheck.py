from __future__ import annotations

import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CheckStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    hint: str | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]


def run_healthcheck(
    *,
    env: Mapping[str, str] | None = None,
    command_runner: CommandRunner | None = None,
) -> list[CheckResult]:
    runtime_env = env or os.environ
    runner = command_runner or run_command

    results = [
        check_os_release(),
        check_session(runtime_env),
        check_desktop(runtime_env),
        check_nvidia(runner),
        check_audio_tools(),
        check_clipboard_tools(runtime_env),
        check_paste_tools(runtime_env),
        check_input_permissions(),
        check_daemon_socket(runtime_env),
    ]

    return results


def render_results(results: Sequence[CheckResult]) -> str:
    lines = ["Voicium healthcheck"]
    for result in results:
        lines.append(f"[{result.status.value}] {result.name}: {result.message}")
        if result.hint:
            lines.append(f"      hint: {result.hint}")
    return "\n".join(lines)


def has_failures(results: Sequence[CheckResult]) -> bool:
    return any(result.status == CheckStatus.FAIL for result in results)


def run_command(args: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def check_os_release() -> CheckResult:
    pretty_name = read_os_pretty_name()
    machine = platform.machine()
    kernel = platform.release()

    if pretty_name:
        return CheckResult(
            name="OS",
            status=CheckStatus.OK,
            message=f"{pretty_name}, kernel {kernel}, arch {machine}",
        )

    return CheckResult(
        name="OS",
        status=CheckStatus.WARN,
        message=f"Unable to read /etc/os-release, kernel {kernel}, arch {machine}",
    )


def check_session(env: Mapping[str, str]) -> CheckResult:
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    wayland_display = env.get("WAYLAND_DISPLAY")
    x11_display = env.get("DISPLAY")

    if session_type == "wayland":
        return CheckResult(
            name="Session",
            status=CheckStatus.OK,
            message=f"Wayland detected ({wayland_display or 'display unknown'})",
        )

    if session_type == "x11":
        return CheckResult(
            name="Session",
            status=CheckStatus.OK,
            message=f"X11 detected ({x11_display or 'display unknown'})",
        )

    if wayland_display:
        return CheckResult(
            name="Session",
            status=CheckStatus.WARN,
            message=f"Wayland display detected but XDG_SESSION_TYPE={session_type or 'unset'}",
        )

    if x11_display:
        return CheckResult(
            name="Session",
            status=CheckStatus.WARN,
            message=f"X11 display detected but XDG_SESSION_TYPE={session_type or 'unset'}",
        )

    return CheckResult(
        name="Session",
        status=CheckStatus.FAIL,
        message="No Wayland or X11 desktop session detected",
        hint="Run Voicium inside a graphical Ubuntu session.",
    )


def check_desktop(env: Mapping[str, str]) -> CheckResult:
    desktop = env.get("XDG_CURRENT_DESKTOP") or env.get("DESKTOP_SESSION")
    if not desktop:
        return CheckResult(
            name="Desktop",
            status=CheckStatus.WARN,
            message="Desktop environment is unknown",
        )

    normalized = desktop.lower()
    if "gnome" in normalized or "ubuntu" in normalized:
        return CheckResult(
            name="Desktop",
            status=CheckStatus.OK,
            message=f"{desktop}",
        )

    return CheckResult(
        name="Desktop",
        status=CheckStatus.WARN,
        message=f"{desktop}; MVP target is Ubuntu GNOME",
    )


def check_nvidia(command_runner: CommandRunner) -> CheckResult:
    if shutil.which("nvidia-smi") is None:
        return CheckResult(
            name="NVIDIA",
            status=CheckStatus.WARN,
            message="nvidia-smi not found; CUDA backend unavailable",
            hint="Install a working NVIDIA driver or use CPU backend.",
        )

    result = command_runner(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,cuda_version",
            "--format=csv,noheader",
        ]
    )
    if result.returncode != 0:
        details = result.stderr or result.stdout or "nvidia-smi failed"
        return CheckResult(
            name="NVIDIA",
            status=CheckStatus.FAIL,
            message=details,
            hint=(
                "Fix NVIDIA driver installation or keep transcription backend=auto "
                "for CPU fallback."
            ),
        )

    gpu_line = first_non_empty_line(result.stdout)
    if not gpu_line:
        return CheckResult(
            name="NVIDIA",
            status=CheckStatus.FAIL,
            message="nvidia-smi returned no GPU data",
            hint="Check that the NVIDIA GPU is enabled and the driver is loaded.",
        )

    return CheckResult(
        name="NVIDIA",
        status=CheckStatus.OK,
        message=gpu_line,
    )


def check_audio_tools() -> CheckResult:
    available = sorted(tool for tool in ("pactl", "pw-cli", "arecord") if shutil.which(tool))
    if available:
        return CheckResult(
            name="Audio",
            status=CheckStatus.OK,
            message=f"Found audio tools: {', '.join(available)}",
        )

    return CheckResult(
        name="Audio",
        status=CheckStatus.WARN,
        message="No common audio diagnostic tools found",
        hint="Install PipeWire/PulseAudio tools if microphone diagnostics are needed.",
    )


def check_clipboard_tools(env: Mapping[str, str]) -> CheckResult:
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        return check_tools(
            name="Clipboard",
            tools=("wl-copy", "wl-paste"),
            hint="Install wl-clipboard: sudo apt install wl-clipboard",
        )

    if session_type == "x11":
        has_xclip = shutil.which("xclip") is not None
        has_xsel = shutil.which("xsel") is not None
        if has_xclip or has_xsel:
            found = "xclip" if has_xclip else "xsel"
            return CheckResult("Clipboard", CheckStatus.OK, f"Found {found}")
        return CheckResult(
            "Clipboard",
            CheckStatus.WARN,
            "No X11 clipboard tool found",
            "Install xclip or xsel.",
        )

    return CheckResult(
        "Clipboard",
        CheckStatus.SKIP,
        "Unknown session type; clipboard backend not selected",
    )


def check_paste_tools(env: Mapping[str, str]) -> CheckResult:
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        if shutil.which("ydotool"):
            return CheckResult("Paste", CheckStatus.OK, "Found ydotool for Wayland paste")
        return CheckResult(
            "Paste",
            CheckStatus.WARN,
            "ydotool not found; auto-paste may be unavailable on Wayland",
            "Install and configure ydotool, or use clipboard-only fallback.",
        )

    if session_type == "x11":
        if shutil.which("xdotool"):
            return CheckResult("Paste", CheckStatus.OK, "Found xdotool for X11 paste")
        return CheckResult(
            "Paste",
            CheckStatus.WARN,
            "xdotool not found; auto-paste unavailable on X11",
            "Install xdotool.",
        )

    return CheckResult(
        "Paste",
        CheckStatus.SKIP,
        "Unknown session type; paste backend not selected",
    )


def check_input_permissions() -> CheckResult:
    input_dir = Path("/dev/input")
    if not input_dir.exists():
        return CheckResult(
            name="Input",
            status=CheckStatus.FAIL,
            message="/dev/input does not exist",
            hint="Run on a Linux desktop with input devices available.",
        )

    event_devices = sorted(input_dir.glob("event*"))
    if not event_devices:
        return CheckResult(
            name="Input",
            status=CheckStatus.FAIL,
            message="No /dev/input/event* devices found",
        )

    readable_count = sum(os.access(path, os.R_OK) for path in event_devices)
    if readable_count > 0:
        return CheckResult(
            name="Input",
            status=CheckStatus.OK,
            message=f"Readable input devices: {readable_count}/{len(event_devices)}",
        )

    return CheckResult(
        name="Input",
        status=CheckStatus.FAIL,
        message=f"No readable input devices out of {len(event_devices)} found",
        hint="Grant access via a udev rule or add the user to the input group, then re-login.",
    )


def check_daemon_socket(env: Mapping[str, str]) -> CheckResult:
    runtime_dir = env.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return CheckResult(
            name="Daemon",
            status=CheckStatus.WARN,
            message="XDG_RUNTIME_DIR is unset; daemon socket path cannot be resolved",
        )

    socket_path = Path(runtime_dir) / "voicium" / "daemon.sock"
    if socket_path.exists():
        return CheckResult(
            name="Daemon",
            status=CheckStatus.OK,
            message=f"Daemon socket exists: {socket_path}",
        )

    return CheckResult(
        name="Daemon",
        status=CheckStatus.WARN,
        message="Daemon is not running",
        hint="Phase 1 does not implement the daemon yet; this is expected before Phase 5.",
    )


def check_tools(*, name: str, tools: Sequence[str], hint: str) -> CheckResult:
    missing = [tool for tool in tools if shutil.which(tool) is None]
    if not missing:
        return CheckResult(name=name, status=CheckStatus.OK, message=f"Found: {', '.join(tools)}")

    return CheckResult(
        name=name,
        status=CheckStatus.WARN,
        message=f"Missing: {', '.join(missing)}",
        hint=hint,
    )


def read_os_pretty_name() -> str | None:
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return None

    for line in os_release.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key == "PRETTY_NAME":
            return value.strip().strip('"')

    return None


def first_non_empty_line(value: str) -> str | None:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None
