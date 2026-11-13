"""Small helpers around ffprobe / ffmpeg."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(RuntimeError):
    """Raised when ffmpeg/ffprobe fails or a file can't be read."""


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    if missing:
        raise MediaError(
            f"{' and '.join(missing)} not found. Install ffmpeg first "
            "(Mac: 'brew install ffmpeg', Windows: 'winget install ffmpeg')."
        )


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    width: int
    height: int


def probe(path: Path) -> MediaInfo:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise MediaError(f"Could not read {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"{path} has no picture in it")
    try:
        duration = float(data.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        duration = 0.0
    return MediaInfo(
        duration=duration,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
    )


_SILENCE_START = re.compile(r"silence_start: (-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end: (-?[\d.]+)")


def speech_segments(
    path: Path, start: float, end: float, *, threshold_db: float, min_duration: float,
    padding: float,
) -> list[tuple[float, float]]:
    """Return the parts of [start, end] that are NOT silent, relative to `start`."""
    length = end - start
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
         "-i", str(path), "-vn", "-af",
         f"silencedetect=noise={threshold_db}dB:d={min_duration}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise MediaError(f"Silence detection failed for {path}: {result.stderr[-500:]}")

    silences: list[tuple[float, float]] = []
    current: float | None = None
    for line in result.stderr.splitlines():
        if (m := _SILENCE_START.search(line)):
            current = max(0.0, float(m.group(1)))
        elif (m := _SILENCE_END.search(line)) and current is not None:
            silences.append((current, min(length, float(m.group(1)))))
            current = None
    if current is not None:
        silences.append((current, length))

    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in silences:
        # Shrink each silence by `padding` so speech isn't clipped.
        cut_from, cut_to = s_start + padding, s_end - padding
        if cut_to - cut_from <= 0:
            continue
        if cut_from > cursor:
            keep.append((cursor, cut_from))
        cursor = max(cursor, cut_to)
    if cursor < length:
        keep.append((cursor, length))
    # Drop slivers that would just produce a flash of picture.
    keep = [(a, b) for a, b in keep if b - a >= 0.1]
    return keep or [(0.0, length)]


def run_ffmpeg(args: list[str], *, label: str, cwd: Path | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise MediaError(f"ffmpeg failed while {label}:\n{result.stderr.strip()[-2000:]}")


def find_font(name: str) -> str | None:
    """Return a font file path for `name` (bold if available), or None."""
    if not shutil.which("fc-match"):
        return None
    result = subprocess.run(["fc-match", "-f", "%{file}", f"{name}:bold"],
                            capture_output=True, text=True)
    path = result.stdout.strip()
    return path if result.returncode == 0 and path and Path(path).exists() else None
