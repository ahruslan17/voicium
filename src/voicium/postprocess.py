from __future__ import annotations

import re
from collections.abc import Mapping

from voicium.config import DEFAULT_REPLACEMENTS

PUNCTUATION_COMMANDS: tuple[tuple[str, str], ...] = (
    ("точка с запятой", ";"),  # noqa: RUF001
    ("вопросительный знак", "?"),
    ("восклицательный знак", "!"),
    ("открой скобку", "("),
    ("закрой скобку", ")"),
    ("новая строка", "\n"),
    ("двоеточие", ":"),
    ("запятая", ","),
    ("точка", "."),
)


WHISPER_ARTIFACTS = (
    "[музыка]",
    "[аплодисменты]",
    "(музыка)",
    "(аплодисменты)",
)


def postprocess_russian(
    text: str,
    *,
    replacements: Mapping[str, str] | None = None,
) -> str:
    cleaned = remove_artifacts(text)
    cleaned = collapse_whitespace(cleaned)
    cleaned = apply_punctuation_commands(cleaned)
    cleaned = apply_replacements(cleaned, replacements or DEFAULT_REPLACEMENTS)
    return cleanup_punctuation_spacing(cleaned).strip()


def remove_artifacts(text: str) -> str:
    result = text
    for artifact in WHISPER_ARTIFACTS:
        result = result.replace(artifact, " ")
    return result


def collapse_whitespace(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def apply_punctuation_commands(text: str) -> str:
    result = text
    for phrase, replacement in PUNCTUATION_COMMANDS:
        result = re.sub(rf"\b{re.escape(phrase)}\b", replacement, result, flags=re.IGNORECASE)
    return result


def apply_replacements(text: str, replacements: Mapping[str, str]) -> str:
    result = text
    for source, target in replacements.items():
        result = re.sub(rf"\b{re.escape(source)}\b", target, result, flags=re.IGNORECASE)
    return result


def cleanup_punctuation_spacing(text: str) -> str:
    result = re.sub(r"\s+([,.:;?!])", r"\1", text)
    result = re.sub(r"[ \t]*\n[ \t]*", "\n", result)
    result = re.sub(r"([.!?])\s*\n\s*", r"\1\n", result)
    result = re.sub(r"\(\s+", "(", result)
    result = re.sub(r"\s+\)", ")", result)
    result = re.sub(r"[ \t]+", " ", result)
    return result
