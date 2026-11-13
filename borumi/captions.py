"""Automatic captions using OpenAI's open-source Whisper model (runs on your computer)."""

from __future__ import annotations

from pathlib import Path


def _timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def segments_to_srt(segments: list[dict], max_words: int = 6) -> str:
    """Build an SRT file, splitting long sentences into short punchy lines."""
    entries: list[tuple[float, float, str]] = []
    for seg in segments:
        words = seg.get("words")
        if words:
            chunk: list[dict] = []
            for w in words:
                chunk.append(w)
                if len(chunk) >= max_words or w["word"].rstrip().endswith((".", "?", "!", ",")):
                    entries.append((chunk[0]["start"], chunk[-1]["end"],
                                    "".join(x["word"] for x in chunk).strip()))
                    chunk = []
            if chunk:
                entries.append((chunk[0]["start"], chunk[-1]["end"],
                                "".join(x["word"] for x in chunk).strip()))
        elif seg.get("text", "").strip():
            entries.append((seg["start"], seg["end"], seg["text"].strip()))

    lines = []
    for n, (start, end, text) in enumerate(entries, 1):
        lines.append(f"{n}\n{_timestamp(start)} --> {_timestamp(max(end, start + 0.3))}\n{text}\n")
    return "\n".join(lines)


def transcribe(audio: Path, *, model: str = "base", language: str | None = None) -> str:
    try:
        import whisper  # type: ignore
    except ImportError:
        raise RuntimeError(
            "Automatic captions need Whisper. Install it with:\n"
            "    pip install openai-whisper\n"
            "or write your own captions file and use  captions: {file: my-captions.srt}"
        ) from None
    result = whisper.load_model(model).transcribe(
        str(audio), language=language, word_timestamps=True, fp16=False
    )
    return segments_to_srt(result.get("segments", []))
