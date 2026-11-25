"""Rebuild the video stream against its original timeline and copy the audio packets."""

import argparse
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from render_video import BRAND, CASE, MODES, filename, read, render


def audio_hash(path):
    return subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "hash",
            "-",
        ]
    )


def update(case, mode, brand):
    original = case / "videos" / filename(read(case / "case-manifest.json"), mode)
    with TemporaryDirectory(dir=original.parent) as tmp:
        new = Path(tmp) / original.name
        render(case, mode, brand, original, new)
        if audio_hash(original) != audio_hash(new):
            raise ValueError("音频流校验失败")

        def streams(path):
            return subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type,duration,nb_frames",
                    "-of",
                    "csv",
                    str(path),
                ]
            )

        if streams(new) != streams(original):
            raise ValueError("成片时长或帧数改变，请核对原时间轴")
        new.replace(original)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=CASE)
    p.add_argument("--brand", type=Path, default=BRAND)
    p.add_argument("--mode", choices=MODES, required=True)
    args = p.parse_args()
    update(args.case, args.mode, args.brand)
