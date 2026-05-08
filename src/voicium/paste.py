from __future__ import annotations

import os
import shutil
import subprocess
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
        if not text.strip():
            return PasteResult(PasteMode.FAILED, "No text to paste.")

        backend = select_paste_backend(self.env, tool_finder=self.tool_finder)
        if backend.clipboard_command is None:
            return PasteResult(PasteMode.FAILED, "No clipboard backend is available.")

        previous_clipboard = self._read_clipboard(backend)
        copy_result = self.command_runner(backend.clipboard_command, text)
        if copy_result.returncode != 0:
            details = copy_result.stderr or copy_result.stdout or "clipboard command failed"
            return PasteResult(PasteMode.FAILED, f"Unable to copy text to clipboard: {details}")

        if not self.config.auto_paste or backend.paste_command is None:
            return PasteResult(PasteMode.COPIED, "Text copied to clipboard; press Ctrl+V to paste.")

        paste_result = self.command_runner(backend.paste_command, None)
        if paste_result.returncode != 0:
            details = paste_result.stderr or paste_result.stdout or "paste command failed"
            return PasteResult(
                PasteMode.COPIED,
                f"Auto-paste failed; text remains in clipboard: {details}",
            )

        if self.config.restore_clipboard and previous_clipboard is not None:
            time.sleep(self.config.restore_delay_ms / 1000)
            self.command_runner(backend.clipboard_command, previous_clipboard)

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
        notify_paste_result(result, command_runner=command_runner, tool_finder=tool_finder)
    return result


def select_paste_backend(
    env: Mapping[str, str],
    *,
    tool_finder: ToolFinder | None = None,
) -> PasteBackend:
    finder = tool_finder or shutil.which
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland":
        clipboard = ("wl-copy",) if finder("wl-copy") else None
        read = ("wl-paste", "--no-newline") if finder("wl-paste") else None
        paste = ("ydotool", "key", "ctrl+v") if finder("ydotool") else None
        return PasteBackend(clipboard, paste, read)

    if session_type == "x11":
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
    runner = command_runner or run_command
    runner(("notify-send", "Voicium", result.message), None)


def run_command(args: Sequence[str], input_text: str | None) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        input=input_text,
        text=True,
        timeout=5,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )
