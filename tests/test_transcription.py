from pathlib import Path

import pytest

from voicium.transcription import (
    CommandResult,
    ModelSource,
    TranscriptionError,
    TranscriptionRequest,
    build_transcribe_command,
    clear_transformers_pipeline_cache,
    download_model,
    ensure_model_available,
    get_model_profile,
    get_transformers_pipeline,
    is_cuda_out_of_memory,
    model_path,
    resolve_transformers_device,
    transcribe,
)


def teardown_function() -> None:
    clear_transformers_pipeline_cache()


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


def test_download_huggingface_model_mentions_optional_extra(monkeypatch, tmp_path: Path) -> None:
    def fail_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "huggingface_hub":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fail_import)

    with pytest.raises(TranscriptionError, match="uv sync --extra transformers"):
        download_model("russian", model_dir=tmp_path)


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


def test_ensure_model_available_downloads_missing_whisper_cpp_model(
    monkeypatch, tmp_path: Path
) -> None:
    downloaded: list[tuple[str, Path | None]] = []

    def fake_download(profile_name: str, *, model_dir: Path | None = None):
        downloaded.append((profile_name, model_dir))
        path = model_path(profile_name, model_dir)
        path.write_text("model", encoding="utf-8")
        return path

    monkeypatch.setattr("voicium.transcription.download_model", fake_download)

    path = ensure_model_available("fast", tmp_path)

    assert path == tmp_path / "ggml-small-q5_1.bin"
    assert downloaded == [("fast", tmp_path)]


def test_build_transcribe_command_downloads_missing_model(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    binary_path = tmp_path / "whisper-cli"
    binary_path.write_text("binary", encoding="utf-8")

    def fake_download(profile_name: str, *, model_dir: Path | None = None):
        path = model_path(profile_name, model_dir)
        path.write_text("model", encoding="utf-8")
        return path

    monkeypatch.setattr("voicium.transcription.download_model", fake_download)

    command = build_transcribe_command(
        TranscriptionRequest(
            audio_path=audio_path,
            profile_name="fast",
            model_dir=tmp_path,
            whisper_binary=binary_path,
        )
    )

    assert str(tmp_path / "ggml-small-q5_1.bin") in command


def test_build_transcribe_command_fails_clearly_when_cuda_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("shutil.which", lambda command: None)
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")
    model_path("fast", tmp_path).write_text("model", encoding="utf-8")

    with pytest.raises(TranscriptionError, match="nvidia-smi not found"):
        build_transcribe_command(
            TranscriptionRequest(
                audio_path=audio_path,
                profile_name="fast",
                backend="cuda",
                model_dir=tmp_path,
            )
        )


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


def test_transformers_pipeline_is_cached(monkeypatch, tmp_path: Path) -> None:
    calls: list[Path | str] = []

    class FakeModel:
        @classmethod
        def from_pretrained(cls, model_reference, **_kwargs: object) -> "FakeModel":
            calls.append(model_reference)
            return cls()

        def eval(self) -> "FakeModel":
            return self

    class FakeProcessor:
        tokenizer = object()
        feature_extractor = object()

        @classmethod
        def from_pretrained(cls, _model_reference, **_kwargs: object) -> "FakeProcessor":
            return cls()

    def fake_pipeline(*_args: object, **_kwargs: object):
        return lambda *_call_args, **_call_kwargs: {"text": "привет"}

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "torch":
            return type(
                "Torch",
                (),
                {
                    "float16": object(),
                    "float32": object(),
                    "cuda": type(
                        "Cuda",
                        (),
                        {"is_available": lambda: False, "empty_cache": lambda: None},
                    ),
                },
            )
        if name == "transformers":
            return type(
                "Transformers",
                (),
                {
                    "WhisperForConditionalGeneration": FakeModel,
                    "WhisperProcessor": FakeProcessor,
                    "pipeline": fake_pipeline,
                },
            )
        return original_import(name, *args, **kwargs)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    first = get_transformers_pipeline(model_reference="model", cache_dir=tmp_path)
    second = get_transformers_pipeline(model_reference="model", cache_dir=tmp_path)
    cuda = get_transformers_pipeline(model_reference="model", cache_dir=tmp_path, device="cuda:0")

    assert first is second
    assert cuda is not first
    assert calls == ["model", "model"]


def test_resolve_transformers_device_uses_cuda_when_available() -> None:
    torch_module = type(
        "Torch",
        (),
        {"cuda": type("Cuda", (), {"is_available": lambda: True})},
    )

    assert resolve_transformers_device("auto", torch_module) == "cuda"
    assert resolve_transformers_device("cpu", torch_module) == "cpu"


def test_cuda_oom_detection() -> None:
    assert is_cuda_out_of_memory(RuntimeError("CUDA out of memory")) is True
    assert is_cuda_out_of_memory(RuntimeError("other error")) is False
