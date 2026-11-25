"""Independently decode every stream, measure loudness and inspect loop endpoints."""

import argparse
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import numpy as np
from PIL import Image
from render_video import BRAND, ROOT, CASE, FPS, MODES, closing_frame, filename, read
from update_video_picture import audio_hash
from master_audio import decode, HZ, master, separated


def small_gray_frame(path, t):
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{t:.4f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=640:360:flags=lanczos,format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
    return np.frombuffer(subprocess.check_output(cmd), dtype=np.uint8).reshape(
        (360, 640)
    )


def shot_cuts(case, mode, frames, script):
    count = len(script["shots"])
    if mode == "tv":
        return [round(i * (frames - 36) / count) / FPS for i in range(count + 1)]
    timeline = read(case / "audio" / mode / "timeline.json")
    return [round(s["start_ms"] * FPS / 1000) / FPS for s in timeline["shots"]] + [
        closing_frame(timeline, script) / FPS
    ]


def measure_motion(case, mode, record):
    """Estimate camera scale on decoded central image detail, outside subtitles."""
    p = case / "videos" / record["file"]
    script = read(case / "script.json")
    cuts = shot_cuts(case, mode, record["frames"], script)
    shifts = {}
    for i, shot in enumerate(script["shots"]):
        if i == 0:
            continue
        a, b = cuts[i : i + 2]
        first = Image.fromarray(small_gray_frame(p, a + 0.65))
        last = small_gray_frame(p, b - 0.15).astype("float32")

        def residual(z):
            dx = 320 * (1 - 1 / z)
            dy = 180 * (1 - 1 / z)
            pred = np.asarray(
                first.transform(
                    (640, 360),
                    Image.Transform.EXTENT,
                    (dx, dy, 640 - dx, 360 - dy),
                    Image.Resampling.BICUBIC,
                ),
                dtype="float32",
            )
            return np.mean(np.abs(last[30:245, 40:600] - pred[30:245, 40:600]))

        best = min(np.arange(1, 1.044, 0.001), key=residual)
        paper = i == 1 or len(shot["image_ids"]) == 2
        valid = best < 1.002 if paper else 1.031 < best < 1.043
        if not valid:
            raise ValueError(f"{mode} 第 {i + 1} 镜实测缩放异常 {best:.3f}")
        shifts[shot["id"]] = round(float(best), 3)
    return shifts


def stem_levels(raw_voice, voice, bed):
    """Measure RMS over the same raw-voice 10 ms windows for both stems."""
    block = HZ // 100
    length = min(len(raw_voice), len(voice), len(bed)) // block
    def power(samples):
        return np.mean(samples[:length * block].reshape(length, block, -1) ** 2, axis=(1, 2))
    def db(power):
        if not len(power):
            raise ValueError("测量段为空")
        return round(float(10 * np.log10(max(1e-20, power.mean()))), 2)
    source = power(raw_voice)
    active = source > 10 ** (-42 / 10)
    first = np.flatnonzero(active)
    if not first.size or first[0] < 10:
        raise ValueError("没有可独立测量的无声片头")
    intro = min(65, first[0])
    music = power(bed)
    speech = db(power(voice)[active])
    backing = db(music[active])
    return dict(voice_active_dbfs=speech, bed_active_dbfs=backing,
                bed_minus_voice_db=round(backing - speech, 2),
                intro_bed_dbfs=db(music[:intro]), intro_ms=int(intro * 10),
                pause_bed_dbfs=db(music[source < 1e-8]))


def check_levels(levels):
    if not -16 <= levels["bed_minus_voice_db"] <= -12 or levels["intro_bed_dbfs"] < -26:
        raise ValueError(f"配乐未达可听范围 {levels}")


def logo_contrast(image, logo, x, y, width, light):
    """Decoded luminance on known vector strokes versus adjacent empty interiors."""
    with Image.open(logo) as src:
        mask = src.getchannel("A")
        mask = np.asarray(mask.crop(mask.getbbox()).resize((width, width), Image.Resampling.LANCZOS))
    with Image.open(image) as src:
        pixels = np.asarray(src.convert("L").crop((x, y, x + width, y + width)))
    contrast = float(pixels[mask > 180].mean() - pixels[mask < 30].mean())
    if (contrast if light else -contrast) < 20:
        raise ValueError(f"{image.name} 品牌图标缺失或对比度不足 {contrast:.1f}")
    return round(contrast, 1)


def verify(case, mode, checks, brand_path=BRAND):
    manifest = read(case / "case-manifest.json")
    script = read(case / "script.json")
    out = case / "videos"
    path = out / filename(manifest, mode)
    streams = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    video, audio = streams["streams"]
    data = dict(
        file=path.name,
        bytes=path.stat().st_size,
        duration_s=float(streams["format"]["duration"]),
        width=video["width"],
        height=video["height"],
        fps=video["avg_frame_rate"],
        codec=video["codec_name"],
        pixel_format=video["pix_fmt"],
        frames=int(video["nb_frames"]),
        audio_codec=audio["codec_name"],
        audio_sample_rate=int(audio["sample_rate"]),
        audio_stream_hash=audio_hash(path).decode().strip(),
    )
    if (
        data["width"],
        data["height"],
        data["fps"],
        data["codec"],
        data["pixel_format"],
    ) != (3840, 2160, "30/1", "h264", "yuv420p"):
        raise ValueError(str(data))
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-f",
        "null",
        "-",
    ]
    decoded = subprocess.run(cmd, check=True, capture_output=True)
    if decoded.stderr:
        raise ValueError("解码器报错")
    data["full_decode_errors"] = 0
    log = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vn",
            "-af",
            "ebur128=peak=true:framelog=verbose",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stderr.decode()
    summary = log.rsplit("Summary:", 1)[1]
    data["integrated_lufs"] = float(re.search(r"I:\s+([-\d.]+) LUFS", summary)[1])
    data["true_peak_dbtp"] = float(re.search(r"Peak:\s+([-\d.]+) dBFS", summary)[1])
    tv = mode == "tv"
    target, peak = (-18, -2) if tv else (-16, -1.8)
    if abs(data["integrated_lufs"] - target) > (1 if tv else .1) or data["true_peak_dbtp"] > peak:
        raise ValueError(f"{mode} 母带超限 {data}")
    if not tv:
        with TemporaryDirectory(dir=checks) as tmp:
            work = Path(tmp)
            duration = data["frames"] / 30
            voice, bed = separated(case, mode, duration, work)
            raw = decode(case / "audio" / mode / "voice.wav", 1)
            levels = stem_levels(raw, voice, bed)
            check_levels(levels)
            if audio_hash(master(case, mode, duration, work)) != audio_hash(path):
                raise ValueError("实际封装音轨与受测分轨重建的母带不同")
            levels["verified_muxed_aac"] = True
            data["premaster_stems"] = levels
    shots = script["shots"]
    cuts = shot_cuts(case, mode, data["frames"], script)
    legacy = manifest["name"] == "庭院自然居"
    aliases = ("plan", "living", "diptych", "hearth", "shelves",
               "bedroom", "timber", "textiles", "wardrobe")
    legacy_tv_times = (10, 17, 24, 31, 38, 45, 52, 59, 67)
    samples = {"first_frame": 0}
    paper_samples = set()
    if not tv:
        timeline = read(case / "audio" / mode / "timeline.json")
        samples["brand_closing"] = cuts[-1] + 0.05
    for idx, shot in enumerate(shots[1:], 1):
        name = aliases[idx - 1] if legacy else shot["id"]
        if idx == 1 or len(shot["image_ids"]) == 2:
            paper_samples.add(name)
        start, end = cuts[idx:idx + 2]
        if tv:
            sec = legacy_tv_times[idx - 1] if legacy else (start + end) / 2
        else:
            cues = [
                c
                for c in timeline["subtitles"]
                if c["shot"] == shot["id"] and start + 0.5 < (c["start_ms"] + c["end_ms"]) / 2000 < end
            ]
            if not cues:
                raise ValueError(f"{shot['id']} 缺少可抽查的画面内字幕")
            cue_index = 1 if legacy and idx == 9 and len(cues) > 1 else len(cues) // 2
            cue = cues[cue_index]
            sec = (cue["start_ms"] + cue["end_ms"]) / 2000
        samples[name] = sec
    samples["last_frame"] = (data["frames"] - 1) / 30
    folder = checks / mode
    folder.mkdir(parents=True, exist_ok=True)
    for name, sec in samples.items():
        dest = folder / (name + ".png")
        fresh = folder / (name + ".new.png")
        fresh.unlink(missing_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                f"{max(0, sec - (0.25 / 30 if name == 'last_frame' else 0)):.8f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-threads",
                "1",
                str(fresh),
            ],
            check=True,
        )
        fresh.replace(dest)
    brand = read(brand_path)
    from render_video import corner, names
    contrasts = {}
    for name in samples:
        paper = name in paper_samples
        logo = brand["logos"]["dark" if paper else "light"]["png"]
        if name in ("first_frame", "last_frame", "brand_closing"):
            geometry = (164, 1480, 64) if name == "first_frame" or tv else (1862, 858, 116)
        else:
            layer = corner(*names(brand), logo, paper)
            geometry = (3680 - layer.width + 72, 1944, 44)
        contrasts[name] = logo_contrast(folder / (name + ".png"), ROOT / logo, *geometry, not paper)
    data["logo_luma_contrast"] = contrasts
    data["frame_samples_s"] = samples
    plan_name = "plan" if legacy else shots[1]["id"]
    data["plan_caption_sample"] = plan_name if not tv else None
    data["plan_steady_s"] = round(cuts[2] - cuts[1] - 14 / 30, 3)
    if data["plan_steady_s"] < 6:
        raise ValueError("成片彩平稳镜不足六秒")
    if not tv:
        with Image.open(folder / (plan_name + ".png")) as still:
            pixels = np.asarray(still.convert("L").crop((2400, 1400, 3840, 1840)))
        ys, xs = np.where(pixels < 120)
        if not len(xs):
            raise ValueError("彩平右栏缺少同期字幕")
        box = [int(xs.min()) + 2400, int(ys.min()) + 1400, int(xs.max()) + 2401, int(ys.max()) + 1401]
        if not (2460 <= box[0] <= 2466 and box[2] <= 3680 and box[3] < 1840):
            raise ValueError(f"彩平右栏字幕超出安全区 {box}")
        data["decoded_plan_caption_ink_box"] = box
    if tv:
        diff = np.abs(
            np.asarray(Image.open(folder / "first_frame.png")).astype("int16")
            - np.asarray(Image.open(folder / "last_frame.png")).astype("int16")
        )
        data["loop_rgb"] = {
            "max_abs": int(diff.max()),
            "mean_abs": float(diff.mean()),
            "changed_pixels": int(np.any(diff, axis=2).sum()),
        }
        # Restrict the comparison to the MP4 presentation length (AAC can pad its final packet).
        samples_pcm = decode(path, 2)[: round(data["duration_s"] * HZ)]
        edge_step = np.abs(samples_pcm[0] - samples_pcm[-1])
        data["loop_audio_join_step_dbfs"] = round(
            float(20 * np.log10(max(edge_step.max(), 1e-10))), 2
        )
        if diff.max():
            raise ValueError(str(data["loop_rgb"]))
    data["measured_scale_early_to_late"] = measure_motion(case, mode, data)
    return data


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=CASE)
    p.add_argument("--brand", type=Path, default=BRAND)
    a = p.parse_args()
    checks = a.case / "video-checks"
    checks.mkdir(exist_ok=True)
    manifest = read(a.case / "case-manifest.json")
    modes = [m for m in MODES if (a.case / "videos" / filename(manifest, m)).is_file()]
    if not modes:
        p.error("案例目录中没有可核验的成片")
    records = {m: verify(a.case, m, checks, a.brand) for m in modes}
    (a.case / "videos/video-qa-report.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False)
    )
