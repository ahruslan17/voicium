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
    model_profile: str = "balanced"
    preload_model: bool = True


@dataclass(frozen=True, slots=True)
class AppConfig:
    general: GeneralConfig
    hotkey: HotkeyConfig
    transcription: TranscriptionConfig

    @classmethod
    def default(cls) -> AppConfig:
        return cls(
            general=GeneralConfig(),
            hotkey=HotkeyConfig(),
            transcription=TranscriptionConfig(),
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
            ]
        )


def default_config_path() -> Path:
    return Path.home() / ".config" / "voicium" / "config.toml"
