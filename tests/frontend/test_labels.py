"""Phase 3 review L7/L8: labels for duplicate headers and honest suggestion captions."""

from __future__ import annotations

from frontend.components.labels import column_labels, suggestion_caption

COLUMNS = [
    {"name": "nama", "original_name": "Nama"},
    {"name": "nama_2", "original_name": "Nama"},
    {"name": "column_3", "original_name": ""},
    {"name": "kota", "original_name": "Kota"},
]
CANDIDATES = [
    {"column": "nama", "original_name": "Nama", "confidence": 0.6, "reasons": ["Header"]},
    {"column": "kota", "original_name": "Kota", "confidence": 0.4, "reasons": ["Other"]},
]


def test_duplicate_headers_get_distinct_labels() -> None:
    labels = column_labels(COLUMNS)

    assert labels["nama"] != labels["nama_2"]
    assert labels["nama"] == "Nama (column 1)"
    assert labels["nama_2"] == "Nama (column 2)"
    assert labels["column_3"] == "(no header, column 3)"
    assert labels["kota"] == "Kota"


def test_caption_explains_the_selected_candidate() -> None:
    caption = suggestion_caption(CANDIDATES, "kota", column_labels(COLUMNS))

    assert "Kota" in caption and "40%" in caption


def test_caption_when_selection_differs_from_all_suggestions() -> None:
    caption = suggestion_caption(CANDIDATES, "column_3", column_labels(COLUMNS))
    visible = caption.replace("\\", "")  # captions are markdown-escaped

    assert "differs" in visible and "Nama (column 1)" in visible


def test_caption_without_suggestions() -> None:
    assert "No suggestion" in suggestion_caption([], None, {})
