from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class RuntimeMode(StrEnum):
    QUALITY = "quality"
    FAST = "fast"
    BALANCED = "balanced"


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
class AudioConfig:
    input_device: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    backend: str = "auto"
    model_profile: str = "fast"
    runtime_mode: str = RuntimeMode.FAST.value
    preload_model: bool = True


@dataclass(frozen=True, slots=True)
class PasteConfig:
    auto_paste: bool = False
    restore_clipboard: bool = False
    restore_delay_ms: int = 500
    fallback_to_clipboard: bool = True
    notify: bool = True


DEFAULT_REPLACEMENTS = MappingProxyType(
    {
        "опенкод": "OpenCode",
        "гитлаб": "GitLab",
        "докер": "Docker",
        "кубернетис": "Kubernetes",
        "пайтон": "Python",
        "постгрес": "Postgres",
    }
)


@dataclass(frozen=True, slots=True)
class RussianConfig:
    replacements: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class AppConfig:
    general: GeneralConfig
    hotkey: HotkeyConfig
    audio: AudioConfig
    transcription: TranscriptionConfig
    paste: PasteConfig
    russian: RussianConfig

    @classmethod
    def default(cls) -> AppConfig:
        return cls(
            general=GeneralConfig(),
            hotkey=HotkeyConfig(),
            audio=AudioConfig(),
            transcription=TranscriptionConfig(),
            paste=PasteConfig(),
            russian=RussianConfig(replacements=DEFAULT_REPLACEMENTS),
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
                "[audio]",
                _optional_toml_string("input_device", self.audio.input_device),
                "",
                "[transcription]",
                f'backend = "{self.transcription.backend}"',
                f'model_profile = "{self.transcription.model_profile}"',
                f'runtime_mode = "{self.transcription.runtime_mode}"',
                f"preload_model = {str(self.transcription.preload_model).lower()}",
                "",
                "[paste]",
                f"auto_paste = {str(self.paste.auto_paste).lower()}",
                f"restore_clipboard = {str(self.paste.restore_clipboard).lower()}",
                f"restore_delay_ms = {self.paste.restore_delay_ms}",
                f"fallback_to_clipboard = {str(self.paste.fallback_to_clipboard).lower()}",
                f"notify = {str(self.paste.notify).lower()}",
                "",
                "[russian.replacements]",
                *[f'"{key}" = "{value}"' for key, value in self.russian.replacements.items()],
                "",
            ]
        )

    def with_runtime_mode(self, runtime_mode: str) -> AppConfig:
        transcription = transcription_for_runtime_mode(runtime_mode, self.transcription.backend)
        return replace(self, transcription=transcription)

    def with_hotkey(self, key: str) -> AppConfig:
        return replace(self, hotkey=replace(self.hotkey, key=key))

    def with_audio_input_device(self, input_device: str | None) -> AppConfig:
        return replace(self, audio=replace(self.audio, input_device=input_device))

    def with_auto_paste(self, auto_paste: bool) -> AppConfig:
        return replace(self, paste=replace(self.paste, auto_paste=auto_paste))


def default_config_path() -> Path:
    return Path.home() / ".config" / "voicium" / "config.toml"


def transcription_for_runtime_mode(
    runtime_mode: str,
    backend: str = "auto",
) -> TranscriptionConfig:
    match RuntimeMode(runtime_mode):
        case RuntimeMode.QUALITY:
            return TranscriptionConfig(
                backend="auto",
                model_profile="accurate",
                runtime_mode=RuntimeMode.QUALITY.value,
            )
        case RuntimeMode.FAST:
            return TranscriptionConfig(
                backend=backend,
                model_profile="fast",
                runtime_mode=RuntimeMode.FAST.value,
            )
        case RuntimeMode.BALANCED:
            return TranscriptionConfig(
                backend=backend,
                model_profile="balanced",
                runtime_mode=RuntimeMode.BALANCED.value,
            )


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or default_config_path()
    if not config_path.exists():
        return AppConfig.default()

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    default = AppConfig.default()
    general_data = data.get("general", {})
    hotkey_data = data.get("hotkey", {})
    audio_data = data.get("audio", {})
    transcription_data = data.get("transcription", {})
    paste_data = data.get("paste", {})
    russian_data = data.get("russian", {})
    replacements = russian_data.get("replacements", DEFAULT_REPLACEMENTS)

    runtime_mode = str(transcription_data.get("runtime_mode", default.transcription.runtime_mode))
    transcription = transcription_for_runtime_mode(
        runtime_mode,
        str(transcription_data.get("backend", default.transcription.backend)),
    )
    transcription = replace(
        transcription,
        preload_model=bool(
            transcription_data.get("preload_model", default.transcription.preload_model)
        ),
    )

    return AppConfig(
        general=GeneralConfig(
            language=str(general_data.get("language", default.general.language)),
            mode=str(general_data.get("mode", default.general.mode)),
            history_enabled=bool(
                general_data.get("history_enabled", default.general.history_enabled)
            ),
            save_audio=bool(general_data.get("save_audio", default.general.save_audio)),
        ),
        hotkey=HotkeyConfig(
            backend=str(hotkey_data.get("backend", default.hotkey.backend)),
            key=str(hotkey_data.get("key", default.hotkey.key)),
        ),
        audio=AudioConfig(input_device=_optional_string(audio_data.get("input_device"))),
        transcription=transcription,
        paste=PasteConfig(
            auto_paste=bool(paste_data.get("auto_paste", default.paste.auto_paste)),
            restore_clipboard=bool(
                paste_data.get("restore_clipboard", default.paste.restore_clipboard)
            ),
            restore_delay_ms=int(
                paste_data.get("restore_delay_ms", default.paste.restore_delay_ms)
            ),
            fallback_to_clipboard=bool(
                paste_data.get("fallback_to_clipboard", default.paste.fallback_to_clipboard)
            ),
            notify=bool(paste_data.get("notify", default.paste.notify)),
        ),
        russian=RussianConfig(replacements=dict(replacements)),
    )


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(config.to_toml(), encoding="utf-8")
    return config_path


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_toml_string(key: str, value: str | None) -> str:
    if value is None:
        return f'{key} = ""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key} = "{escaped}"'
