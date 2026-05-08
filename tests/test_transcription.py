from pathlib import Path

import pytest

from voicium.transcription import (
    CommandResult,
    ModelSource,
    TranscriptionError,
    TranscriptionRequest,
    build_transcribe_command,
    download_model,
    get_model_profile,
    model_path,
    transcribe,
)


def test_model_profiles_include_phase_two_profiles() -> None:
    assert get_model_profile("fast").filename == "ggml-small-q5_1.bin"
    assert get_model_profile("balanced").filename == "ggml-medium-q5_0.bin"
    assert get_model_profile("accurate").filename == "ggml-large-v3-turbo-q5_0.bin"
    assert get_model_profile("russian").source == ModelSource.HUGGINGFACE
    assert get_model_profile("russian").model_id == "antony66/whisper-large-v3-russian"


def test_model_path_uses_profile_filename(tmp_path: Path) -> None:
    path = model_path("fast", tmp_path)

    assert path == tmp_path / "ggml-small-q5_1.bin"


def test_model_path_uses_huggingface_model_id(tmp_path: Path) -> None:
    path = model_path("russian", tmp_path)

    assert path == tmp_path / "antony66--whisper-large-v3-russian"


def test_download_model_uses_profile_url_and_destination(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def downloader(url: str, destination: Path) -> object:
        calls.append((url, destination))
        destination.write_text("model", encoding="utf-8")
        return None

    path = download_model("fast", model_dir=tmp_path, downloader=downloader)

    assert path == tmp_path / "ggml-small-q5_1.bin"
    assert calls == [(get_model_profile("fast").url, path)]


def test_download_model_uses_huggingface_model_id(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []

    def downloader(source: str, destination: Path) -> object:
        calls.append((source, destination))
        destination.mkdir()
        return None

    path = download_model("russian", model_dir=tmp_path, downloader=downloader)

    assert path == tmp_path / "antony66--whisper-large-v3-russian"
    assert calls == [("antony66/whisper-large-v3-russian", path)]


def test_build_transcribe_command_rejects_huggingface_profile(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")

    with pytest.raises(TranscriptionError, match="Transformers backend"):
        build_transcribe_command(
            TranscriptionRequest(audio_path=audio_path, profile_name="russian")
        )


def test_build_transcribe_command_adds_russian_cpu_flags(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    binary_path = tmp_path / "whisper-cli"
    binary_path.write_text("binary", encoding="utf-8")
    model_path("fast", tmp_path).write_text("model", encoding="utf-8")

    command = build_transcribe_command(
        TranscriptionRequest(
            audio_path=audio_path,
            language="ru",
            profile_name="fast",
            backend="cpu",
            model_dir=tmp_path,
            whisper_binary=binary_path,
        )
    )

    assert command == [
        str(binary_path),
        "-m",
        str(tmp_path / "ggml-small-q5_1.bin"),
        "-f",
        str(audio_path),
        "-l",
        "ru",
        "--no-translate",
        "--print-progress",
        "false",
        "--print-timestamps",
        "false",
    ]


def test_build_transcribe_command_fails_when_model_is_missing(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")

    with pytest.raises(TranscriptionError, match="voicium models download fast"):
        build_transcribe_command(
            TranscriptionRequest(
                audio_path=audio_path,
                profile_name="fast",
                model_dir=tmp_path,
                whisper_binary=tmp_path / "whisper-cli",
            )
        )


def test_build_transcribe_command_rejects_cuda_in_phase_two(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")

    with pytest.raises(TranscriptionError, match="backend=auto or backend=cpu"):
        build_transcribe_command(TranscriptionRequest(audio_path=audio_path, backend="cuda"))


def test_transcribe_returns_whisper_output(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    binary_path = tmp_path / "whisper-cli"
    binary_path.write_text("binary", encoding="utf-8")
    model_path("fast", tmp_path).write_text("model", encoding="utf-8")

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=0, stdout="привет мир", stderr="")

    text = transcribe(
        TranscriptionRequest(
            audio_path=audio_path,
            profile_name="fast",
            model_dir=tmp_path,
            whisper_binary=binary_path,
        ),
        command_runner=runner,
    )

    assert text == "привет мир"


def test_transcribe_reports_whisper_failure(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    binary_path = tmp_path / "whisper-cli"
    binary_path.write_text("binary", encoding="utf-8")
    model_path("fast", tmp_path).write_text("model", encoding="utf-8")

    def runner(_command: list[str]) -> CommandResult:
        return CommandResult(returncode=1, stdout="", stderr="bad wav")

    with pytest.raises(TranscriptionError, match="bad wav"):
        transcribe(
            TranscriptionRequest(
                audio_path=audio_path,
                profile_name="fast",
                model_dir=tmp_path,
                whisper_binary=binary_path,
            ),
            command_runner=runner,
        )
