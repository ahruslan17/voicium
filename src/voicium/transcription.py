from __future__ import annotations

import subprocess
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from voicium.backend import BackendError, BackendName, select_backend


class TranscriptionError(RuntimeError):
    pass


class ModelSource(StrEnum):
    WHISPER_CPP = "whisper_cpp"
    HUGGINGFACE = "huggingface"


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    target: str
    source: ModelSource
    filename: str | None = None
    url: str | None = None
    model_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio_path: Path
    language: str = "auto"
    profile_name: str = "small-q8_0"
    backend: str = "auto"
    model_dir: Path | None = None
    whisper_binary: Path | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]
DownloadRunner = Callable[[str, Path], object]
TransformersPipeline = Callable[..., dict[str, object]]

_TRANSFORMERS_PIPELINES: dict[tuple[str, str, str], TransformersPipeline] = {}
TRANSFORMERS_DEVICE = "auto"


MODEL_PROFILES: dict[str, ModelProfile] = {
    "small-q8_0": ModelProfile(
        name="small-q8_0",
        target="default CPU quality/speed balance",
        source=ModelSource.WHISPER_CPP,
        filename="ggml-small-q8_0.bin",
        url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin",
    ),
    "small": ModelProfile(
        name="small",
        target="higher quality small model",
        source=ModelSource.WHISPER_CPP,
        filename="ggml-small.bin",
        url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
    ),
    "medium-q5_0": ModelProfile(
        name="medium-q5_0",
        target="higher quality balanced CPU mode",
        source=ModelSource.WHISPER_CPP,
        filename="ggml-medium-q5_0.bin",
        url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium-q5_0.bin",
    ),
    "large-v3-turbo-q5_0": ModelProfile(
        name="large-v3-turbo-q5_0",
        target="multilingual quality",
        source=ModelSource.WHISPER_CPP,
        filename="ggml-large-v3-turbo-q5_0.bin",
        url=(
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
        ),
    ),
    "russian": ModelProfile(
        name="russian",
        target="Russian dictation quality",
        source=ModelSource.HUGGINGFACE,
        model_id="antony66/whisper-large-v3-russian",
        url="https://huggingface.co/antony66/whisper-large-v3-russian",
    ),
}


def default_model_dir() -> Path:
    return Path.home() / ".local" / "share" / "voicium" / "models"


def get_model_profile(profile_name: str) -> ModelProfile:
    profile = MODEL_PROFILES.get(profile_name)
    if profile is None:
        available = ", ".join(sorted(MODEL_PROFILES))
        raise TranscriptionError(f"Unknown model profile '{profile_name}'. Available: {available}.")
    return profile


def model_path(profile_name: str, model_dir: Path | None = None) -> Path:
    profile = get_model_profile(profile_name)
    if profile.source == ModelSource.HUGGINGFACE:
        if profile.model_id is None:
            raise TranscriptionError(f"Model profile '{profile_name}' has no HuggingFace model id.")
        return (model_dir or default_model_dir()) / profile.model_id.replace("/", "--")

    if profile.filename is None:
        raise TranscriptionError(f"Model profile '{profile_name}' has no whisper.cpp filename.")
    return (model_dir or default_model_dir()) / profile.filename


def download_model(
    profile_name: str,
    *,
    model_dir: Path | None = None,
    downloader: DownloadRunner | None = None,
) -> Path:
    profile = get_model_profile(profile_name)
    destination = model_path(profile.name, model_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if downloader is not None:
        source = profile.model_id if profile.source == ModelSource.HUGGINGFACE else profile.url
        if source is None:
            raise TranscriptionError(f"Model profile '{profile_name}' has no download source.")
        downloader(source, destination)
        return destination

    if profile.source == ModelSource.HUGGINGFACE:
        if profile.model_id is None:
            raise TranscriptionError(f"Model profile '{profile_name}' has no HuggingFace model id.")
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise TranscriptionError(
                "huggingface_hub is not installed. Run `uv sync --extra transformers` "
                "before downloading HF models."
            ) from error
        snapshot_download(repo_id=profile.model_id, local_dir=destination)
        return destination

    if profile.url is None:
        raise TranscriptionError(f"Model profile '{profile_name}' has no download URL.")
    urllib.request.urlretrieve(profile.url, destination)
    return destination


def build_transcribe_command(request: TranscriptionRequest) -> list[str]:
    profile = get_model_profile(request.profile_name)
    if profile.source == ModelSource.HUGGINGFACE:
        raise TranscriptionError(
            "HuggingFace profiles run through the Transformers backend and do not have a "
            "whisper.cpp command."
        )

    if not request.audio_path.exists():
        raise TranscriptionError(f"Audio file not found: {request.audio_path}")

    model = ensure_model_available(request.profile_name, request.model_dir)

    try:
        backend_selection = select_backend(
            request.backend,
            explicit_binary=request.whisper_binary,
        )
    except BackendError as error:
        raise TranscriptionError(str(error)) from error

    if backend_selection.binary_path is None:
        raise TranscriptionError("Selected backend has no whisper.cpp binary path.")

    return [
        str(backend_selection.binary_path),
        "-m",
        str(model),
        "-f",
        str(request.audio_path),
        "-np",
        "-nt",
        *low_latency_decode_args(profile.name),
        *language_args(request.language),
    ]


def transcribe(
    request: TranscriptionRequest,
    *,
    command_runner: CommandRunner | None = None,
) -> str:
    profile = get_model_profile(request.profile_name)
    if profile.source == ModelSource.HUGGINGFACE:
        return transcribe_with_transformers(request)

    command = build_transcribe_command(request)
    runner = command_runner or run_command
    result = runner(command)
    if result.returncode != 0:
        details = result.stderr or result.stdout or "whisper.cpp failed"
        raise TranscriptionError(f"Transcription failed: {details}")

    text = clean_whisper_output(result.stdout)
    if not text:
        raise TranscriptionError("Transcription produced empty output.")
    return text


def ensure_model_available(profile_name: str, model_dir: Path | None = None) -> Path:
    profile = get_model_profile(profile_name)
    path = model_path(profile.name, model_dir)
    if path.exists():
        return path
    if profile.source == ModelSource.HUGGINGFACE:
        return path
    return download_model(profile.name, model_dir=model_dir)


def clean_whisper_output(output: str) -> str:
    lines = [line.strip() for line in output.splitlines()]
    text = " ".join(line for line in lines if line and line != "[BLANK_AUDIO]").strip()
    return " ".join(text.split())


def low_latency_decode_args(profile_name: str) -> list[str]:
    if profile_name == "small-q8_0":
        return ["-nf"]
    return []


def transcribe_with_transformers(request: TranscriptionRequest) -> str:
    if request.backend == BackendName.CUDA.value:
        raise TranscriptionError("CUDA backend is only supported by whisper.cpp profiles.")
    if not request.audio_path.exists():
        raise TranscriptionError(f"Audio file not found: {request.audio_path}")

    profile = get_model_profile(request.profile_name)
    if profile.model_id is None:
        raise TranscriptionError(
            f"Model profile '{request.profile_name}' has no HuggingFace model id."
        )

    local_model_path = model_path(profile.name, request.model_dir)
    model_reference = local_model_path if local_model_path.exists() else profile.model_id
    cache_dir = request.model_dir or default_model_dir()
    try:
        asr_pipeline = get_transformers_pipeline(
            model_reference=model_reference,
            cache_dir=cache_dir,
            device=TRANSFORMERS_DEVICE,
        )
        result = run_transformers_pipeline(asr_pipeline, request)
    except RuntimeError as error:
        if not is_cuda_out_of_memory(error):
            raise
        clear_cuda_cache()
        clear_transformers_pipeline_cache()
        asr_pipeline = get_transformers_pipeline(
            model_reference=model_reference,
            cache_dir=cache_dir,
            device="cpu",
        )
        result = run_transformers_pipeline(asr_pipeline, request)
    text = str(result.get("text", "")).strip()
    if not text:
        raise TranscriptionError("Transcription produced empty output.")
    return text


def get_transformers_pipeline(
    *, model_reference: str | Path, cache_dir: Path, device: str = TRANSFORMERS_DEVICE
) -> TransformersPipeline:
    cache_key = (str(model_reference), str(cache_dir), device)
    cached = _TRANSFORMERS_PIPELINES.get(cache_key)
    if cached is not None:
        return cached

    try:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline
    except ImportError as error:
        raise TranscriptionError(
            "Transformers backend dependencies are not installed. "
            "Run `uv sync --extra transformers`."
        ) from error

    resolved_device = resolve_transformers_device(device, torch)
    if resolved_device == "cuda":
        torch.cuda.empty_cache()

    model = WhisperForConditionalGeneration.from_pretrained(
        model_reference,
        torch_dtype=torch.float16 if resolved_device == "cuda" else torch.float32,
        low_cpu_mem_usage=True,
        use_safetensors=True,
        device_map="auto" if resolved_device == "cuda" else None,
        cache_dir=cache_dir,
    ).eval()
    processor = WhisperProcessor.from_pretrained(
        model_reference,
        cache_dir=cache_dir,
    )
    asr_pipeline = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        device=-1 if resolved_device == "cpu" else None,
        max_new_tokens=256,
        chunk_length_s=30,
        batch_size=1,
        return_timestamps=False,
        ignore_warning=True,
    )
    _TRANSFORMERS_PIPELINES[cache_key] = asr_pipeline
    return asr_pipeline


def clear_transformers_pipeline_cache() -> None:
    _TRANSFORMERS_PIPELINES.clear()


def run_transformers_pipeline(
    asr_pipeline: TransformersPipeline,
    request: TranscriptionRequest,
) -> dict[str, object]:
    return asr_pipeline(
        str(request.audio_path),
        generate_kwargs=transformers_generate_kwargs(request.language),
        return_timestamps=False,
        chunk_length_s=30,
        batch_size=1,
    )


def language_args(language: str) -> list[str]:
    selected_language = "ru" if language == "auto" else language
    return ["-l", selected_language]


def transformers_generate_kwargs(language: str) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "max_new_tokens": 256,
        "num_beams": 1,
        "temperature": 0.0,
        "use_cache": True,
    }
    if language != "auto":
        kwargs["language"] = "russian" if language == "ru" else language
    return kwargs


def resolve_transformers_device(device: str, torch_module: object) -> str:
    if device != "auto":
        return device
    cuda = getattr(torch_module, "cuda", None)
    if cuda is not None and cuda.is_available():
        return "cuda"
    return "cpu"


def is_cuda_out_of_memory(error: RuntimeError) -> bool:
    message = str(error).lower()
    return "cuda" in message and "out of memory" in message


def clear_cuda_cache() -> None:
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def run_command(args: Sequence[str]) -> CommandResult:
    completed = subprocess.run(
        args,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )
