from pathlib import Path

from voicium.config import (
    AppConfig,
    RuntimeMode,
    default_config_path,
    load_config,
    save_config,
    transcription_for_runtime_mode,
)


def test_default_config_uses_auto_language_push_to_talk() -> None:
    config = AppConfig.default()

    assert config.general.language == "ru"
    assert config.general.mode == "push_to_talk"
    assert config.hotkey.backend == "evdev"
    assert config.audio.input_device is None
    assert config.transcription.backend == "auto"
    assert config.transcription.model_profile == "fast"
    assert config.transcription.runtime_mode == RuntimeMode.FAST.value
    assert config.paste.auto_paste is False
    assert config.paste.fallback_to_clipboard is True
    assert config.russian.replacements["опенкод"] == "OpenCode"


def test_default_config_path_uses_user_config_directory() -> None:
    path = default_config_path()

    assert isinstance(path, Path)
    assert path.name == "config.toml"
    assert path.parent.name == "voicium"


def test_transcription_for_runtime_mode_maps_profiles() -> None:
    assert transcription_for_runtime_mode("quality").model_profile == "accurate"
    assert transcription_for_runtime_mode("fast").model_profile == "fast"
    assert transcription_for_runtime_mode("balanced").model_profile == "balanced"


def test_config_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = (
        AppConfig.default()
        .with_hotkey("KEY_F8")
        .with_runtime_mode("fast")
        .with_audio_input_device("alsa_input.test")
        .with_auto_paste(True)
    )

    save_config(config, path)
    loaded = load_config(path)

    assert loaded.hotkey.key == "KEY_F8"
    assert loaded.audio.input_device == "alsa_input.test"
    assert loaded.paste.auto_paste is True
    assert loaded.transcription.runtime_mode == "fast"
    assert loaded.transcription.model_profile == "fast"


def test_config_loads_audio_input_device(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[audio]",
                'input_device = "alsa_input.usb-test.analog-stereo"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_config(path)

    assert loaded.audio.input_device == "alsa_input.usb-test.analog-stereo"
