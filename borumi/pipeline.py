"""Turns a Project into finished videos with a single ffmpeg pass per format."""

from __future__ import annotations

import re
import shutil
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import FORMATS, Clip, Project
from .media import find_font, probe, run_ffmpeg, speech_segments

Log = Callable[[str], None]

AUDIO_FMT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


@dataclass
class PlannedClip:
    clip: Clip
    start: float              # where to start reading the source file
    length: float             # how much of the source file to read
    has_audio: bool
    segments: list[tuple[float, float]] | None  # parts to keep (relative), None = all

    @property
    def duration(self) -> float:
        if self.segments is None:
            return self.length
        return sum(b - a for a, b in self.segments)


def plan_clips(project: Project, log: Log = print) -> list[PlannedClip]:
    planned = []
    for clip in project.clips:
        if clip.is_image:
            planned.append(PlannedClip(clip, 0.0, clip.duration or 3.0, False, None))
            continue
        info = probe(clip.file)
        start = clip.start or 0.0
        end = clip.end if clip.end is not None else info.duration
        if clip.duration is not None:
            end = min(end, start + clip.duration)
        end = min(end, info.duration) if info.duration else end
        if end - start <= 0.05:
            raise ValueError(f"{clip.file.name}: nothing left after trimming (start={start}, end={end})")
        has_audio = info.has_audio and not clip.mute
        segments = None
        if project.remove_silence and has_audio:
            s = project.silence
            segments = speech_segments(clip.file, start, end, threshold_db=s.threshold_db,
                                       min_duration=s.min_duration, padding=s.padding)
            if len(segments) == 1 and segments[0] == (0.0, end - start):
                segments = None
            else:
                removed = (end - start) - sum(b - a for a, b in segments)
                log(f"  {clip.file.name}: removed {removed:.1f}s of silence")
        planned.append(PlannedClip(clip, start, end - start, has_audio, segments))
    return planned


def _between(segments: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in segments)


def _ass_colour(rrggbb: str) -> str:
    rrggbb = (rrggbb + "000000")[:6]
    return f"&H00{rrggbb[4:6]}{rrggbb[2:4]}{rrggbb[0:2]}".upper()


class GraphBuilder:
    """Collects ffmpeg inputs and filter_complex lines."""

    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.count = 0
        self.lines: list[str] = []

    def add_input(self, *args: str) -> int:
        self.inputs.extend(args)
        self.count += 1
        return self.count - 1

    def add(self, line: str) -> None:
        self.lines.append(line)


def _clip_audio(g: GraphBuilder, i: int, pc: PlannedClip, video_idx: int) -> None:
    if pc.has_audio:
        src = f"[{video_idx}:a]"
    else:
        idx = g.add_input("-f", "lavfi", "-t", f"{pc.length:.3f}",
                          "-i", "anullsrc=r=48000:cl=stereo")
        src = f"[{idx}:a]"
    chain = [AUDIO_FMT]
    if pc.segments:
        chain += [f"aselect='{_between(pc.segments)}'", "asetpts=N/SR/TB"]
    chain += ["apad", f"atrim=duration={pc.duration:.3f}"]
    g.add(f"{src}{','.join(chain)}[a{i}]")


def _clip_input(g: GraphBuilder, pc: PlannedClip, fps: int) -> int:
    if pc.clip.is_image:
        return g.add_input("-loop", "1", "-framerate", str(fps), "-t", f"{pc.length:.3f}",
                           "-i", str(pc.clip.file.resolve()))
    return g.add_input("-ss", f"{pc.start:.3f}", "-t", f"{pc.length:.3f}",
                       "-i", str(pc.clip.file.resolve()))


def build_voice_graph(planned: list[PlannedClip], project: Project) -> GraphBuilder:
    """Audio-only graph of the edited timeline (used for automatic captions)."""
    g = GraphBuilder()
    for i, pc in enumerate(planned):
        idx = _clip_input(g, pc, project.fps)
        _clip_audio(g, i, pc, idx)
    joined = "".join(f"[a{i}]" for i in range(len(planned)))
    g.add(f"{joined}concat=n={len(planned)}:v=0:a=1[aout]")
    return g


def build_graph(
    planned: list[PlannedClip], project: Project, fmt: str, workdir: Path,
    captions_name: str | None, font_name: str | None,
) -> GraphBuilder:
    W, H = FORMATS[fmt]
    fps = project.fps
    g = GraphBuilder()
    total = sum(pc.duration for pc in planned)
    short_side = min(W, H)

    for i, pc in enumerate(planned):
        idx = _clip_input(g, pc, fps)
        v = [f"fps={fps}"]
        if pc.segments:
            v += [f"select='{_between(pc.segments)}'", f"setpts=N/{fps}/TB"]
        v += [f"trim=duration={pc.duration:.3f}", "setsar=1", f"split=2[bg{i}][fg{i}]"]
        g.add(f"[{idx}:v]{','.join(v)}")
        # Blurred, zoomed copy of the clip fills any empty space (no black bars).
        g.add(f"[bg{i}]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
              f"crop={W // 8}:{H // 8},boxblur=10:2,scale={W}:{H},eq=brightness=-0.06[bgb{i}]")
        g.add(f"[fg{i}]scale={W}:{H}:force_original_aspect_ratio=decrease:"
              f"force_divisible_by=2[fgs{i}]")
        post = f"[bgb{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p"
        if pc.clip.text and font_name:
            size = int(short_side * 0.075)
            # Keep the headline clear of corner logos, especially on wide videos.
            text_width = min(W * 0.8, short_side * 1.1)
            chars = max(10, int(text_width / (size * 0.55)))
            text_file = workdir / f"text{i}.txt"
            text_file.write_text("\n".join(textwrap.wrap(pc.clip.text, chars)))
            # expansion=none: show the text literally ("50% off" must not be parsed).
            post += (f",drawtext=fontfile={font_name}:textfile={text_file.name}:expansion=none:"
                     f"fontsize={size}:fontcolor=white:line_spacing={size // 4}:"
                     f"box=1:boxcolor=black@0.45:boxborderw={size // 2}:"
                     f"x=(w-text_w)/2:y=h*0.14:enable='lt(t\\,{pc.clip.text_seconds:.2f})'")
        g.add(f"{post}[v{i}]")
        _clip_audio(g, i, pc, idx)

    joined = "".join(f"[v{i}][a{i}]" for i in range(len(planned)))
    g.add(f"{joined}concat=n={len(planned)}:v=1:a=1[vcat][acat]")

    # ---- picture finishing ----
    vf = []
    fade = min(project.fade, total / 4)
    if fade > 0:
        vf += [f"fade=t=in:st=0:d={fade:.2f}",
               f"fade=t=out:st={total - fade:.3f}:d={fade:.2f}"]
    if captions_name and project.captions:
        c = project.captions
        size = max(1, round(c.size * short_side / H))
        margin = 60 if H > W else 18
        style = (f"FontName={c.font},FontSize={size},Bold=1,"
                 f"PrimaryColour={_ass_colour(c.color)},"
                 f"OutlineColour={_ass_colour(c.outline_color)},"
                 f"BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV={margin}")
        vf.append(f"subtitles={captions_name}:original_size={W}x{H}:force_style='{style}'")
    g.add(f"[vcat]{','.join(vf) or 'null'}[vfin]")

    if project.logo:
        lg = project.logo
        idx = g.add_input("-i", str(lg.file.resolve()))
        logo_w = max(2, int(W * lg.width) // 2 * 2)
        m = lg.margin
        x, y = {
            "top-left": (f"{m}", f"{m}"),
            "top-right": (f"W-w-{m}", f"{m}"),
            "bottom-left": (f"{m}", f"H-h-{m}"),
            "bottom-right": (f"W-w-{m}", f"H-h-{m}"),
            "center": ("(W-w)/2", "(H-h)/2"),
        }[lg.position]
        g.add(f"[{idx}:v]scale={logo_w}:-2,format=rgba,"
              f"colorchannelmixer=aa={lg.opacity:.2f}[logo]")
        g.add(f"[vfin][logo]overlay={x}:{y}:format=auto,format=yuv420p[vout]")
    else:
        g.add("[vfin]null[vout]")

    # ---- sound finishing ----
    voice = "[acat]"
    if project.normalize_audio:
        g.add(f"[acat]loudnorm=I=-16:TP=-1.5:LRA=11,{AUDIO_FMT}[voice]")
        voice = "[voice]"
    if project.music:
        mu = project.music
        idx = g.add_input("-stream_loop", "-1", "-i", str(mu.file.resolve()))
        fo = min(mu.fade_out, total / 2)
        g.add(f"[{idx}:a]{AUDIO_FMT},volume={mu.volume:.3f},atrim=duration={total:.3f},"
              f"afade=t=in:d=0.5,afade=t=out:st={total - fo:.3f}:d={max(fo, 0.01):.2f}[music]")
        if mu.duck:
            g.add(f"{voice}asplit=2[vmix][vkey]")
            g.add("[music][vkey]sidechaincompress=threshold=0.03:ratio=10:"
                  "attack=15:release=400[ducked]")
            g.add("[vmix][ducked]amix=inputs=2:duration=first:normalize=0[amixed]")
        else:
            g.add(f"{voice}[music]amix=inputs=2:duration=first:normalize=0[amixed]")
        voice = "[amixed]"
    afade = min(project.fade, total / 4)
    if afade > 0:
        g.add(f"{voice}afade=t=in:d={afade:.2f},"
              f"afade=t=out:st={total - afade:.3f}:d={afade:.2f}[aout]")
    else:
        g.add(f"{voice}anull[aout]")
    return g


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower() or "video"


def render(
    project: Project, *, formats: list[str] | None = None, preview: bool = False,
    log: Log = print,
) -> list[Path]:
    formats = formats or project.formats
    for fmt in formats:
        if fmt not in FORMATS:
            raise ValueError(f"Unknown format '{fmt}'. Choose from: {', '.join(FORMATS)}")

    log(f"Planning {len(project.clips)} clip(s)...")
    planned = plan_clips(project, log)
    total = sum(pc.duration for pc in planned)
    log(f"Final length: {total:.1f}s")

    project.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    with tempfile.TemporaryDirectory(prefix="borumi-") as tmp:
        workdir = Path(tmp)

        # Files referenced inside filters are copied here under plain names so
        # that spaces, colons and quotes in the user's paths can't break ffmpeg.
        font_name = None
        if any(pc.clip.text for pc in planned):
            font = find_font(project.captions.font if project.captions else "Inter") \
                or find_font("sans")
            if font:
                font_name = "font" + Path(font).suffix
                shutil.copy(font, workdir / font_name)
            else:
                log("  Warning: no font found, on-screen text will be skipped")

        captions_name = None
        if project.captions:
            captions_name = "captions.srt"
            if project.captions.file:
                shutil.copy(project.captions.file, workdir / captions_name)
            else:
                from .captions import transcribe
                log("Transcribing speech for captions (this can take a while)...")
                voice = workdir / "voice.wav"
                vg = build_voice_graph(planned, project)
                run_ffmpeg([*vg.inputs, "-filter_complex", ";".join(vg.lines),
                            "-map", "[aout]", "-ac", "1", "-ar", "16000", str(voice)],
                           label="preparing audio for captions", cwd=workdir)
                srt = transcribe(voice, model=project.captions.model,
                                 language=project.captions.language)
                (workdir / captions_name).write_text(srt)
                saved = project.output_dir / f"{_slug(project.name)}.srt"
                saved.write_text(srt)
                log(f"  Captions saved to {saved} (edit it and use 'captions: file:' to fix words)")

        for fmt in formats:
            out = (project.output_dir / f"{_slug(project.name)}-{fmt}.mp4").resolve()
            log(f"Rendering {fmt} {FORMATS[fmt][0]}x{FORMATS[fmt][1]} -> {out.name}")
            g = build_graph(planned, project, fmt, workdir, captions_name, font_name)
            encode = (["-preset", "ultrafast", "-crf", "30"] if preview
                      else ["-preset", "medium", "-crf", str(project.quality)])
            run_ffmpeg([*g.inputs, "-filter_complex", ";\n".join(g.lines),
                        "-map", "[vout]", "-map", "[aout]",
                        "-c:v", "libx264", *encode, "-pix_fmt", "yuv420p",
                        "-r", str(project.fps),
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        "-t", f"{total:.3f}", str(out)],
                       label=f"rendering {fmt}", cwd=workdir)
            outputs.append(out)
    log("Done!")
    return outputs
