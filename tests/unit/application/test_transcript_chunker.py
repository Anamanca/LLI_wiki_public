"""Unit tests for transcript chunker (P2)."""

from llm_wiki.application.use_cases.ingestion.transcript_chunker import (
    MAX_CHUNKS,
    chunk_transcript,
    total_duration,
)


def _segments(n: int, dur: float = 30.0, gap: float = 0.0) -> list[dict]:
    """n segments of `dur` seconds each with optional gap between them."""
    out = []
    t = 0.0
    for i in range(n):
        out.append({"start": round(t, 2), "end": round(t + dur, 2), "text": f"đoạn {i}"})
        t += dur + gap
    return out


def test_empty_segments_returns_none() -> None:
    assert chunk_transcript([]) is None


def test_malformed_segments_dropped() -> None:
    segs = [
        {"start": "x", "end": 10, "text": "bad"},
        {"start": 0, "end": 30, "text": "ok"},
        {"start": 5, "end": 2, "text": "reverse"},
    ]
    chunks = chunk_transcript(segs, target_duration=600.0)
    # Only the valid 0-30 segment survives -> total < MIN_CHUNK_DURATION -> None
    assert chunks is None


def test_short_transcript_returns_none() -> None:
    # 5 segments x 60s = 300s < 480s
    assert chunk_transcript(_segments(5, dur=60.0), target_duration=600.0) is None


def test_single_chunk_when_under_target() -> None:
    # 10 segments x 60s = 600s >= MIN, under target 600 -> 1 chunk (tail)
    chunks = chunk_transcript(_segments(10, dur=60.0), target_duration=600.0)
    assert chunks is not None
    assert len(chunks) == 1
    assert chunks[0].start_time == 0.0
    assert chunks[0].end_time == 600.0


def test_multiple_chunks_with_overlap() -> None:
    # 40 segments x 30s = 1200s, target 600s -> 3 chunks, overlap 45s
    chunks = chunk_transcript(_segments(40, dur=30.0), target_duration=600.0)
    assert chunks is not None
    assert len(chunks) == 3
    # chunk 1 covers [0, 600]; chunk 2 starts at 600-30=570 (last seg of chunk 1)
    assert chunks[1].start_time == 570.0
    assert chunks[1].end_time == 1170.0
    assert chunks[2].start_time == 1140.0
    assert chunks[2].end_time == 1200.0
    # overlap text present in both
    assert chunks[1].segment_indices[0] == chunks[0].segment_indices[-1] == 19


def test_max_chunks_cap() -> None:
    # 500 segments x 30s = 15000s -> would be 25 chunks without cap
    segs = _segments(500, dur=30.0)
    chunks = chunk_transcript(segs, target_duration=600.0)
    assert chunks is not None
    assert len(chunks) <= MAX_CHUNKS


def test_total_duration() -> None:
    assert total_duration([]) == 0.0
    assert total_duration(_segments(3, dur=10.0)) == 30.0
