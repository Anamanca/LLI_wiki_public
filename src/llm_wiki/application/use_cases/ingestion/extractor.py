"""Video transcript extraction with yt-dlp → faster-whisper fallback.

Optimized flow: single --dump-json call to inspect metadata (availability,
subtitle languages, duration), then at most ONE action call (subtitle download
or audio download).  This cuts API calls from 3-4 to 1-2 per video and
eliminates wasted rate-limit cooldowns.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Where raw transcripts are persisted
TRANSCRIPT_DIR = "/app/data/transcripts"
# Cookie file for YouTube auth (bypasses anti-bot)
COOKIE_DIR = "/app/cookies"
COOKIE_FILE_NAME = "youtube.cookies.txt"

# Skip videos shorter than this (seconds) — no meaningful content to extract
_MIN_VIDEO_DURATION = 300  # 5 minutes


def _ensure_transcript_dir() -> None:
    """Ensure the transcript directory exists (best-effort, called lazily)."""
    with contextlib.suppress(OSError):
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)


@dataclass
class TranscriptSegment:
    """A single subtitle segment with timing."""

    start: float
    end: float
    text: str


@dataclass
class Transcript:
    """Full transcript of a video with metadata."""

    video_id: str
    language: str = "en"
    duration_seconds: float | None = None
    segments: list[TranscriptSegment] = field(default_factory=list)
    raw_text: str = ""


def _find_cookie_file() -> str | None:
    """Return path to a writable copy of youtube.cookies.txt if available.

    yt-dlp writes updated cookies back to the file (token refresh), so
    we copy the read-only mounted file to a temp location.
    """
    cookie_path = os.path.join(COOKIE_DIR, COOKIE_FILE_NAME)
    if not os.path.isfile(cookie_path):
        return None
    mtime = os.path.getmtime(cookie_path)
    if time.time() - mtime > 86400:
        logger.warning(
            "Cookie file %s is stale (%.1fh old)",
            cookie_path,
            (time.time() - mtime) / 3600,
        )
    tmp_path = f"/tmp/{COOKIE_FILE_NAME}"  # nosec B108 — yt-dlp cookie file, no user input
    try:
        import shutil

        shutil.copy2(cookie_path, tmp_path)
        return tmp_path
    except OSError as e:
        logger.warning("Failed to copy cookie file: %s", e)
        return cookie_path


# Regex to parse VTT timestamps: 00:00:01.234 --> 00:00:05.678
_VTT_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)


def _parse_vtt_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


# Regex to strip inline VTT formatting tags: <00:00:01.234><c>text</c>
_VTT_INLINE_TAG_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
_VTT_C_TAG_RE = re.compile(r"</?c>")


def _clean_vtt_text_line(line: str) -> str:
    """Strip inline VTT timestamp markers and <c> tags from a text line."""
    line = _VTT_INLINE_TAG_RE.sub("", line)
    line = _VTT_C_TAG_RE.sub("", line)
    return line.strip()


def parse_vtt(content: str) -> list[TranscriptSegment]:
    """Parse VTT subtitle content into a list of timed segments.

    Handles WebVTT header lines, style blocks, and timestamped cues.
    Strips inline VTT formatting tags (<c>, timestamps) from text lines.
    """
    segments: list[TranscriptSegment] = []
    lines = content.splitlines()
    i = 0
    # Skip WEBVTT header
    if lines and lines[0].strip().startswith("WEBVTT"):
        i = 1
        # Skip optional header metadata until blank line
        while i < len(lines) and lines[i].strip():
            i += 1

    current_text_lines: list[str] = []
    current_start = 0.0
    current_end = 0.0
    in_cue = False

    while i < len(lines):
        line = lines[i].strip()
        # Skip cue identifiers (numeric or style lines)
        if not in_cue and line and not _VTT_TIMESTAMP_RE.match(line):
            # Could be a cue id — skip to next line
            i += 1
            if i < len(lines):
                line = lines[i].strip()

        match = _VTT_TIMESTAMP_RE.match(line)
        if match:
            # Save previous cue
            if in_cue and current_text_lines:
                text = " ".join(current_text_lines).strip()
                text = _clean_vtt_text_line(text)
                if text:
                    segments.append(
                        TranscriptSegment(
                            start=current_start,
                            end=current_end,
                            text=text,
                        )
                    )
            current_start = _parse_vtt_seconds(*match.groups()[:4])
            current_end = _parse_vtt_seconds(*match.groups()[4:])
            current_text_lines = []
            in_cue = True
            i += 1
            continue

        if line == "":
            if in_cue and current_text_lines:
                text = " ".join(current_text_lines).strip()
                text = _clean_vtt_text_line(text)
                if text:
                    segments.append(
                        TranscriptSegment(
                            start=current_start,
                            end=current_end,
                            text=text,
                        )
                    )
            in_cue = False
            current_text_lines = []
            i += 1
            continue

        if in_cue:
            current_text_lines.append(line)
        i += 1

    # Don't miss the final cue
    if in_cue and current_text_lines:
        text = " ".join(current_text_lines).strip()
        text = _clean_vtt_text_line(text)
        if text:
            segments.append(
                TranscriptSegment(
                    start=current_start,
                    end=current_end,
                    text=text,
                )
            )

    return segments


# ---------------------------------------------------------------------------
# yt-dlp helpers — cookies are injected once so ALL calls benefit from auth
# ---------------------------------------------------------------------------


def _build_ytdlp_base_args() -> list[str]:
    """yt-dlp base args with cookies + impersonation (shared by all calls)."""
    base = ["yt-dlp"]

    # TLS fingerprint impersonation — pretend to be Chrome on Android.
    # curl_cffi 0.15.0 is REQUIRED (0.16.0 API is incompatible with yt-dlp 2026.07.04).
    base = base + ["--impersonate", "chrome-131:android-14"]

    cookie_file = _find_cookie_file()
    if cookie_file:
        base = base + ["--cookies", cookie_file]
    return base


async def _run_ytdlp(args: list[str], timeout: float = 300.0) -> str:
    """Run yt-dlp as a subprocess with timeout.

    Returns stdout as string. Raises RuntimeError on failure.
    """
    await _wait_yt_cooldown()

    # NOTE: yt-dlp enables deno by default. Do NOT pass --js-runtimes "deno,node"
    #       (that is treated as one runtime named "deno,node" and silently ignored).
    full_args = _build_ytdlp_base_args() + args

    proc = await asyncio.create_subprocess_exec(
        *full_args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"yt-dlp timed out after {timeout}s") from exc

    if proc.returncode not in (0, None):
        err_text = stderr.decode("utf-8", errors="replace")[:500]
        if "429" in err_text or "Too Many Requests" in err_text:
            _set_yt_cooldown()
        raise RuntimeError(f"yt-dlp exited {proc.returncode}: {err_text}")

    # Successful call — reset adaptive backoff counter
    _clear_yt_cooldown()
    return stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# YouTube rate-limit cooldown — shared across workers via file on volume
# ---------------------------------------------------------------------------
# Uses adaptive exponential backoff: first hit = short cooldown (2 min),
# consecutive hits scale up to 10 min max.  A successful request resets the
# backoff counter.  Random jitter prevents thundering-herd between workers.

_COOLDOWN_FILE = Path(os.environ.get("DATA_DIR", "/app/data")) / "transcripts" / ".yt_cooldown"

# Adaptive backoff: [2, 4, 8, 10, 10, ...] minutes
_BACKOFF_SEQUENCE = (2 * 60, 4 * 60, 8 * 60, 10 * 60)
_COOLDOWN_JITTER_MAX = 120  # seconds (0-2 min, reduced from 5 min — impersonation helps)


def _set_yt_cooldown() -> None:
    """Mark YouTube as rate-limited with adaptive backoff.

    Reads the current backoff level from the cooldown file (if any),
    increments it, and writes the new cooldown.
    Successful requests reset the counter (see _clear_yt_cooldown).
    """
    level = 0
    if _COOLDOWN_FILE.exists():
        try:
            raw = _COOLDOWN_FILE.read_text().strip()
            # format: "<cooldown_until_unix> <backoff_level>"
            parts = raw.split()
            if len(parts) == 2:
                level = min(int(parts[1]) + 1, len(_BACKOFF_SEQUENCE) - 1)
        except (ValueError, OSError):
            pass

    duration = _BACKOFF_SEQUENCE[level]
    cooldown_until = time.time() + duration
    _COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _COOLDOWN_FILE.write_text(f"{int(cooldown_until)} {level}")
    logger.warning(
        "YouTube rate-limited — cooldown until %s (level %d, %ds)",
        datetime.fromtimestamp(cooldown_until), level, duration,
    )


def _clear_yt_cooldown() -> None:
    """Reset cooldown after a successful request (backoff counter → 0)."""
    try:
        _COOLDOWN_FILE.unlink(missing_ok=True)
    except OSError:
        pass


async def _wait_yt_cooldown() -> None:
    """If any worker hit a YouTube rate limit recently, sleep until cooldown expires.

    Adds random jitter after the cooldown to prevent both workers from waking
    up simultaneously and triggering another rate-limit in lockstep.
    """
    try:
        if _COOLDOWN_FILE.exists():
            content = _COOLDOWN_FILE.read_text().strip()
            if content:
                parts = content.split()
                cooldown_until = float(parts[0])
                remaining = cooldown_until - time.time()
                if remaining > 0:
                    jitter = random.randint(0, _COOLDOWN_JITTER_MAX)
                    total_sleep = remaining + jitter
                    logger.warning(
                        "YouTube rate-limit cooldown active — sleeping %ds (+%ds jitter)",
                        int(total_sleep), jitter,
                    )
                    await asyncio.sleep(total_sleep)
    except (ValueError, OSError):
        pass


# ---------------------------------------------------------------------------
# Single-call subtitle extraction (used only when we KNOW subs exist)
# ---------------------------------------------------------------------------


async def _try_extract_subs_to_file(
    video_id: str,
    video_url: str,
    write_auto: bool,
    work_dir: str,
    timeout: float = 300.0,
) -> str | None:
    """Download subtitles to a file (only called after dump-json confirms they exist)."""
    args = [
        "--skip-download",
        "--sub-lang", "en,vi",
        "--sub-format", "vtt",
        f"--write-{'auto-' if write_auto else ''}subs",
        "--convert-subs", "vtt",
        "-o", f"{work_dir}/%(id)s",
        video_url,
    ]
    try:
        await _run_ytdlp(args, timeout=timeout)
        vtt_files = list(Path(work_dir).glob(f"{video_id}*.vtt"))
        if vtt_files:
            return vtt_files[0].read_text(encoding="utf-8", errors="replace")
        return None
    except RuntimeError:
        return None


# ---------------------------------------------------------------------------
# Video accessibility check — lightweight, retries transient errors
# ---------------------------------------------------------------------------


async def check_video_accessible(
    video_id: str, video_url: str, timeout: float = 60.0
) -> str | None:
    """Check if video is accessible (not private/members-only/deleted).

    Returns None if public and accessible.
    Returns reason string ("private", "members_only", "unavailable") if permanently blocked.
    Raises RuntimeError for transient errors (network, rate-limit) — caller should retry.

    Uses yt-dlp --dump-json (lightweight, doesn't download) with retries.
    """
    transient_keywords = [
        "429", "rate", "too many", "timeout", "connection",
        "network", "dns", "resolve", "refused", "reset", "aborted",
    ]

    last_err = ""
    for attempt in range(3):
        args = ["--dump-json", "--skip-download", video_url]
        try:
            output = await _run_ytdlp(args, timeout=timeout)
            json.loads(output)
            return None  # public and accessible
        except RuntimeError as e:
            err = str(e).lower()
            last_err = err

            if "private" in err:
                return "private"
            if "members" in err or "member" in err:
                return "members_only"
            if "unavailable" in err or "removed" in err or "deleted" in err or "not found" in err:
                return "unavailable"

            # Transient error — retry with backoff
            if any(kw in err for kw in transient_keywords):
                logger.warning(
                    "Access check for %s: transient error (attempt %d/3): %.150s",
                    video_id, attempt + 1, err,
                )
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue

            # Unknown error — also retry
            if attempt < 2:
                logger.warning(
                    "Access check for %s: unknown error (attempt %d/3): %.150s",
                    video_id, attempt + 1, err,
                )
                await asyncio.sleep(2 ** attempt)
                continue

    raise RuntimeError(f"check_video_accessible failed after 3 attempts: {last_err[:200]}")


# ---------------------------------------------------------------------------
# Faster-Whisper transcription (Tier 3 — download audio + transcribe locally)
# ---------------------------------------------------------------------------

# Split long audio into 25-min chunks to keep per-chunk memory ~1.5 GB on CPU.
_CHUNK_DURATION = 1500  # 25 minutes
_CHUNK_OVERLAP = 3  # 3-second overlap to prevent boundary word cuts


async def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()[:200]}")
    return float(stdout.decode().strip())


async def _split_audio_chunks(
    audio_path: str,
    tmpdir: str,
    chunk_duration: int,
    overlap: int,
) -> list[tuple[str, float]]:
    """Split audio into overlapping chunks via ffmpeg.

    Returns [(chunk_path, offset_seconds), ...] where offset is the chunk's
    start time in the original audio.
    """
    total = await _get_audio_duration(audio_path)
    chunks: list[tuple[str, float]] = []
    idx = 0
    start = 0.0
    while start < total:
        extract_start = start
        extract_dur = chunk_duration + overlap
        if extract_start + extract_dur > total:
            extract_dur = total - extract_start

        chunk_path = os.path.join(tmpdir, f"chunk_{idx:03d}.mp3")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(extract_start),
            "-i", audio_path,
            "-t", str(extract_dur),
            "-c:a", "libmp3lame",
            "-q:a", "5",
            chunk_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg split chunk {idx} failed: {stderr.decode()[:200]}")

        chunks.append((chunk_path, start))
        start += chunk_duration
        idx += 1

    logger.info(
        "Split audio into %d chunks (total=%.0fs, chunk=%ds, overlap=%ds)",
        len(chunks), total, chunk_duration, overlap,
    )
    return chunks


def _merge_chunked_segments(
    chunk_results: list[tuple[list[TranscriptSegment], float]],
    overlap: float,
) -> list[TranscriptSegment]:
    """Merge segments from multiple chunks, deduplicating overlap zones."""
    merged: list[TranscriptSegment] = []
    prev_max_end = -1.0
    for segments, offset in chunk_results:
        for seg in segments:
            abs_start = seg.start + offset
            abs_end = seg.end + offset
            if abs_end <= prev_max_end + 0.1:
                continue
            if abs_start < prev_max_end:
                abs_start = prev_max_end
            merged.append(
                TranscriptSegment(
                    start=abs_start,
                    end=max(abs_end, abs_start + 0.01),
                    text=seg.text.strip(),
                )
            )
            prev_max_end = max(prev_max_end, abs_end)
    return merged


async def _transcribe_audio_whisper(
    video_id: str,
    video_url: str,
    tmpdir: str,
    device: str = "cpu",
    compute_type: str = "int8",
) -> Transcript:
    """Download audio and transcribe with faster-whisper.

    Long videos (>25 min) are split into overlapping chunks.
    """
    logger.info("Whisper: downloading audio for %s", video_id)
    dl_args = [
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "128K",
        "-o", f"{tmpdir}/%(id)s.%(ext)s",
        video_url,
    ]
    await _run_ytdlp(dl_args, timeout=300.0)

    audio_files = list(Path(tmpdir).glob("*.mp3"))
    if not audio_files:
        audio_files = list(Path(tmpdir).glob("*.m4a"))
    if not audio_files:
        raise RuntimeError("Whisper: no audio file extracted")

    audio_path = str(audio_files[0])
    logger.info("Whisper: transcribing %s (file: %s)", video_id, os.path.basename(audio_path))

    try:
        total_duration = await _get_audio_duration(audio_path)
    except Exception:
        total_duration = 0.0

    need_chunking = total_duration > _CHUNK_DURATION
    if need_chunking:
        logger.info(
            "Whisper: audio %.0fs > %ds — splitting into chunks",
            total_duration, _CHUNK_DURATION,
        )
        chunk_paths = await _split_audio_chunks(audio_path, tmpdir, _CHUNK_DURATION, _CHUNK_OVERLAP)
    else:
        chunk_paths = [(audio_path, 0.0)]

    from faster_whisper import WhisperModel

    def _transcribe_sync():
        model = WhisperModel("small", device=device, compute_type=compute_type)
        locked_language = None
        chunk_results: list[tuple[list[TranscriptSegment], float]] = []
        for i, (chunk_path, offset) in enumerate(chunk_paths):
            seg_gen, info = model.transcribe(chunk_path, vad_filter=True, language=locked_language)
            if locked_language is None:
                locked_language = info.language
            segments = []
            last_log = time.time()
            for seg in seg_gen:
                segments.append(
                    TranscriptSegment(start=seg.start, end=seg.end, text=seg.text.strip())
                )
                now = time.time()
                if now - last_log >= 60:
                    label = f"chunk{i}" if need_chunking else ""
                    logger.info(
                        "Whisper progress %s: %d segments, pos %.0fs / %.0fs",
                        label, len(segments), seg.end, info.duration,
                    )
                    last_log = now
            chunk_results.append((segments, offset))
            logger.info(
                "Whisper chunk %d/%d done: %d segments",
                i + 1, len(chunk_paths), len(segments),
            )
        return chunk_results, locked_language

    chunk_results, language = await asyncio.to_thread(_transcribe_sync)
    language = language or "unknown"

    if need_chunking and len(chunk_results) > 1:
        segments = _merge_chunked_segments(chunk_results, _CHUNK_OVERLAP)
        logger.info(
            "Whisper merged %d chunks → %d segments (before dedup: %d)",
            len(chunk_results), len(segments),
            sum(len(s) for s, _ in chunk_results),
        )
    else:
        segments = chunk_results[0][0] if chunk_results else []

    raw_parts = [s.text for s in segments]
    duration = segments[-1].end if segments else None
    raw_text = " ".join(raw_parts)

    logger.info(
        "Whisper done for %s: %d segments, lang=%s, dur=%.0fs",
        video_id, len(segments), language, duration or 0,
    )
    return Transcript(
        video_id=video_id,
        language=language,
        duration_seconds=duration,
        segments=segments,
        raw_text=raw_text,
    )


# ---------------------------------------------------------------------------
# Main extraction entry point — OPTIMISED
# ---------------------------------------------------------------------------


async def extract_transcript(
    video_url: str,
    video_id: str,
    timeout: float = 300.0,
) -> Transcript:
    """Extract transcript with at most 2 yt-dlp calls (was 3-4).

    1. --dump-json (lightweight) → inspect availability, duration, subtitle langs
    2. Skip if < 5 min, unavailable, or upcoming
    3. Exactly ONE action: subs (auto preferred) → manual subs → whisper audio

    Args:
        video_url: Full YouTube video URL
        video_id: YouTube video ID
        timeout: Max time per yt-dlp invocation (seconds)

    Returns:
        Transcript with segments or empty segments if no captions found.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # ── Step 1: single metadata call ──
        logger.info("Fetching metadata for %s", video_id)
        try:
            meta_json = await _run_ytdlp(
                ["--dump-json", "--skip-download", video_url], timeout=min(timeout, 60.0),
            )
            meta = json.loads(meta_json)
        except RuntimeError as e:
            permanent = _classify_video_error(str(e))
            if permanent:
                raise RuntimeError(f"Video permanently unavailable: {permanent}") from e
            raise  # transient — worker will retry

        # ── Step 2: gate checks ──
        availability = meta.get("availability", "public")
        live_status = meta.get("live_status", "not_live")
        duration = meta.get("duration")  # seconds (int or None)

        if availability != "public":
            raise RuntimeError(f"Video permanently unavailable: {availability}")

        if live_status in ("is_live", "is_upcoming", "upcoming"):
            raise RuntimeError(f"Video scheduled/premiere — retry later")

        if duration is not None and duration < _MIN_VIDEO_DURATION:
            logger.info(
                "Skipping %s: duration %ds < %ds minimum — too short",
                video_id, duration, _MIN_VIDEO_DURATION,
            )
            return Transcript(
                video_id=video_id,
                language="unknown",
                duration_seconds=float(duration),
                segments=[],
                raw_text="",
            )

        # ── Step 3: inspect subtitle availability (NO download yet) ──
        auto_subs = meta.get("automatic_captions", {})
        manual_subs = meta.get("subtitles", {})

        has_auto_en_vi = bool({"en", "vi"} & set(k.lower() for k in auto_subs))
        has_manual_en_vi = bool({"en", "vi"} & set(k.lower() for k in manual_subs))

        vtt_content: str | None = None
        language = "en"

        # ── Step 4: exactly ONE extraction call ──
        if has_auto_en_vi:
            logger.info("auto-subs (en/vi) available for %s — downloading", video_id)
            vtt_content = await _try_extract_subs_to_file(
                video_id, video_url, write_auto=True, work_dir=tmpdir, timeout=timeout,
            )
        elif has_manual_en_vi:
            logger.info("manual subs (en/vi) available for %s — downloading", video_id)
            vtt_content = await _try_extract_subs_to_file(
                video_id, video_url, write_auto=False, work_dir=tmpdir, timeout=timeout,
            )

        # ── Step 5: parse VTT if we have it, else fallback to whisper ──
        segments: list[TranscriptSegment] = []
        raw_text = ""

        if vtt_content:
            segments = parse_vtt(vtt_content)
            raw_text = " ".join(seg.text for seg in segments)

        # If VTT parsed to empty segments (or no VTT at all), fall back to whisper.
        if not segments:
            if vtt_content:
                logger.info(
                    "VTT parsed to 0 segments for %s — falling back to faster-whisper", video_id,
                )
            else:
                logger.info("No subs for %s — falling back to faster-whisper", video_id)
            try:
                whisper_transcript = await _transcribe_audio_whisper(video_id, video_url, tmpdir)
                if whisper_transcript.segments:
                    _save_transcript_json(whisper_transcript)
                    return whisper_transcript
                # whisper produced empty segments — treat as no captions
                logger.warning("Whisper returned empty segments for %s", video_id)
                return Transcript(
                    video_id=video_id, language="unknown",
                    duration_seconds=whisper_transcript.duration_seconds,
                    segments=[], raw_text="",
                )
            except Exception as e:
                permanent = _classify_video_error(str(e))
                if permanent:
                    logger.info("Permanent error for %s: %s", video_id, permanent)
                    raise RuntimeError(f"Video permanently unavailable: {permanent}") from e
                logger.warning(
                    "Whisper failed for %s: %s — propagating to worker for retry", video_id, e,
                )
                raise

        # Detect language from segments
        vi_chars = sum(1 for c in raw_text if ord(c) > 127 and c.isalpha())
        if vi_chars > len(raw_text) * 0.05:
            language = "vi"

        transcript_duration = segments[-1].end if segments else None

        transcript = Transcript(
            video_id=video_id,
            language=language,
            duration_seconds=transcript_duration,
            segments=segments,
            raw_text=raw_text,
        )
        _save_transcript_json(transcript)

        logger.info(
            "Transcript for %s: %d segments, lang=%s, dur=%.0fs",
            video_id, len(segments), language, transcript_duration or 0,
        )
        return transcript


def _save_transcript_json(transcript: Transcript) -> None:
    """Persist transcript to disk (best-effort)."""
    _ensure_transcript_dir()
    transcript_path = os.path.join(TRANSCRIPT_DIR, f"{transcript.video_id}.json")
    try:
        with open(transcript_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "video_id": transcript.video_id,
                    "language": transcript.language,
                    "duration_seconds": transcript.duration_seconds,
                    "segments": [
                        {"start": s.start, "end": s.end, "text": s.text}
                        for s in transcript.segments
                    ],
                    "raw_text": transcript.raw_text,
                },
                f,
                indent=2,
            )
    except OSError as exc:
        logger.warning("Failed to save transcript for %s: %s", transcript.video_id, exc)


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

ExtractError = tuple[str, bool]  # (status, is_permanent)


def _classify_video_error(err: str) -> str | None:
    """Classify a video access error into a permanent status or None (transient)."""
    e = err.lower()

    if "available to this channel's members" in e:
        return "requires_membership"
    if "members-only" in e:
        return "requires_membership"
    if "members" in e and "only content" in e:
        return "requires_membership"

    if any(kw in e for kw in ("private", "unavailable", "removed", "deleted", "not found")):
        return "unavailable"

    if "premieres" in e or "premiere" in e or "upcoming" in e:
        return "scheduled"

    return None


def classify_extract_error(error_msg: str) -> ExtractError:
    """Classify an extraction failure into (status, is_permanent)."""
    permanent = _classify_video_error(error_msg)
    if permanent:
        return (permanent, True)

    err_lower = error_msg.lower()
    if "429" in err_lower or "rate" in err_lower:
        return ("pending", False)

    return ("pending", False)
