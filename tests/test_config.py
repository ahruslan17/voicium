from pathlib import Path

from voicium.config import (
    AppConfig,
    RuntimeMode,
    default_config_path,
    load_config,
    save_config,
    transcription_for_runtime_mode,
)


def test_default_config_is_russian_push_to_talk() -> None:
    config = AppConfig.default()

    assert config.general.language == "ru"
    assert config.general.mode == "push_to_talk"
    assert config.hotkey.backend == "evdev"
    assert config.transcription.backend == "auto"
    assert config.transcription.model_profile == "russian"
    assert config.transcription.runtime_mode == RuntimeMode.QUALITY.value
    assert config.paste.auto_paste is False
    assert config.paste.fallback_to_clipboard is True
    assert config.russian.replacements["опенкод"] == "OpenCode"


def test_default_config_path_uses_user_config_directory() -> None:
    path = default_config_path()

    assert isinstance(path, Path)
    assert path.name == "config.toml"
    assert path.parent.name == "voicium"


def test_transcription_for_runtime_mode_maps_profiles() -> None:
    assert transcription_for_runtime_mode("quality").model_profile == "russian"
    assert transcription_for_runtime_mode("fast").model_profile == "fast"
    assert transcription_for_runtime_mode("balanced").model_profile == "balanced"


def test_config_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = AppConfig.default().with_hotkey("KEY_F8").with_runtime_mode("fast")

    save_config(config, path)
    loaded = load_config(path)

    assert loaded.hotkey.key == "KEY_F8"
    assert loaded.transcription.runtime_mode == "fast"
    assert loaded.transcription.model_profile == "fast"
