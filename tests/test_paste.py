from __future__ import annotations

import subprocess
from collections.abc import Sequence

from voicium.config import PasteConfig
from voicium.paste import CommandResult, PasteManager, PasteMode, run_command, select_paste_backend


def test_selects_wayland_backend_when_tools_exist() -> None:
    backend = select_paste_backend(
        {"XDG_SESSION_TYPE": "wayland"},
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    assert backend.clipboard_command == ("wl-copy",)
    assert backend.paste_command == ("ydotool", "key", "ctrl+v")
    assert backend.read_command == ("wl-paste", "--no-newline")


def test_selects_x11_xclip_backend_when_tools_exist() -> None:
    def tool_finder(tool: str) -> str | None:
        return f"/usr/bin/{tool}" if tool in {"xclip", "xdotool"} else None

    backend = select_paste_backend({"XDG_SESSION_TYPE": "x11"}, tool_finder=tool_finder)

    assert backend.clipboard_command == ("xclip", "-selection", "clipboard")
    assert backend.paste_command == ("xdotool", "key", "ctrl+v")
    assert backend.read_command == ("xclip", "-selection", "clipboard", "-o")


def test_wayland_paste_failure_keeps_text_copied() -> None:
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def runner(args: Sequence[str], input_text: str | None) -> CommandResult:
        calls.append((tuple(args), input_text))
        if args[0] == "ydotool":
            return CommandResult(1, "", "ydotoold unavailable")
        return CommandResult(0, "", "")

    manager = PasteManager(
        env={"XDG_SESSION_TYPE": "wayland"},
        command_runner=runner,
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    result = manager.insert_or_copy("привет")

    assert result.mode == PasteMode.COPIED
    assert "text remains in clipboard" in result.message
    assert (("wl-copy",), "привет") in calls


def test_auto_paste_disabled_copies_only() -> None:
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def runner(args: Sequence[str], input_text: str | None) -> CommandResult:
        calls.append((tuple(args), input_text))
        return CommandResult(0, "", "")

    manager = PasteManager(
        config=PasteConfig(auto_paste=False),
        env={"XDG_SESSION_TYPE": "wayland"},
        command_runner=runner,
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    result = manager.insert_or_copy("привет")

    assert result.mode == PasteMode.COPIED
    assert calls == [(("wl-copy",), "привет")]


def test_run_command_converts_timeout_to_result(monkeypatch) -> None:
    def timeout_run(*_args: object, **_kwargs: object) -> object:
        raise subprocess.TimeoutExpired(("xclip",), timeout=5)

    monkeypatch.setattr("subprocess.run", timeout_run)

    result = run_command(("wl-copy",), "привет")

    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_run_command_starts_xclip_owner(monkeypatch) -> None:
    writes: list[str] = []

    class FakePipe:
        def write(self, text: str) -> None:
            writes.append(text)

        def close(self) -> None:
            writes.append("closed")

        def read(self) -> str:
            return ""

    class FakeProcess:
        stdin = FakePipe()
        stderr = FakePipe()

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(("xclip",), timeout=timeout)

    def fake_popen(*_args: object, **_kwargs: object) -> FakeProcess:
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    result = run_command(("xclip", "-selection", "clipboard"), "привет")

    assert result.returncode == 0
    assert writes == ["привет", "closed"]
