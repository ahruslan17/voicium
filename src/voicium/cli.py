from __future__ import annotations

import argparse
import tempfile
from collections.abc import Sequence
from pathlib import Path

from voicium import __version__
from voicium.audio import AudioError, list_input_devices, record_wav
from voicium.backend import BackendError, run_cuda_smoke_test, select_backend
from voicium.config import AppConfig, PasteConfig, default_config_path, load_config
from voicium.daemon import DaemonCommand, DaemonError, DaemonService, send_command
from voicium.healthcheck import has_failures, render_results
from voicium.healthcheck import run_healthcheck as collect_healthcheck
from voicium.history import HistoryError, HistoryStore, format_history_entries
from voicium.paste import PasteMode, insert_or_copy
from voicium.transcription import (
    TranscriptionError,
    TranscriptionRequest,
    download_model,
    transcribe,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voicium",
        description="Fast Russian push-to-talk dictation for Ubuntu.",
    )
    parser.add_argument("--version", action="version", version=f"voicium {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    healthcheck_parser = subparsers.add_parser(
        "healthcheck",
        help="Print Ubuntu environment diagnostics.",
    )
    healthcheck_parser.set_defaults(handler=run_healthcheck)

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Transcribe a WAV file with local whisper.cpp CPU runtime.",
    )
    transcribe_parser.add_argument("audio_path", type=Path)
    transcribe_parser.add_argument("--lang", default="auto")
    transcribe_parser.add_argument("--profile", default="small-q8_0")
    transcribe_parser.add_argument("--backend", choices=("auto", "cpu", "cuda"), default="auto")
    transcribe_parser.add_argument("--model-dir", type=Path)
    transcribe_parser.add_argument("--whisper-bin", type=Path)
    transcribe_parser.set_defaults(handler=run_transcribe)

    audio_parser = subparsers.add_parser("audio", help="Inspect audio input devices.")
    audio_subparsers = audio_parser.add_subparsers(dest="audio_command")
    audio_inputs_parser = audio_subparsers.add_parser("inputs", help="List microphone inputs.")
    audio_inputs_parser.set_defaults(handler=list_audio_inputs_command)

    record_parser = subparsers.add_parser("record", help="Record microphone audio to WAV.")
    record_parser.add_argument("output_path", type=Path)
    record_parser.add_argument("--duration", type=int, default=5)
    record_parser.add_argument("--device")
    record_parser.set_defaults(handler=record_command)

    record_transcribe_parser = subparsers.add_parser(
        "record-transcribe",
        help="Record microphone audio to a temporary WAV and transcribe it.",
    )
    record_transcribe_parser.add_argument("--duration", type=int, default=5)
    record_transcribe_parser.add_argument("--device")
    record_transcribe_parser.add_argument("--keep-audio", type=Path)
    record_transcribe_parser.add_argument("--lang", default="auto")
    record_transcribe_parser.add_argument("--profile", default="small-q8_0")
    record_transcribe_parser.add_argument(
        "--backend",
        choices=("auto", "cpu", "cuda"),
        default="auto",
    )
    record_transcribe_parser.add_argument("--model-dir", type=Path)
    record_transcribe_parser.add_argument("--whisper-bin", type=Path)
    record_transcribe_parser.set_defaults(handler=record_transcribe_command)

    models_parser = subparsers.add_parser("models", help="Manage local Whisper models.")
    models_subparsers = models_parser.add_subparsers(dest="models_command")
    models_download_parser = models_subparsers.add_parser(
        "download",
        help="Download a whisper.cpp model profile.",
    )
    models_download_parser.add_argument(
        "profile",
        choices=("small-q8_0", "small", "medium-q5_0", "large-v3-turbo-q5_0", "russian"),
    )
    models_download_parser.add_argument("--model-dir", type=Path)
    models_download_parser.set_defaults(handler=download_model_command)

    backend_parser = subparsers.add_parser("backend", help="Inspect transcription backends.")
    backend_subparsers = backend_parser.add_subparsers(dest="backend_command")
    backend_select_parser = backend_subparsers.add_parser(
        "select",
        help="Print selected backend for auto/cpu/cuda mode.",
    )
    backend_select_parser.add_argument("--backend", choices=("auto", "cpu", "cuda"), default="auto")
    backend_select_parser.add_argument("--whisper-bin", type=Path)
    backend_select_parser.set_defaults(handler=select_backend_command)
    backend_smoke_parser = backend_subparsers.add_parser(
        "cuda-smoke-test",
        help="Check NVIDIA and CUDA whisper.cpp binary availability.",
    )
    backend_smoke_parser.add_argument("--whisper-bin", type=Path)
    backend_smoke_parser.set_defaults(handler=cuda_smoke_test_command)

    daemon_parser = subparsers.add_parser("daemon", help="Run the Voicium daemon foreground loop.")
    daemon_parser.set_defaults(handler=daemon_command)

    start_parser = subparsers.add_parser("start", help="Tell the daemon to start recording.")
    start_parser.set_defaults(handler=start_recording_command)

    stop_parser = subparsers.add_parser("stop", help="Tell the daemon to stop recording.")
    stop_parser.set_defaults(handler=stop_recording_command)

    status_parser = subparsers.add_parser("status", help="Print daemon status.")
    status_parser.set_defaults(handler=status_command)

    reload_parser = subparsers.add_parser("reload", help="Reload daemon configuration.")
    reload_parser.set_defaults(handler=reload_command)

    history_parser = subparsers.add_parser("history", help="Inspect transcription history.")
    history_subparsers = history_parser.add_subparsers(dest="history_command")
    history_list_parser = history_subparsers.add_parser("list", help="List recent transcriptions.")
    history_list_parser.add_argument("--limit", type=int, default=20)
    history_list_parser.set_defaults(handler=history_list_command)
    history_copy_parser = history_subparsers.add_parser("copy", help="Copy a history item.")
    history_copy_parser.add_argument("id", type=int)
    history_copy_parser.set_defaults(handler=history_copy_command)
    history_repeat_parser = history_subparsers.add_parser(
        "repeat",
        help="Paste or copy a history item again.",
    )
    history_repeat_parser.add_argument("id", type=int)
    history_repeat_parser.set_defaults(handler=history_repeat_command)

    config_parser = subparsers.add_parser("config", help="Inspect Voicium configuration.")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_show_parser = config_subparsers.add_parser("show", help="Print default config values.")
    config_show_parser.set_defaults(handler=show_config)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    return int(handler(args))


def run_healthcheck(_args: argparse.Namespace) -> int:
    config_path = default_config_path()
    print(f"Config path: {config_path}")
    results = collect_healthcheck()
    print(render_results(results))
    return 1 if has_failures(results) else 0


def show_config(_args: argparse.Namespace) -> int:
    config = load_config()
    print(config.to_toml())
    return 0


def run_transcribe(args: argparse.Namespace) -> int:
    request = TranscriptionRequest(
        audio_path=args.audio_path,
        language=args.lang,
        profile_name=args.profile,
        backend=args.backend,
        model_dir=args.model_dir,
        whisper_binary=args.whisper_bin,
    )
    try:
        print(transcribe(request))
    except TranscriptionError as error:
        print(f"error: {error}")
        return 1
    return 0


def download_model_command(args: argparse.Namespace) -> int:
    try:
        path = download_model(args.profile, model_dir=args.model_dir)
    except TranscriptionError as error:
        print(f"error: {error}")
        return 1
    print(f"Downloaded model: {path}")
    return 0


def select_backend_command(args: argparse.Namespace) -> int:
    try:
        selection = select_backend(args.backend, explicit_binary=args.whisper_bin)
    except BackendError as error:
        print(f"error: {error}")
        return 1

    print(f"Selected backend: {selection.backend.value}")
    print(f"Reason: {selection.reason}")
    if selection.binary_path is not None:
        print(f"Binary: {selection.binary_path}")
    if selection.gpu is not None:
        print(f"GPU: {selection.gpu.name}")
    return 0


def cuda_smoke_test_command(args: argparse.Namespace) -> int:
    try:
        selection = run_cuda_smoke_test(binary_path=args.whisper_bin)
    except BackendError as error:
        print(f"error: {error}")
        return 1

    print(f"Selected backend: {selection.backend.value}")
    print(f"Reason: {selection.reason}")
    if selection.binary_path is not None:
        print(f"Binary: {selection.binary_path}")
    if selection.gpu is not None:
        print(f"GPU: {selection.gpu.name}")
    print("CUDA smoke-test passed")
    return 0


def daemon_command(_args: argparse.Namespace) -> int:
    return DaemonService(config=load_config()).serve_forever()


def start_recording_command(_args: argparse.Namespace) -> int:
    return _daemon_client_command(DaemonCommand.START_RECORDING, timeout=2.0)


def stop_recording_command(_args: argparse.Namespace) -> int:
    return _daemon_client_command(DaemonCommand.STOP_RECORDING, timeout=300.0)


def status_command(_args: argparse.Namespace) -> int:
    return _daemon_client_command(DaemonCommand.STATUS, timeout=2.0)


def reload_command(_args: argparse.Namespace) -> int:
    return _daemon_client_command(DaemonCommand.RELOAD_CONFIG, timeout=2.0)


def history_list_command(args: argparse.Namespace) -> int:
    entries = HistoryStore().list(limit=args.limit)
    if not entries:
        print("History is empty.")
        return 0
    print(format_history_entries(entries))
    return 0


def history_copy_command(args: argparse.Namespace) -> int:
    return _history_insert_command(args.id, auto_paste=False)


def history_repeat_command(args: argparse.Namespace) -> int:
    return _history_insert_command(args.id, auto_paste=True)


def _history_insert_command(entry_id: int, *, auto_paste: bool) -> int:
    try:
        entry = HistoryStore().get(entry_id)
        config = AppConfig.default().paste
        paste_config = PasteConfig(
            auto_paste=auto_paste,
            restore_clipboard=config.restore_clipboard,
            restore_delay_ms=config.restore_delay_ms,
            fallback_to_clipboard=config.fallback_to_clipboard,
            notify=config.notify,
        )
        result = insert_or_copy(entry.text, config=paste_config)
    except HistoryError as error:
        print(f"error: {error}")
        return 1

    print(f"History item {entry.id}: {result.mode.value}")
    print(result.message)
    return 0 if result.mode != PasteMode.FAILED else 1


def _daemon_client_command(command: DaemonCommand, *, timeout: float) -> int:
    try:
        response = send_command(command.value, timeout=timeout)
    except DaemonError as error:
        print(f"error: {error}")
        return 1

    print(f"State: {response.state.value}")
    print(f"Message: {response.message}")
    if response.transcript is not None:
        print(response.transcript)
    return 0 if response.ok else 1


def list_audio_inputs_command(_args: argparse.Namespace) -> int:
    try:
        devices = list_input_devices()
    except AudioError as error:
        print(f"error: {error}")
        return 1

    if not devices:
        print("No microphone inputs found.")
        return 0

    for device in devices:
        print(f"{device.name}\t{device.description}")
    return 0


def record_command(args: argparse.Namespace) -> int:
    try:
        path = record_wav(
            args.output_path,
            duration_seconds=args.duration,
            device=args.device,
        )
    except AudioError as error:
        print(f"error: {error}")
        return 1
    print(f"Recorded WAV: {path}")
    return 0


def record_transcribe_command(args: argparse.Namespace) -> int:
    try:
        if args.keep_audio is not None:
            audio_path = record_wav(
                args.keep_audio,
                duration_seconds=args.duration,
                device=args.device,
            )
            print(transcribe(_transcription_request(args, audio_path)))
            return 0

        with tempfile.TemporaryDirectory(prefix="voicium-") as temp_dir:
            audio_path = record_wav(
                Path(temp_dir) / "recording.wav",
                duration_seconds=args.duration,
                device=args.device,
            )
            print(transcribe(_transcription_request(args, audio_path)))
            return 0
    except (AudioError, TranscriptionError) as error:
        print(f"error: {error}")
        return 1


def _transcription_request(args: argparse.Namespace, audio_path: Path) -> TranscriptionRequest:
    return TranscriptionRequest(
        audio_path=audio_path,
        language=args.lang,
        profile_name=args.profile,
        backend=args.backend,
        model_dir=args.model_dir,
        whisper_binary=args.whisper_bin,
    )
