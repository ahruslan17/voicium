from pathlib import Path

from voicium.config import AppConfig, default_config_path


def test_default_config_is_russian_push_to_talk() -> None:
    config = AppConfig.default()

    assert config.general.language == "ru"
    assert config.general.mode == "push_to_talk"
    assert config.hotkey.backend == "evdev"
    assert config.transcription.backend == "auto"
    assert config.transcription.model_profile == "russian"


def test_default_config_path_uses_user_config_directory() -> None:
    path = default_config_path()

    assert isinstance(path, Path)
    assert path.name == "config.toml"
    assert path.parent.name == "voicium"
