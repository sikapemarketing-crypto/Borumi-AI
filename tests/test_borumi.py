import subprocess
from pathlib import Path

import pytest

from borumi.captions import segments_to_srt
from borumi.cli import main
from borumi.config import ConfigError, parse_project
from borumi.media import probe, speech_segments
from borumi.pipeline import render


def ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    # 6s clip: sound 0-2s, silence 2-4s, sound 4-6s
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi",
       "-i", "sine=f=440:d=6,volume='if(between(t,2,4),0,1)':eval=frame",
       "-c:v", "libx264", "-c:a", "aac", "-shortest", str(d / "talk.mp4"))
    ff("-f", "lavfi", "-i", "smptebars=s=360x640:r=25:d=2", "-c:v", "libx264",
       str(d / "silent clip.mp4"))
    ff("-f", "lavfi", "-i", "color=c=blue:s=400x300", "-frames:v", "1", str(d / "photo.png"))
    ff("-f", "lavfi", "-i", "color=c=white:s=200x80", "-frames:v", "1", str(d / "logo.png"))
    ff("-f", "lavfi", "-i", "sine=f=220:d=3", str(d / "music.mp3"))
    (d / "caps.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHello 100% \"quoted\"\n")
    return d


def test_config_rejects_unknown_setting(tmp_path):
    with pytest.raises(ConfigError, match="Unknown setting"):
        parse_project({"clips": ["x.mp4"], "colour": 1}, tmp_path)


def test_config_reports_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="File not found"):
        parse_project({"clips": ["missing.mp4"]}, tmp_path)


def test_config_rejects_bad_format(media):
    with pytest.raises(ConfigError, match="Unknown format"):
        parse_project({"clips": ["talk.mp4"], "formats": ["imax"]}, media)


def test_speech_segments_finds_the_pause(media):
    segs = speech_segments(media / "talk.mp4", 0, 6, threshold_db=-35, min_duration=0.5,
                           padding=0.1)
    assert len(segs) == 2
    assert segs[0][1] == pytest.approx(2.1, abs=0.15)
    assert segs[1][0] == pytest.approx(3.9, abs=0.15)


def test_srt_splits_long_lines():
    words = [{"word": f" w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(9)]
    srt = segments_to_srt([{"words": words}], max_words=4)
    assert srt.count("-->") == 3
    assert "00:00:00,000 --> 00:00:01,900" in srt


def test_full_render(media, tmp_path):
    project = parse_project({
        "name": "Test Promo!",
        "formats": ["vertical", "landscape"],
        "clips": [
            {"file": "talk.mp4", "text": "50% off: 'today' only"},
            "silent clip.mp4",
            {"file": "photo.png", "duration": 1},
        ],
        "remove_silence": True,
        "logo": "logo.png",
        "music": {"file": "music.mp3", "volume": 0.3},
        "captions": "caps.srt",
        "output_dir": str(tmp_path / "out"),
    }, media)
    outputs = render(project, preview=True, log=lambda _: None)
    assert [o.name for o in outputs] == ["test-promo-vertical.mp4", "test-promo-landscape.mp4"]
    expected = 6 - 1.8 + 2 + 1  # pause shortened by silence removal
    for out, size in zip(outputs, [(1080, 1920), (1920, 1080)]):
        info = probe(out)
        assert (info.width, info.height) == size
        assert info.has_audio
        assert info.duration == pytest.approx(expected, abs=0.3)


def test_cli_new_creates_template(tmp_path, capsys):
    assert main(["new", str(tmp_path / "promo")]) == 0
    assert (tmp_path / "promo" / "project.yaml").exists()
    assert (tmp_path / "promo" / "clips").is_dir()
    assert main(["new", str(tmp_path / "promo")]) == 1  # never overwrites


def test_cli_shows_friendly_error(tmp_path, capsys):
    (tmp_path / "project.yaml").write_text("clips: [nope.mp4]\n")
    assert main(["render", str(tmp_path / "project.yaml")]) == 1
    assert "File not found" in capsys.readouterr().err
