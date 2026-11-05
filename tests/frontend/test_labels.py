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


def test_keep_as_text_conflicts() -> None:
    """Task 21: warn in the import form before dates or numbers are kept as text."""
    from frontend.components.labels import keep_as_text_conflicts

    sheets = [
        {"name": "A", "detected_types": {"Tanggal": "date", "Nilai": "numeric", "ID": "text"}},
        {"name": "B", "detected_types": {"Waktu": "datetime", "Jumlah": "integer"}},
    ]

    dates, numbers = keep_as_text_conflicts(sheets, ["A"], ["Tanggal", "Nilai", "ID", "Waktu"])

    assert dates == ["Tanggal"]  # "Waktu" is only in sheet B, which is not imported
    assert numbers == ["Nilai"]


def test_keep_as_text_conflicts_csv_and_old_backend() -> None:
    from frontend.components.labels import keep_as_text_conflicts

    csv = [{"name": "data", "detected_types": {"Tgl": "date"}}]
    old = [{"name": "data"}]  # inspection without detected_types

    assert keep_as_text_conflicts(csv, [None], ["Tgl"]) == (["Tgl"], [])
    assert keep_as_text_conflicts(old, [None], ["Tgl"]) == ([], [])


def test_fmt_stat_keeps_small_fractions() -> None:
    """Phase 6 review L3: profile Min/Mean/Median showed 0.004 as "0,00"."""
    from frontend.components.formatting import fmt_stat

    assert fmt_stat(None) == ""
    assert fmt_stat(4000000.0) == "4.000.000"
    assert fmt_stat(38247512.5) == "38.247.512,50"
    assert fmt_stat(0.0045) == "0,0045"
    assert fmt_stat(-0.000123456) == "-0,000123"


# --- smart import -----------------------------------------------------------------------
def test_layout_summary_for_the_import_form() -> None:
    from frontend.components.labels import layout_summary

    plain = {"header_row": 1, "header_detected": True, "ignored_columns": [], "template_rows": 0}
    sheet = {
        "header_row": 4,
        "header_detected": True,
        "ignored_columns": ["column 58", "Daftar hari libur"],
        "template_rows": 320,
    }

    assert layout_summary(plain) is None  # nothing to say for a plain table
    text = layout_summary(sheet)
    assert "row 4" in text and "Daftar hari libur" in text and "320" in text


def test_import_layout_caption_for_a_dataset() -> None:
    from frontend.components.labels import import_layout_caption

    assert import_layout_caption(None) is None
    assert (
        import_layout_caption(
            {
                "header_row": 1,
                "header_chosen": False,
                "ignored_columns": [],
                "template_rows_skipped": 0,
            }
        )
        is None
    )
    text = import_layout_caption(
        {
            "header_row": 4,
            "header_chosen": True,
            "ignored_columns": ["Daftar hari libur"],
            "template_rows_skipped": 320,
        }
    )
    assert "row 4 (chosen)" in text and "320" in text and "Daftar hari libur" in text


def test_columns_summary() -> None:
    from frontend.components.labels import columns_summary

    columns = [{"logical_type": t} for t in ["date"] * 11 + ["integer", "numeric"] + ["text"] * 41]

    assert columns_summary(columns) == "54 columns read: 11 dates, 2 numbers, 41 text"
    assert columns_summary([{"logical_type": "boolean"}]) == "1 column read: 1 yes/no"
