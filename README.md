# Borumi AI

Automatic editor for marketing videos. Give it your raw clips and photos, and it makes
finished videos for every platform in one go:

- **Every size at once**: vertical (Reels / TikTok / Shorts), square, portrait 4:5 and
  landscape (YouTube). Clips that don't fit the shape get a blurred background instead
  of black bars.
- **Trims and cuts dead air**: set start/end points per clip, and optionally remove
  pauses where nobody is talking.
- **Headlines** on any clip ("Big Summer Sale — 50% off").
- **Your logo** in any corner.
- **Background music** that gets quieter automatically while people talk.
- **Captions** from your own `.srt` file, or written automatically from the speech.
- **Even sound levels** and smooth fade in/out.

## Install (one time)

1. Install **ffmpeg** (the video engine):
   - Mac: install [Homebrew](https://brew.sh), then `brew install ffmpeg`
   - Windows: `winget install ffmpeg`
2. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/).
3. In this folder, run:

   ```bash
   pip install -e .
   ```

   For automatic captions also run `pip install -e ".[captions]"`.

## Make a video

```bash
borumi new summer-promo
```

This creates a `summer-promo` folder with `clips`, `brand`, `music` and `output`
folders, plus a `project.yaml` file. Put your videos and photos in `clips`, your logo
in `brand` and a song in `music`. Then open `project.yaml` in any text editor:

```yaml
name: Summer Promo
formats: [vertical, square, landscape]

clips:
  - file: clips/intro.mp4
    start: 2            # skip the first 2 seconds
    end: 10             # stop at 10 seconds
    text: "Big Summer Sale — 50% off"
  - file: clips/product.jpg
    duration: 3         # show the photo for 3 seconds
  - file: clips/testimonial.mp4

remove_silence: true    # cut out pauses

logo:
  file: brand/logo.png
  position: top-right

music:
  file: music/track.mp3
  volume: 0.25

captions:
  auto: true            # or: file: my-captions.srt
```

Then make the videos:

```bash
borumi render summer-promo/project.yaml
```

The finished files appear in `summer-promo/output/`, e.g. `summer-promo-vertical.mp4`.

Useful options:

| Command | What it does |
| --- | --- |
| `borumi render project.yaml --preview` | Quick low-quality version to check the edit |
| `borumi render project.yaml -f vertical` | Only make one size |
| `borumi formats` | List the available sizes |

## All settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `name` | `video` | Used for the output file names |
| `formats` | `[vertical]` | `vertical`, `square`, `portrait`, `landscape` |
| `clips` | – | List of videos/photos. Each can have `start`, `end`, `duration`, `text`, `text_seconds`, `mute` |
| `remove_silence` | `false` | Cut out pauses. Tune with `silence: {threshold_db: -35, min_duration: 0.6, padding: 0.15}` |
| `logo` | – | `file`, `position`, `width` (share of video width), `margin`, `opacity` |
| `music` | – | `file`, `volume`, `duck` (lower under speech), `fade_out` |
| `captions` | – | `file` or `auto: true`, plus `model`, `language`, `font`, `size`, `color` |
| `fade` | `0.5` | Fade in/out length in seconds |
| `normalize_audio` | `true` | Make the voice level consistent |
| `quality` | `20` | Lower = better quality and bigger files (18–28 is sensible) |
| `fps` | `30` | Frames per second |
| `output_dir` | `output` | Where finished videos are saved |

Automatic captions also save an `.srt` file next to the videos. If a word is wrong, fix
it in that file and switch to `captions: {file: output/<name>.srt}`.

## For developers

```bash
pip install -e ".[dev]"
pytest
```

The code lives in `borumi/`: `config.py` reads the project file, `pipeline.py` builds one
ffmpeg filter graph per output size, `media.py` wraps ffprobe and silence detection, and
`captions.py` runs Whisper.
