from voicium.postprocess import postprocess_russian


def test_postprocess_converts_common_punctuation_commands() -> None:
    text = postprocess_russian(
        " привет запятая новая строка как дела вопросительный знак ",
        replacements={},
    )

    assert text == "привет,\nкак дела?"  # noqa: RUF001


def test_postprocess_applies_default_replacements() -> None:
    text = postprocess_russian("запусти опенкод и гитлаб")

    assert text == "запусти OpenCode и GitLab"


def test_postprocess_applies_custom_replacements() -> None:
    text = postprocess_russian("привет прод", replacements={"прод": "production"})

    assert text == "привет production"


def test_postprocess_removes_artifacts_and_collapses_whitespace() -> None:
    text = postprocess_russian("  привет   [музыка]   мир  ", replacements={})

    assert text == "привет мир"
