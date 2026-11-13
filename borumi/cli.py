"""Command line: `borumi new`, `borumi render`, `borumi formats`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import FORMATS, ConfigError, load_project
from .media import MediaError, require_ffmpeg

TEMPLATE = """\
# Borumi project file. Lines starting with # are notes and are ignored.
# Put your files in the folders next to this file, then run:
#     borumi render project.yaml

name: My Promo

# Which sizes to make: vertical (Reels/TikTok), square, portrait (4:5), landscape (YouTube)
formats: [vertical, square, landscape]

# Your clips, in order. Videos and photos both work.
clips:
  - file: clips/clip1.mp4
    start: 0          # seconds to skip at the beginning (optional)
    end: 8            # where to stop, in seconds (optional)
    text: "Big Summer Sale"   # headline shown at the top (optional)
  - file: clips/photo.jpg
    duration: 3       # how long to show a photo
  # - file: clips/clip2.mp4

# Cut out pauses where nobody is talking
remove_silence: false

# Your logo in a corner (PNG with transparent background works best)
# logo:
#   file: brand/logo.png
#   position: top-right    # top-left, top-right, bottom-left, bottom-right, center
#   width: 0.15            # 15% of the video width

# Background music, automatically quieter while people talk
# music:
#   file: music/track.mp3
#   volume: 0.25

# Captions: use your own .srt file, or auto: true to write them automatically
# captions:
#   auto: true
#   # file: captions.srt

fade: 0.5          # fade in/out at the start and end, in seconds
output_dir: output
"""


def cmd_new(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    project_file = folder / "project.yaml"
    if project_file.exists():
        print(f"{project_file} already exists, not overwriting it.")
        return 1
    for sub in ("clips", "brand", "music", "output"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    project_file.write_text(TEMPLATE)
    print(f"Created {folder}/ with project.yaml and folders: clips, brand, music, output")
    print(f"Next: put your videos in {folder}/clips, edit {project_file}, then run:")
    print(f"    borumi render {project_file}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from .pipeline import render

    require_ffmpeg()
    project = load_project(args.project)
    if args.output:
        project.output_dir = Path(args.output).resolve()
    outputs = render(project, formats=args.format or None, preview=args.preview)
    print("\nYour videos:")
    for out in outputs:
        print(f"  {out}")
    return 0


def cmd_formats(_: argparse.Namespace) -> int:
    for name, (w, h) in FORMATS.items():
        print(f"  {name:<10} {w}x{h}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="borumi", description="Borumi AI - automatic marketing video editor")
    parser.add_argument("--version", action="version", version=f"borumi {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="create a new project folder with a starter project.yaml")
    p.add_argument("folder", help="folder to create, e.g. summer-promo")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("render", help="make the finished videos from a project.yaml")
    p.add_argument("project", help="path to project.yaml")
    p.add_argument("-f", "--format", action="append", choices=list(FORMATS),
                   help="only make this size (can repeat)")
    p.add_argument("-o", "--output", help="folder to save videos in")
    p.add_argument("--preview", action="store_true",
                   help="fast, lower quality render to check the edit")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("formats", help="list the available video sizes")
    p.set_defaults(func=cmd_formats)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, MediaError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
