from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from voicium.config import PasteConfig


class PasteError(RuntimeError):
    pass


class PasteMode(StrEnum):
    PASTED = "pasted"
    COPIED = "copied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class PasteResult:
    mode: PasteMode
    message: str


CommandRunner = Callable[[Sequence[str], str | None], CommandResult]
ToolFinder = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class PasteBackend:
    clipboard_command: tuple[str, ...] | None
    paste_command: tuple[str, ...] | None
    read_command: tuple[str, ...] | None = None


class PasteManager:
    def __init__(
        self,
        *,
        config: PasteConfig | None = None,
        env: Mapping[str, str] | None = None,
        command_runner: CommandRunner | None = None,
        tool_finder: ToolFinder | None = None,
    ) -> None:
        self.config = config or PasteConfig()
        self.env = env or os.environ
        self.command_runner = command_runner or run_command
        self.tool_finder = tool_finder or shutil.which

    def insert_or_copy(self, text: str) -> PasteResult:
        total_started_at = time.perf_counter()
        if not text.strip():
            return PasteResult(PasteMode.FAILED, "No text to paste.")

        select_started_at = time.perf_counter()
        backend = select_paste_backend(self.env, tool_finder=self.tool_finder)
        log_timing("paste.select_backend", select_started_at)
        if backend.clipboard_command is None:
            return PasteResult(PasteMode.FAILED, "No clipboard backend is available.")

        previous_clipboard = self._read_clipboard(backend)
        copy_started_at = time.perf_counter()
        copy_result = self.command_runner(backend.clipboard_command, text)
        log_timing("paste.copy", copy_started_at)
        if copy_result.returncode != 0:
            details = copy_result.stderr or copy_result.stdout or "clipboard command failed"
            return PasteResult(PasteMode.FAILED, f"Unable to copy text to clipboard: {details}")

        if not self.config.auto_paste or backend.paste_command is None:
            log_timing("paste.total", total_started_at)
            return PasteResult(PasteMode.COPIED, "Text copied to clipboard; press Ctrl+V to paste.")

        paste_started_at = time.perf_counter()
        paste_result = self.command_runner(backend.paste_command, None)
        log_timing("paste.auto_paste", paste_started_at)
        if paste_result.returncode != 0:
            details = paste_result.stderr or paste_result.stdout or "paste command failed"
            return PasteResult(
                PasteMode.COPIED,
                f"Auto-paste failed; text remains in clipboard: {details}",
            )

        if self.config.restore_clipboard and previous_clipboard is not None:
            time.sleep(self.config.restore_delay_ms / 1000)
            self.command_runner(backend.clipboard_command, previous_clipboard)

        log_timing("paste.total", total_started_at)
        return PasteResult(PasteMode.PASTED, "Text pasted into the focused field.")

    def _read_clipboard(self, backend: PasteBackend) -> str | None:
        if not self.config.restore_clipboard or backend.read_command is None:
            return None
        result = self.command_runner(backend.read_command, None)
        if result.returncode != 0:
            return None
        return result.stdout


def insert_or_copy(
    text: str,
    *,
    config: PasteConfig | None = None,
    env: Mapping[str, str] | None = None,
    command_runner: CommandRunner | None = None,
    tool_finder: ToolFinder | None = None,
) -> PasteResult:
    manager = PasteManager(
        config=config,
        env=env,
        command_runner=command_runner,
        tool_finder=tool_finder,
    )
    result = manager.insert_or_copy(text)
    if config is None or config.notify:
        notify_started_at = time.perf_counter()
        notify_paste_result(result, command_runner=command_runner, tool_finder=tool_finder)
        log_timing("paste.notify", notify_started_at)
    return result


def copy_to_clipboard(
    text: str,
    *,
    env: Mapping[str, str] | None = None,
    command_runner: CommandRunner | None = None,
    tool_finder: ToolFinder | None = None,
) -> PasteResult:
    if not text.strip():
        return PasteResult(PasteMode.FAILED, "No text to copy.")

    backend = select_paste_backend(env or os.environ, tool_finder=tool_finder)
    if backend.clipboard_command is None:
        return PasteResult(PasteMode.FAILED, "No clipboard backend is available.")

    if command_runner is None and supports_fast_clipboard_owner(backend.clipboard_command):
        result = start_clipboard_owner(backend.clipboard_command, text)
    else:
        runner = command_runner or run_command
        result = runner(backend.clipboard_command, text)

    if result.returncode != 0:
        details = result.stderr or result.stdout or "clipboard command failed"
        return PasteResult(PasteMode.FAILED, f"Unable to copy text to clipboard: {details}")
    return PasteResult(PasteMode.COPIED, "Text copied to clipboard.")


def select_paste_backend(
    env: Mapping[str, str],
    *,
    tool_finder: ToolFinder | None = None,
) -> PasteBackend:
    finder = tool_finder or shutil.which
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland" or (not session_type and env.get("WAYLAND_DISPLAY")):
        clipboard = ("wl-copy",) if finder("wl-copy") else None
        read = ("wl-paste", "--no-newline") if finder("wl-paste") else None
        paste = ("ydotool", "key", "ctrl+v") if finder("ydotool") else None
        return PasteBackend(clipboard, paste, read)

    if session_type == "x11" or (not session_type and env.get("DISPLAY")):
        if finder("xclip"):
            clipboard = ("xclip", "-selection", "clipboard")
            read = ("xclip", "-selection", "clipboard", "-o")
        elif finder("xsel"):
            clipboard = ("xsel", "--clipboard", "--input")
            read = ("xsel", "--clipboard", "--output")
        else:
            clipboard = None
            read = None
        paste = ("xdotool", "key", "ctrl+v") if finder("xdotool") else None
        return PasteBackend(clipboard, paste, read)

    if finder("wl-copy"):
        return PasteBackend(("wl-copy",), None, None)
    if finder("xclip"):
        return PasteBackend(("xclip", "-selection", "clipboard"), None, None)
    if finder("xsel"):
        return PasteBackend(("xsel", "--clipboard", "--input"), None, None)
    return PasteBackend(None, None, None)


def notify_paste_result(
    result: PasteResult,
    *,
    command_runner: CommandRunner | None = None,
    tool_finder: ToolFinder | None = None,
) -> None:
    finder = tool_finder or shutil.which
    if finder("notify-send") is None:
        return
    if command_runner is None:
        start_detached_command(("notify-send", "Voicium", result.message))
        return
    runner = command_runner or run_command
    runner(("notify-send", "Voicium", result.message), None)


def run_command(args: Sequence[str], input_text: str | None) -> CommandResult:
    if input_text is not None and supports_fast_clipboard_owner(args):
        return start_clipboard_owner(args, input_text)

    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            check=False,
            input=input_text,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as error:
        return CommandResult(
            returncode=124,
            stdout=(error.stdout or "").strip() if isinstance(error.stdout, str) else "",
            stderr=f"Command timed out after {error.timeout:g} seconds: {' '.join(args)}",
        )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )


def supports_fast_clipboard_owner(args: Sequence[str] | None) -> bool:
    if args is None:
        return False
    return tuple(args) in {
        ("wl-copy",),
        ("xclip", "-selection", "clipboard"),
    }


def start_clipboard_owner(
    args: Sequence[str],
    text: str,
    *,
    wait_for_start: bool = True,
) -> CommandResult:
    started_at = time.perf_counter()
    command = clipboard_owner_command(args)
    if tuple(args) == ("xclip", "-selection", "clipboard"):
        result = start_xclip_owner(command, text)
        log_timing("paste.clipboard_owner", started_at)
        return result

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdin is not None
    assert process.stderr is not None
    process.stdin.write(text)
    process.stdin.close()

    if not wait_for_start:
        return CommandResult(returncode=0, stdout="", stderr="")

    try:
        returncode = process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        wait_for_process(process)
        log_timing("paste.clipboard_owner", started_at)
        return CommandResult(returncode=0, stdout="", stderr="")

    stderr = process.stderr.read().strip()
    log_timing("paste.clipboard_owner", started_at)
    if returncode != 0:
        return CommandResult(returncode=returncode, stdout="", stderr=stderr)
    return CommandResult(returncode=0, stdout="", stderr="")


def start_xclip_owner(command: Sequence[str], text: str) -> CommandResult:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    assert process.stdin is not None
    process.stdin.write(text)
    process.stdin.close()
    wait_for_process(process)
    return CommandResult(returncode=0, stdout="", stderr="")


def clipboard_owner_command(args: Sequence[str]) -> list[str]:
    command = list(args)
    if tuple(args) == ("xclip", "-selection", "clipboard"):
        command.extend(["-loops", "10"])
    return command


def start_detached_command(args: Sequence[str]) -> None:
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    wait_for_process(process)


def wait_for_process(process: subprocess.Popen[object]) -> None:
    def wait() -> None:
        try:
            process.wait()
        except Exception:
            return

    threading.Thread(target=wait, daemon=True).start()


def log_timing(stage: str, started_at: float) -> None:
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    print(f"[voicium timing] {stage}: {elapsed_ms:.1f} ms", flush=True)
