from __future__ import annotations

import subprocess
from collections.abc import Sequence

from voicium.config import PasteConfig
from voicium.paste import (
    CommandResult,
    PasteManager,
    PasteMode,
    PasteResult,
    copy_to_clipboard,
    notify_paste_result,
    run_command,
    select_paste_backend,
    start_clipboard_owner,
)


def test_selects_wayland_backend_when_tools_exist() -> None:
    backend = select_paste_backend(
        {"XDG_SESSION_TYPE": "wayland"},
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    assert backend.clipboard_command == ("wl-copy",)
    assert backend.paste_command == ("ydotool", "key", "ctrl+v")
    assert backend.read_command == ("wl-paste", "--no-newline")


def test_selects_wayland_backend_from_display_variable() -> None:
    backend = select_paste_backend(
        {"WAYLAND_DISPLAY": "wayland-0"},
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    assert backend.clipboard_command == ("wl-copy",)


def test_selects_x11_backend_from_display_variable() -> None:
    def tool_finder(tool: str) -> str | None:
        return f"/usr/bin/{tool}" if tool in {"xclip", "xdotool"} else None

    backend = select_paste_backend({"DISPLAY": ":0"}, tool_finder=tool_finder)

    assert backend.clipboard_command == ("xclip", "-selection", "clipboard")


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
        config=PasteConfig(auto_paste=True),
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

    result = run_command(("custom-copy",), "привет")

    assert result.returncode == 124
    assert "timed out" in result.stderr


def test_run_command_starts_fast_clipboard_owner(monkeypatch) -> None:
    writes: list[str] = []
    commands: list[list[str]] = []

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

    def fake_popen(args: list[str], *_other_args: object, **_kwargs: object) -> FakeProcess:
        commands.append(args)
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    result = run_command(("xclip", "-selection", "clipboard"), "привет")

    assert result.returncode == 0
    assert commands == [["xclip", "-selection", "clipboard", "-loops", "10"]]
    assert writes == ["привет", "closed"]


def test_wl_copy_owner_keeps_command_minimal(monkeypatch) -> None:
    commands: list[list[str]] = []

    class FakePipe:
        def write(self, _text: str) -> None:
            pass

        def close(self) -> None:
            pass

        def read(self) -> str:
            return ""

    class FakeProcess:
        stdin = FakePipe()
        stderr = FakePipe()

        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired(("wl-copy",), timeout=timeout)

    def fake_popen(args: list[str], *_other_args: object, **_kwargs: object) -> FakeProcess:
        commands.append(args)
        return FakeProcess()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    result = start_clipboard_owner(("wl-copy",), "привет")

    assert result.returncode == 0
    assert commands == [["wl-copy"]]


def test_copy_to_clipboard_waits_for_fast_owner_start(monkeypatch) -> None:
    waited: list[float | None] = []

    class FakePipe:
        def write(self, _text: str) -> None:
            pass

        def close(self) -> None:
            pass

        def read(self) -> str:
            return ""

    class FakeProcess:
        stdin = FakePipe()
        stderr = FakePipe()

        def wait(self, timeout: float | None = None) -> int:
            waited.append(timeout)
            raise subprocess.TimeoutExpired(("wl-copy",), timeout=timeout)

    monkeypatch.setattr("subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())

    result = copy_to_clipboard(
        "привет",
        env={"WAYLAND_DISPLAY": "wayland-0"},
        tool_finder=lambda tool: f"/usr/bin/{tool}" if tool == "wl-copy" else None,
    )

    assert result.mode == PasteMode.COPIED
    assert waited[0] == 0.1


def test_notify_paste_result_is_detached_by_default(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr("shutil.which", lambda _command: "/usr/bin/notify-send")
    monkeypatch.setattr(
        "voicium.paste.start_detached_command", lambda args: calls.append(tuple(args))
    )

    notify_paste_result(PasteResult(PasteMode.COPIED, "copied"))

    assert calls == [("notify-send", "Voicium", "copied")]


def test_copy_to_clipboard_does_not_attempt_auto_paste() -> None:
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def runner(args: Sequence[str], input_text: str | None) -> CommandResult:
        calls.append((tuple(args), input_text))
        return CommandResult(0, "", "")

    result = copy_to_clipboard(
        "привет",
        env={"XDG_SESSION_TYPE": "wayland"},
        command_runner=runner,
        tool_finder=lambda tool: f"/usr/bin/{tool}",
    )

    assert result.mode == PasteMode.COPIED
    assert calls == [(("wl-copy",), "привет")]
