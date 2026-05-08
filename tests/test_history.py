from pathlib import Path

import pytest

from voicium.history import HistoryError, HistoryStore, format_history_entries


def test_history_store_adds_and_lists_entries(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.sqlite")

    entry = store.add(
        text="привет",
        raw_text=" привет ",
        model="russian",
        backend="auto",
        pasted=True,
    )
    entries = store.list()

    assert entry.id == 1
    assert entries == [entry]
    assert entries[0].pasted is True


def test_history_store_reports_missing_entry(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.sqlite")

    with pytest.raises(HistoryError, match="History entry not found"):
        store.get(42)


def test_format_history_entries_uses_single_line_preview(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.sqlite")
    entry = store.add(text="первая строка\nвторая строка")  # noqa: RUF001

    output = format_history_entries([entry])

    assert "1\t" in output
    assert "первая строка вторая строка" in output
