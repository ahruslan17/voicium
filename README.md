# Voicium

Fast Russian push-to-talk dictation for Ubuntu, inspired by VoiceInk.

The target product is a GitLab/GitHub-buildable application with local Whisper transcription, NVIDIA CUDA support when available, CPU fallback, GNOME Wayland-aware input handling, and optional OpenAI-compatible text enhancement.

## Development

Install dependencies and run checks:

```bash
uv sync
uv run ruff check
uv run ruff format --check
uv run pytest
uv build
```

Run the Phase 0 CLI skeleton:

```bash
uv run voicium --help
uv run voicium healthcheck
uv run voicium config show
```

See the detailed MVP plan:

- [docs/ubuntu-mvp-plan.md](docs/ubuntu-mvp-plan.md)
- [docs/ubuntu-advanced-architecture.md](docs/ubuntu-advanced-architecture.md)
