"""ARCH-11 Step 3 — chunking, offsets, and bounding boxes unit test suite."""

from __future__ import annotations

import re
import pytest

from app.services.chunking_service import (
    DEFAULT_CHUNK_OVERLAP_PCT,
    DEFAULT_CHUNK_SIZE_TOKENS,
    ChunkingError,
    chunking_summary,
    split_pages,
)
from app.services.document_models import (
    BBOX_FROM_BLOCKS,
    BBOX_NO_BLOCKS,
    BBOX_NO_INTERSECTION,
    BBOX_SPAN_MISMATCH,
    DocumentPage,
    union_box,
)

pytestmark = pytest.mark.no_db


class FakeTokenizer:
    """One token per whitespace-delimited word, with true character offsets."""

    def __call__(
        self,
        text,
        add_special_tokens=False,
        truncation=False,
        padding=False,
        return_offsets_mapping=False,
        verbose=False,
    ):
        if isinstance(text, list):
            return {
                "input_ids": [[0] * max(1, len(item.split())) for item in text]
            }
        offsets = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        payload = {"input_ids": [0] * len(offsets)}
        if return_offsets_mapping:
            payload["offset_mapping"] = offsets
        return payload


class NoOffsetTokenizer(FakeTokenizer):
    """A slow tokenizer: no offset mapping, forcing the word-walk fallback."""

    def __call__(self, text, return_offsets_mapping=False, **kwargs):
        if return_offsets_mapping:
            raise NotImplementedError("slow tokenizer")
        return super().__call__(text, **kwargs)


@pytest.fixture()
def tokenizer():
    return FakeTokenizer()


def _words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def _boxed_page(sentences, *, page_number=1, width=1700, height=2200):
    blocks = [
        {
            "text": sentence,
            "confidence": 0.9 + index / 100,
            "box": {
                "x0": 10.0,
                "y0": 20.0 + index * 40,
                "x1": 600.0 + index,
                "y1": 48.0 + index * 40,
            },
        }
        for index, sentence in enumerate(sentences)
    ]
    return DocumentPage(
        page_number=page_number,
        text="\n".join(sentences),
        blocks=blocks,
        width=width,
        height=height,
    )


# ===========================================================================
# Sizing
# ===========================================================================


def test_no_chunk_exceeds_the_token_budget(tokenizer):
    page = DocumentPage(page_number=1, text=_words(1000))
    chunks = split_pages(
        [page], tokenizer=tokenizer, chunk_size_tokens=64, chunk_overlap_pct=10
    )
    assert chunks
    assert all(chunk.token_count <= 64 for chunk in chunks)


def test_budget_leaves_headroom_for_special_tokens():
    from app.core.embeddings import resolve_spec

    window = resolve_spec("sentence-transformers/all-MiniLM-L6-v2").max_sequence_tokens
    assert DEFAULT_CHUNK_SIZE_TOKENS + 2 < window


def test_short_page_is_one_chunk(tokenizer):
    page = DocumentPage(page_number=1, text=_words(20))
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=64)
    assert len(chunks) == 1
    assert chunks[0].token_count == 20


def test_chunk_index_is_document_wide_and_contiguous(tokenizer):
    pages = [
        DocumentPage(page_number=1, text=_words(200, "a")),
        DocumentPage(page_number=2, text=_words(200, "b")),
    ]
    chunks = split_pages(pages, tokenizer=tokenizer, chunk_size_tokens=64)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert {chunk.page_number for chunk in chunks} == {1, 2}


def test_chunks_never_span_a_page_boundary(tokenizer):
    pages = [
        DocumentPage(page_number=1, text=_words(10, "a")),
        DocumentPage(page_number=2, text=_words(10, "b")),
    ]
    chunks = split_pages(pages, tokenizer=tokenizer, chunk_size_tokens=200)
    assert len(chunks) == 2
    assert "b0" not in chunks[0].content
    assert "a0" not in chunks[1].content


@pytest.mark.parametrize("bad", [0, 8, 31])
def test_budget_below_the_floor_is_refused(tokenizer, bad):
    with pytest.raises(ChunkingError, match="floor"):
        split_pages(
            [DocumentPage(1, _words(50))], tokenizer=tokenizer, chunk_size_tokens=bad
        )


@pytest.mark.parametrize("bad", [50, 60, -1])
def test_overlap_outside_range_is_refused(tokenizer, bad):
    with pytest.raises(ChunkingError):
        split_pages(
            [DocumentPage(1, _words(50))],
            tokenizer=tokenizer,
            chunk_size_tokens=64,
            chunk_overlap_pct=bad,
        )


# ===========================================================================
# Overlap
# ===========================================================================


def test_consecutive_chunks_overlap_by_roughly_the_configured_share(tokenizer):
    page = DocumentPage(page_number=1, text=_words(600))
    chunks = split_pages(
        [page], tokenizer=tokenizer, chunk_size_tokens=100, chunk_overlap_pct=10
    )
    assert len(chunks) >= 3
    for previous, current in zip(chunks, chunks[1:]):
        assert current.page_start_char < previous.page_end_char, (
            "chunks must overlap, not merely abut"
        )


def test_zero_overlap_produces_abutting_chunks(tokenizer):
    page = DocumentPage(page_number=1, text=_words(300))
    chunks = split_pages(
        [page], tokenizer=tokenizer, chunk_size_tokens=64, chunk_overlap_pct=0
    )
    for previous, current in zip(chunks, chunks[1:]):
        assert current.page_start_char >= previous.page_end_char


def test_chunking_always_terminates_on_pathological_overlap(tokenizer):
    page = DocumentPage(page_number=1, text=_words(500))
    chunks = split_pages(
        [page], tokenizer=tokenizer, chunk_size_tokens=32, chunk_overlap_pct=40
    )
    assert 0 < len(chunks) < 500


# ===========================================================================
# Offsets — the invariant
# ===========================================================================


def test_content_is_exactly_the_recorded_span(tokenizer):
    text = "\n\n".join(_words(40, f"p{index}_") for index in range(6))
    page = DocumentPage(page_number=4, text=text)
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=48)
    assert chunks
    for chunk in chunks:
        assert chunk.content == text[chunk.page_start_char : chunk.page_end_char]


def test_spans_are_ordered_and_within_the_page(tokenizer):
    text = _words(400)
    chunks = split_pages(
        [DocumentPage(1, text)], tokenizer=tokenizer, chunk_size_tokens=64
    )
    for chunk in chunks:
        assert 0 <= chunk.page_start_char < chunk.page_end_char <= len(text)


def test_the_whole_page_is_covered(tokenizer):
    text = "\n\n".join(_words(30, f"s{index}_") for index in range(8))
    chunks = split_pages(
        [DocumentPage(1, text)], tokenizer=tokenizer, chunk_size_tokens=48
    )
    covered = chunks[0].page_end_char
    for chunk in chunks[1:]:
        assert chunk.page_start_char <= covered, "gap in coverage"
        covered = max(covered, chunk.page_end_char)
    assert text[covered:].strip() == ""


def test_page_text_is_not_normalised(tokenizer):
    text = "alpha   beta\n\n\n\ngamma\tdelta"
    chunks = split_pages(
        [DocumentPage(1, text)], tokenizer=tokenizer, chunk_size_tokens=64
    )
    assert chunks[0].content == text[
        chunks[0].page_start_char : chunks[0].page_end_char
    ]
    assert "   " in "".join(chunk.content for chunk in chunks)


def test_word_offset_fallback_preserves_the_invariant():
    text = _words(200)
    chunks = split_pages(
        [DocumentPage(1, text)],
        tokenizer=NoOffsetTokenizer(),
        chunk_size_tokens=48,
    )
    assert chunks
    for chunk in chunks:
        assert chunk.content == text[chunk.page_start_char : chunk.page_end_char]


# ===========================================================================
# Bounding boxes
# ===========================================================================


def test_block_spans_map_onto_the_page_text():
    page = _boxed_page(["First sentence here.", "Second sentence here."])
    spans, status = page.block_spans()
    assert status == BBOX_FROM_BLOCKS
    assert [(span.start, span.end) for span in spans] == [(0, 20), (21, 42)]
    assert page.text[spans[1].start : spans[1].end] == "Second sentence here."


def test_bbox_is_the_union_of_intersecting_blocks(tokenizer):
    page = _boxed_page(
        [
            "Full-time employees accrue annual leave each completed month of service.",
            "A maximum of five unused days may be carried into the following year.",
            "Requests must be approved by a line manager before leave is taken.",
        ],
        page_number=3,
    )
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=200)
    assert len(chunks) == 1
    box = chunks[0].bbox
    assert box is not None
    assert box["page"] == 3
    assert box["x0"] == 10.0
    assert box["y0"] == 20.0
    assert box["y1"] == 128.0
    assert len(box["blocks"]) == 3
    assert chunks[0].bbox_source == BBOX_FROM_BLOCKS


def test_bbox_carries_the_raster_dimensions(tokenizer):
    page = _boxed_page(["One sentence only here."], width=1700, height=2200)
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=200)
    assert chunks[0].bbox["width"] == 1700
    assert chunks[0].bbox["height"] == 2200
    assert chunks[0].bbox["space"] == "pixels"


def test_a_chunk_only_carries_the_blocks_it_overlaps(tokenizer):
    page = _boxed_page([f"Sentence number {index} with several words." for index in range(6)])
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=32)
    assert len(chunks) > 1
    first, last = chunks[0], chunks[-1]
    assert first.bbox["y0"] < last.bbox["y0"], "later chunks sit lower on the page"
    assert len(first.bbox["blocks"]) < 6


def test_text_layer_pages_have_no_boxes_and_say_so(tokenizer):
    page = DocumentPage(
        page_number=1, text=_words(60), blocks=[], ocr_applied=False
    )
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=200)
    assert chunks[0].bbox is None
    assert chunks[0].bbox_source == BBOX_NO_BLOCKS
    assert chunking_summary(chunks)["bbox_sources"] == {BBOX_NO_BLOCKS: 1}


def test_blocks_that_do_not_reconstruct_the_page_abandon_boxes():
    page = DocumentPage(
        page_number=1,
        text="completely unrelated page text",
        blocks=[{"text": "not present in the page", "box": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}}],
    )
    spans, status = page.block_spans()
    assert status == BBOX_SPAN_MISMATCH
    assert spans == []


def test_blocks_without_boxes_produce_no_union(tokenizer):
    page = DocumentPage(
        page_number=1,
        text="One sentence.\nTwo sentence.",
        blocks=[{"text": "One sentence."}, {"text": "Two sentence."}],
    )
    chunks = split_pages([page], tokenizer=tokenizer, chunk_size_tokens=64)
    assert chunks[0].bbox is None
    assert chunks[0].bbox_source == BBOX_NO_INTERSECTION


def test_union_box_of_nothing_is_none():
    assert union_box([], page_number=1) is None


# ===========================================================================
# Degenerate input
# ===========================================================================


def test_no_pages(tokenizer):
    assert split_pages([], tokenizer=tokenizer) == []


@pytest.mark.parametrize("text", ["", "   ", "\n\n\n", "\t"])
def test_blank_pages_are_skipped(tokenizer, text):
    assert split_pages([DocumentPage(1, text)], tokenizer=tokenizer) == []


def test_a_blank_page_between_two_real_ones_does_not_break_indexing(tokenizer):
    pages = [
        DocumentPage(1, _words(20, "a")),
        DocumentPage(2, "   "),
        DocumentPage(3, _words(20, "c")),
    ]
    chunks = split_pages(pages, tokenizer=tokenizer, chunk_size_tokens=64)
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.page_number for chunk in chunks] == [1, 3]


def test_single_enormous_word_still_produces_a_chunk(tokenizer):
    text = "x" * 5000
    chunks = split_pages(
        [DocumentPage(1, text)], tokenizer=tokenizer, chunk_size_tokens=64
    )
    assert len(chunks) == 1
    assert chunks[0].content == text


def test_summary_reports_box_coverage(tokenizer):
    boxed = _boxed_page(["A sentence with words in it."], page_number=1)
    plain = DocumentPage(2, _words(40), blocks=[])
    chunks = split_pages([boxed, plain], tokenizer=tokenizer, chunk_size_tokens=200)
    summary = chunking_summary(chunks)
    assert summary["chunks"] == 2
    assert summary["boxed_chunks"] == 1
    assert summary["bbox_sources"][BBOX_FROM_BLOCKS] == 1
    assert summary["bbox_sources"][BBOX_NO_BLOCKS] == 1