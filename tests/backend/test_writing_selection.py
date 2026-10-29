"""Strict, bounded saved-page overrides using browser UTF-16 selections."""

import pytest
from app.modules.documents.schemas import WritingBody, WritingDocumentCreate
from pydantic import ValidationError


def document(styles, body="a👩🏽‍💻क्षिe\u0301!"):
    return {"body": body, "text_styles": styles}


def test_empty_ranges_do_not_change_existing_create_hash_input():
    old = WritingDocumentCreate(title="A note", format="note", document=WritingBody())
    explicit = WritingDocumentCreate.model_validate(
        {"title": "A note", "format": "note", "document": {"text_styles": []}}
    )
    assert old.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "text_styles" not in old.model_dump(mode="json")["document"]


def test_valid_ranges_use_utf16_grapheme_boundaries_and_only_set_properties():
    body = WritingBody.model_validate(
        document(
            [
                {"field": "body", "start": 1, "end": 8, "font_size": 64},
                {"field": "body", "start": 8, "end": 12, "baseline_shift": -8, "weight": 700},
            ]
        )
    )
    dumped = body.model_dump(mode="json")["text_styles"]
    assert dumped[0] == {"field": "body", "start": 1, "end": 8, "font_size": 64}
    assert dumped[1]["baseline_shift"] == -8


@pytest.mark.parametrize(
    "style",
    [
        {"start": 2, "end": 8, "weight": 600},  # inside surrogate/emoji sequence
        {"start": 8, "end": 9, "weight": 600},  # inside Indic cluster
        {"start": 12, "end": 13, "weight": 600},  # inside combining sequence
        {"start": 0, "end": 200, "weight": 600},
        {"start": 1, "end": 1, "weight": 600},
        {"start": -1, "end": 1, "weight": 600},
        {"start": True, "end": 1, "weight": 600},
        {"start": "0", "end": 1, "weight": 600},
        {"start": 0, "end": 1},
        {"start": 0, "end": 1, "weight": 701},
        {"start": 0, "end": 1, "font_size": 121},
        {"start": 0, "end": 1, "font_size": float("nan")},
        {"start": 0, "end": 1, "spacing": 17},
        {"start": 0, "end": 1, "baseline_shift": -25},
        {"start": 0, "end": 1, "weight": 600, "html": "<script>"},
    ],
)
def test_invalid_styles_are_rejected(style):
    with pytest.raises(ValidationError):
        WritingBody.model_validate(document([{"field": "body", **style}]))


def test_overlaps_and_over_budget_lists_are_rejected():
    with pytest.raises(ValidationError):
        WritingBody.model_validate(
            document(
                [
                    {"field": "body", "start": 0, "end": 8, "weight": 600},
                    {"field": "body", "start": 1, "end": 8, "spacing": 2},
                ]
            )
        )
    with pytest.raises(ValidationError):
        WritingBody.model_validate(
            document(
                [{"field": "body", "start": i, "end": i + 1, "weight": 600} for i in range(129)],
                "x" * 129,
            )
        )


def test_title_uses_the_outer_document_text_not_the_body():
    with pytest.raises(ValidationError):
        WritingDocumentCreate.model_validate(
            {
                "title": "Hi",
                "format": "note",
                "document": document(
                    [
                        {"field": "title", "start": 0, "end": 3, "font_size": 64},
                    ]
                ),
            }
        )
