"""Chunk a timed transcript into timestamp-aligned windows for map-reduce extraction.

Chunks follow the real subtitle segments (``{start, end, text}``) instead of
arbitrary character cuts, so each extract call is scoped to a contiguous spoken
range with a known ``start_time``/``end_time``. An overlap window is carried
between consecutive chunks so statements that straddle a boundary (or reference
the previous minute) stay in context.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Target window length in seconds — small enough that deepseek-v4-flash keeps
# full attention on every number, large enough to avoid excessive LLM calls.
TARGET_DURATION = 600.0  # 10 minutes
# Overlap carried from the tail of the previous chunk (seconds).
OVERLAP = 45.0
# Below this total duration a single pass is strictly better than chunking.
MIN_CHUNK_DURATION = 480.0
# Hard cap on the number of chunks per video (bounds LLM calls / 1800s deadline).
MAX_CHUNKS = 12


@dataclass
class Chunk:
    """A contiguous transcript window with its raw segment text."""

    start_time: float
    end_time: float
    text: str
    segment_indices: list[int] = field(default_factory=list)


def _valid_segments(segments: list[dict]) -> list[dict]:
    """Keep segments with usable timing/text; drop malformed ones."""
    cleaned: list[dict] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        text = (seg.get("text") or "").strip()
        if start < 0 or end <= start or not text:
            continue
        cleaned.append({"start": start, "end": end, "text": text})
    cleaned.sort(key=lambda s: s["start"])
    return cleaned


def total_duration(segments: list[dict]) -> float:
    """Sum of segment durations (fallback: last end)."""
    cleaned = _valid_segments(segments)
    if not cleaned:
        return 0.0
    return cleaned[-1]["end"] - cleaned[0]["start"]


def chunk_transcript(
    segments: list[dict],
    target_duration: float = TARGET_DURATION,
    overlap: float = OVERLAP,
) -> list[Chunk] | None:
    """Build timestamp-aligned chunks from transcript segments.

    Returns ``None`` when chunking is not beneficial (no usable segments or
    total duration below ``MIN_CHUNK_DURATION``) — callers must fall back to a
    single-pass extraction in that case. Never returns a single "whole text"
    chunk as a fallback; the single-pass path belongs to the caller.
    """
    cleaned = _valid_segments(segments)
    if not cleaned:
        return None
    total = cleaned[-1]["end"] - cleaned[0]["start"]
    if total < MIN_CHUNK_DURATION:
        return None

    chunks: list[Chunk] = []
    current: list[dict] = []
    chunk_start: float | None = None

    for idx, seg in enumerate(cleaned):
        if chunk_start is None:
            chunk_start = seg["start"]
        current.append(seg)
        if seg["end"] - chunk_start >= target_duration:
            # Overlap: carry back the tail of this chunk (up to `overlap` secs).
            tail: list[dict] = []
            for tail_seg in reversed(current):
                if seg["end"] - tail_seg["start"] > overlap:
                    break
                tail.insert(0, tail_seg)
            chunks.append(
                Chunk(
                    start_time=chunk_start,
                    end_time=seg["end"],
                    text=" ".join(s["text"] for s in current),
                    segment_indices=[_segment_pos(cleaned, s) for s in current],
                )
            )
            if len(chunks) >= MAX_CHUNKS:
                # Last chunk absorbs the rest of the video.
                remaining = cleaned[idx + 1 :]
                if remaining:
                    last = chunks[-1]
                    last.end_time = remaining[-1]["end"]
                    last.text = f"{last.text} {' '.join(s['text'] for s in remaining)}"
                    last.segment_indices.extend(_segment_pos(cleaned, s) for s in remaining)
                return chunks
            current = list(tail)
            chunk_start = tail[0]["start"] if tail else seg["end"]

    if current:
        chunks.append(
            Chunk(
                start_time=chunk_start if chunk_start is not None else current[0]["start"],
                end_time=current[-1]["end"],
                text=" ".join(s["text"] for s in current),
                segment_indices=[_segment_pos(cleaned, s) for s in current],
            )
        )
    return chunks or None


def _segment_pos(cleaned: list[dict], seg: dict) -> int:
    """Position of a segment dict within the cleaned list (identity by id())."""
    for i, s in enumerate(cleaned):
        if s is seg or (s.get("start") == seg.get("start") and s.get("text") == seg.get("text")):
            return i
    return -1
