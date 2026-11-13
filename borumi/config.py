"""Loading and validating a Borumi project file (YAML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

FORMATS: dict[str, tuple[int, int]] = {
    "vertical": (1080, 1920),   # Reels, TikTok, Shorts, Stories
    "square": (1080, 1080),     # Instagram / Facebook feed
    "portrait": (1080, 1350),   # Instagram 4:5 feed
    "landscape": (1920, 1080),  # YouTube, website
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}


class ConfigError(ValueError):
    """Raised when a project file is invalid."""


@dataclass
class Clip:
    file: Path
    start: float | None = None
    end: float | None = None
    duration: float | None = None  # required for images
    text: str | None = None
    text_seconds: float = 3.0
    mute: bool = False

    @property
    def is_image(self) -> bool:
        return self.file.suffix.lower() in IMAGE_EXTS


@dataclass
class Logo:
    file: Path
    position: str = "top-right"
    width: float = 0.15   # fraction of video width
    margin: int = 40      # pixels
    opacity: float = 0.9


@dataclass
class Music:
    file: Path
    volume: float = 0.25
    duck: bool = True     # lower the music while people are speaking
    fade_out: float = 2.0


@dataclass
class Captions:
    file: Path | None = None   # an existing .srt file
    auto: bool = False         # transcribe speech automatically (needs openai-whisper)
    model: str = "base"
    language: str | None = None
    font: str = "Inter"
    size: int = 18             # relative to a 288-line canvas, scales with video size
    color: str = "FFFFFF"      # RRGGBB
    outline_color: str = "000000"


@dataclass
class Silence:
    threshold_db: float = -35.0
    min_duration: float = 0.6  # gaps shorter than this are kept
    padding: float = 0.15      # seconds of breathing room kept around speech


@dataclass
class Project:
    name: str
    clips: list[Clip]
    formats: list[str] = field(default_factory=lambda: ["vertical"])
    output_dir: Path = Path("output")
    fps: int = 30
    fade: float = 0.5
    normalize_audio: bool = True
    remove_silence: bool = False
    silence: Silence = field(default_factory=Silence)
    logo: Logo | None = None
    music: Music | None = None
    captions: Captions | None = None
    quality: int = 20  # x264 CRF: lower = better quality, bigger file
    base_dir: Path = Path(".")


def _num(value, key: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{key}' must be a number, got {value!r}") from None
    if minimum is not None and number < minimum:
        raise ConfigError(f"'{key}' must be at least {minimum}, got {number}")
    return number


def _check_keys(data: dict, allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in {where}: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def _path(base: Path, value, key: str, *, must_exist: bool = True) -> Path:
    if not value:
        raise ConfigError(f"'{key}' needs a file path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    if must_exist and not path.exists():
        raise ConfigError(f"File not found for '{key}': {path}")
    return path


def _clip(base: Path, data, index: int) -> Clip:
    where = f"clips[{index}]"
    if isinstance(data, str):
        data = {"file": data}
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a file path or a mapping")
    _check_keys(data, {"file", "start", "end", "duration", "text", "text_seconds", "mute"}, where)
    clip = Clip(file=_path(base, data.get("file"), f"{where}.file"))
    for key in ("start", "end", "duration"):
        if data.get(key) is not None:
            setattr(clip, key, _num(data[key], f"{where}.{key}", minimum=0))
    clip.text = data.get("text")
    clip.text_seconds = _num(data.get("text_seconds", 3.0), f"{where}.text_seconds", minimum=0)
    clip.mute = bool(data.get("mute", False))
    if clip.is_image and clip.duration is None:
        clip.duration = 3.0
    if clip.start is not None and clip.end is not None and clip.end <= clip.start:
        raise ConfigError(f"{where}: 'end' must be after 'start'")
    return clip


def load_project(path: str | Path) -> Project:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Project file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError("The project file must contain settings like 'name:' and 'clips:'")
    return parse_project(data, base_dir=path.resolve().parent)


def parse_project(data: dict, base_dir: Path) -> Project:
    _check_keys(
        data,
        {"name", "clips", "formats", "output_dir", "fps", "fade", "normalize_audio",
         "remove_silence", "silence", "logo", "music", "captions", "quality"},
        "project",
    )
    clips_data = data.get("clips")
    if not clips_data or not isinstance(clips_data, list):
        raise ConfigError("'clips' must list at least one video or image file")

    formats = data.get("formats", ["vertical"])
    if isinstance(formats, str):
        formats = [formats]
    for fmt in formats:
        if fmt not in FORMATS:
            raise ConfigError(f"Unknown format '{fmt}'. Choose from: {', '.join(FORMATS)}")

    project = Project(
        name=str(data.get("name") or "video"),
        clips=[_clip(base_dir, c, i) for i, c in enumerate(clips_data)],
        formats=list(dict.fromkeys(formats)),
        output_dir=_path(base_dir, data.get("output_dir", "output"), "output_dir", must_exist=False),
        fps=int(_num(data.get("fps", 30), "fps", minimum=1)),
        fade=_num(data.get("fade", 0.5), "fade", minimum=0),
        normalize_audio=bool(data.get("normalize_audio", True)),
        remove_silence=bool(data.get("remove_silence", False)),
        quality=int(_num(data.get("quality", 20), "quality", minimum=0)),
        base_dir=base_dir,
    )

    if (s := data.get("silence")) is not None:
        _check_keys(s, {"threshold_db", "min_duration", "padding"}, "silence")
        project.silence = Silence(
            threshold_db=_num(s.get("threshold_db", -35), "silence.threshold_db"),
            min_duration=_num(s.get("min_duration", 0.6), "silence.min_duration", minimum=0.1),
            padding=_num(s.get("padding", 0.15), "silence.padding", minimum=0),
        )

    if (lg := data.get("logo")) is not None:
        if isinstance(lg, str):
            lg = {"file": lg}
        _check_keys(lg, {"file", "position", "width", "margin", "opacity"}, "logo")
        position = lg.get("position", "top-right")
        if position not in POSITIONS:
            raise ConfigError(f"logo.position must be one of: {', '.join(sorted(POSITIONS))}")
        project.logo = Logo(
            file=_path(base_dir, lg.get("file"), "logo.file"),
            position=position,
            width=_num(lg.get("width", 0.15), "logo.width", minimum=0.01),
            margin=int(_num(lg.get("margin", 40), "logo.margin", minimum=0)),
            opacity=min(1.0, _num(lg.get("opacity", 0.9), "logo.opacity", minimum=0)),
        )

    if (m := data.get("music")) is not None:
        if isinstance(m, str):
            m = {"file": m}
        _check_keys(m, {"file", "volume", "duck", "fade_out"}, "music")
        project.music = Music(
            file=_path(base_dir, m.get("file"), "music.file"),
            volume=_num(m.get("volume", 0.25), "music.volume", minimum=0),
            duck=bool(m.get("duck", True)),
            fade_out=_num(m.get("fade_out", 2.0), "music.fade_out", minimum=0),
        )

    if (c := data.get("captions")) is not None:
        if isinstance(c, str):
            c = {"file": c}
        _check_keys(c, {"file", "auto", "model", "language", "font", "size", "color",
                        "outline_color"}, "captions")
        caps = Captions(
            auto=bool(c.get("auto", False)),
            model=str(c.get("model", "base")),
            language=c.get("language"),
            font=str(c.get("font", "Inter")),
            size=int(_num(c.get("size", 18), "captions.size", minimum=1)),
            color=str(c.get("color", "FFFFFF")).lstrip("#"),
            outline_color=str(c.get("outline_color", "000000")).lstrip("#"),
        )
        if c.get("file"):
            caps.file = _path(base_dir, c["file"], "captions.file")
        if not caps.file and not caps.auto:
            raise ConfigError("captions needs either 'file: my.srt' or 'auto: true'")
        project.captions = caps

    return project
