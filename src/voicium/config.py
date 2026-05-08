from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GeneralConfig:
    language: str = "ru"
    mode: str = "push_to_talk"
    history_enabled: bool = True
    save_audio: bool = False


@dataclass(frozen=True, slots=True)
class HotkeyConfig:
    backend: str = "evdev"
    key: str = "KEY_RIGHTCTRL"


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    backend: str = "auto"
    model_profile: str = "russian"
    preload_model: bool = True


@dataclass(frozen=True, slots=True)
class PasteConfig:
    auto_paste: bool = True
    restore_clipboard: bool = False
    restore_delay_ms: int = 500
    fallback_to_clipboard: bool = True
    notify: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    general: GeneralConfig
    hotkey: HotkeyConfig
    transcription: TranscriptionConfig
    paste: PasteConfig

    @classmethod
    def default(cls) -> AppConfig:
        return cls(
            general=GeneralConfig(),
            hotkey=HotkeyConfig(),
            transcription=TranscriptionConfig(),
            paste=PasteConfig(),
        )

    def to_toml(self) -> str:
        return "\n".join(
            [
                "[general]",
                f'language = "{self.general.language}"',
                f'mode = "{self.general.mode}"',
                f"history_enabled = {str(self.general.history_enabled).lower()}",
                f"save_audio = {str(self.general.save_audio).lower()}",
                "",
                "[hotkey]",
                f'backend = "{self.hotkey.backend}"',
                f'key = "{self.hotkey.key}"',
                "",
                "[transcription]",
                f'backend = "{self.transcription.backend}"',
                f'model_profile = "{self.transcription.model_profile}"',
                f"preload_model = {str(self.transcription.preload_model).lower()}",
                "",
                "[paste]",
                f"auto_paste = {str(self.paste.auto_paste).lower()}",
                f"restore_clipboard = {str(self.paste.restore_clipboard).lower()}",
                f"restore_delay_ms = {self.paste.restore_delay_ms}",
                f"fallback_to_clipboard = {str(self.paste.fallback_to_clipboard).lower()}",
                f"notify = {str(self.paste.notify).lower()}",
                "",
            ]
        )


def default_config_path() -> Path:
    return Path.home() / ".config" / "voicium" / "config.toml"
