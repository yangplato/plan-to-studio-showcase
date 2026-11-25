"""Compose UHD editorial stills, time caption layers, dissolve and mux at 30 fps."""

import argparse
from functools import lru_cache
from pathlib import Path
import math
import re
import numpy as np
import os
import subprocess
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from build_brochure import mount_plan
from master_audio import master
from synthesize_voice import BRAND, CASE, ROOT, copy_text, read

SIZE = (3840, 2160)
FPS = 30
PAPER = "#f3eee4"
INK = "#302a22"
PLAN_CAPTION_X = 2463
PLAN_CAPTION_Y = 1560
PLAN_CAPTION_WIDTH = 1180
MODES = {"edge": "讲解版-微软配音", "hailuo": "讲解版-海螺配音", "tv": "展厅电视氛围版"}


@lru_cache(None)
def font(size, sans=False):
    key = "SHOWCASE_SANS" if sans else "SHOWCASE_SERIF"
    names = [
        os.getenv(key),
        "STHeiti Light.ttc" if sans else "Songti.ttc",
    ]
    for name in filter(None, names):
        try:
            return ImageFont.truetype(
                name, size, index={"Songti.ttc": 3, "STHeiti Light.ttc": 1}.get(name, 0)
            )
        except OSError:
            continue
    raise RuntimeError("请用 SHOWCASE_SERIF 与 SHOWCASE_SANS 指定中文字体文件")


def label(im, xy, value, size, color=INK, sans=False, anchor="lm"):
    face = font(size, sans)
    value = value.replace(chr(0x33A1), "m²")
    ImageDraw.Draw(im).text(xy, value, font=face, fill=color, anchor=anchor)


def icon(im, brand, x, y, width, variant="light"):
    with Image.open(ROOT / brand["logos"][variant]["png"]) as mark:
        mark = mark.crop(mark.getchannel("A").getbbox()).resize(
            (width, width), Image.Resampling.LANCZOS
        )
        im.paste(mark, (x, y), mark)


def names(brand):
    cn, _, en = brand["name"].partition(" · ")
    return cn, en


@lru_cache(12)
def corner(cn, en, logo, paper):
    """Straight alpha artwork. Keep the background visible through the lettering."""
    color = "#2B2721" if paper else "#FAF6EF"
    en = " ".join(en)
    cn = cn.removesuffix("事务所")
    english = ImageFont.truetype("HelveticaNeue.ttc", 22)
    width = math.ceil(font(34).getlength(cn) + english.getlength(en)) + 84
    layer = Image.new("RGBA", (width + 72, 130))
    draw = ImageDraw.Draw(layer)
    draw.text((width + 36, 65), en, font=english, fill=color, anchor="rm")
    draw.text((width + 16 - english.getlength(en), 65), cn, font=font(34), fill=color, anchor="rm")
    with Image.open(ROOT / logo) as src:
        mask = src.getchannel("A")
        mask = mask.crop(mask.getbbox()).resize((44, 44), Image.Resampling.LANCZOS)
        layer.paste(color, (36, 43, 80, 87), mask)
    mask = layer.getchannel("A")
    layer.putalpha(mask.point(lambda a: round(a * .86)))
    if paper:
        return layer
    shadow = Image.new("RGBA", layer.size)
    shadow_mask = mask.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(3.5))
    shadow.putalpha(shadow_mask.point(lambda a: round(a * 0.68)))
    shadow.alpha_composite(layer)
    return shadow


def closing_frame(timeline, script):
    parts = script["shots"][-1]["narration"][-1]["subtitles"]
    cues = timeline["subtitles"][-len(parts):]
    if [p["text"] for p in parts] != [c["text"] for c in cues]:
        raise ValueError("片尾台词与字幕不一致")
    return math.floor(cues[0]["start_ms"] * FPS / 1000)


def area(im, xy, number, size, color):
    face = ImageFont.truetype("Baskerville.ttc", size)
    ImageDraw.Draw(im).text(xy, f"{number:g} m²", font=face, fill=color, anchor="lm")


@lru_cache(3)
def shade(kind):
    y, x = np.ogrid[:2160, :3840]
    if kind == "cover":
        a = np.interp(x / 3840, [0, 0.28, 0.54, 1], [0, 1, 2, 2])
        alpha = np.where(
            a <= 1,
            0.51 + 0.13 * np.cos(np.pi * a),
            0.19 + 0.19 * np.cos(np.pi * (a - 1)),
        )
    elif kind == "bottom":
        alpha = 0.2 * (1 - np.cos(np.pi * np.clip((y - 1560) / 600, 0, 1)))
    else:
        r = np.sqrt(((x - 260) / 1200) ** 2 + ((y - 2020) / 440) ** 2)
        alpha = 0.27 * (1 + np.cos(np.pi * np.minimum(r, 1)))
    return Image.fromarray(np.broadcast_to(np.uint8(alpha * 255), SIZE[::-1]).copy())


def board(case, manifest, script, brand, i, narrated=True):
    shot = script["shots"][i]
    page = next(p for p in script["pages"] if p["id"] == shot["page_id"])
    assets = {a["id"]: case / a["output"] for a in manifest["assets"] if a["adopted"]}
    photos = [assets[k] for k in shot["image_ids"]]

    def fitted(path, size):
        with Image.open(path) as src:
            return ImageOps.fit(
                src.convert("RGB"), size, method=Image.Resampling.LANCZOS
            )

    if i == 1:
        im = Image.new("RGB", SIZE, PAPER)
        with Image.open(photos[0]) as src:
            src.thumbnail((1950, 1950), Image.Resampling.LANCZOS)
            plan = mount_plan(src, PAPER)
            im.paste(plan, ((2320 - plan.width) // 2, (2160 - plan.height) // 2))
        ImageDraw.Draw(im).line((2290, 540, 2290, 1560), fill="#b3a48e", width=2)
        label(im, (2460, 780), "平面", 116)
        area(im, (2460, 1030), manifest["area_m2"], 180, INK)
        label(im, (2460, 1300), manifest["layout"], 64)
    elif len(photos) == 2:
        im = Image.new("RGB", SIZE, PAPER)
        if narrated:
            label(im, (164, 145), page["title"].replace("，", "　"), 76)
        for x, path in zip((168, 1944), photos):
            im.paste(fitted(path, (1728, 1440)), (x, 280))
    else:
        im = fitted(photos[0], SIZE)
        if i == 0 and manifest.get("mirror_cover", script["shots"][0]["image_ids"][0] == "cover-material"):
            im = ImageOps.mirror(im)
        if i == 0:
            im.paste("black", (0, 0, *SIZE), shade("cover"))
            label(im, (164, 535), names(brand)[1] + " · STUDIO SHOWCASE", 42, "#F3EDE0", True)
            label(im, (164, 820), manifest["name"], 154, "#fff7e9")
            area(im, (164, 1055), manifest["area_m2"], 116, "#ede1cc")
            icon(im, brand, 164, 1480, 64)
            label(im, (250, 1512), brand["name"], 42, "#fff7e9")
    return im


def push(im, t):
    zoom = 1 + 0.019 * (1 - math.cos(math.pi * t))
    dx = im.width * (1 - 1 / zoom) / 2
    dy = im.height * (1 - 1 / zoom) / 2
    return im.transform(
        SIZE,
        Image.Transform.EXTENT,
        (dx, dy, im.width - dx, im.height - dy),
        Image.Resampling.BICUBIC,
    )


@lru_cache(8)
def lettering(text, paper):
    face = font(72, True)
    width = math.ceil(face.getlength(text)) + 96
    layer = Image.new("RGBA", (width, 176))
    ink = ImageDraw.Draw(layer)
    ink.text(
        (48, 88), text, font=face, anchor="lm", fill="#2B2721" if paper else "#FAF6EF"
    )
    if paper:
        return layer
    shadow = Image.new("RGBA", layer.size)
    for radius, opacity in ((16, 0.7), (2, 0.9)):
        blurred = layer.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
        cast = Image.new("RGBA", layer.size)
        cast.putalpha(blurred.point(lambda a: round(a * opacity)))
        shadow.alpha_composite(cast, (0, 2))
    shadow.alpha_composite(layer)
    return shadow


def plan_caption_lines(text):
    width = font(72, True).getlength
    if width(text) <= PLAN_CAPTION_WIDTH:
        return [text]
    punctuation = "，。；：、！？,.!?;:"
    splits = [
        (i, [text[:i].rstrip(), text[i:].lstrip()])
        for i in range(1, len(text))
        if text[i] not in punctuation
    ]
    fits = [(i, lines) for i, lines in splits if all(width(line) <= PLAN_CAPTION_WIDTH for line in lines)]
    if not fits:
        raise ValueError("彩平字幕超过两行安全宽度")
    return min(
        fits,
        key=lambda split: (
            text[split[0] - 1] not in punctuation
            and not any(char.isspace() for char in text[split[0] - 1:split[0] + 1]),
            abs(width(split[1][0]) - width(split[1][1])),
        ),
    )[1]


def subtitle(im, text, i, paper=False):
    clean = re.sub(r"[，。、；：,.;:…！!]+$", "", text.strip())
    if not clean:
        return
    if i == 1:
        layers = [lettering(line, True) for line in plan_caption_lines(clean)]
        y = PLAN_CAPTION_Y - (layers[0].height + 92 * (len(layers) - 1)) // 2
        for layer in layers:
            left, _, right, bottom = layer.getchannel("A").getbbox()
            x = PLAN_CAPTION_X - left
            if not (2460 <= x + left < x + right <= 3680 and y + bottom < 1840):
                raise ValueError("彩平字幕超出右栏安全区")
            im.paste(layer, (x, y), layer)
            y += 92
        return
    layer = lettering(clean, paper)
    x = (3840 - layer.width) // 2
    if x + layer.width - 48 > 3800:
        raise ValueError(f"字幕超宽 {text}")
    im.paste(layer, (x, 1872), layer)


def filename(manifest, mode):
    return f"{manifest['name']}-{manifest['area_m2']:g} m²-{MODES[mode]}-4K.mp4"


def run(*args):
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *map(str, args),
        ],
        check=True,
    )


def render(case, mode, brand_path=BRAND, original=None, output=None):
    case = case.resolve()
    manifest, script, brand = (
        read(case / "case-manifest.json"),
        read(case / "script.json"),
        read(brand_path),
    )
    out = case / "videos"
    out.mkdir(exist_ok=True)
    dest = output or out / filename(manifest, mode)
    narr = mode != "tv"
    timeline = read(case / "audio" / mode / "timeline.json") if narr else None
    shots = script["shots"]
    total = math.ceil(timeline["duration_ms"] * FPS / 1000) if narr else 72 * FPS
    starts = (
        [round(s["start_ms"] * FPS / 1000) for s in timeline["shots"]]
        if narr
        else [round(i * (total - 36) / len(shots)) for i in range(len(shots))]
    )
    if starts[2] - starts[1] - 14 < 6 * FPS:
        raise ValueError("彩平稳镜不足六秒")
    # Each entry represents a range of identical frames. Frame cuts stay on the CFR grid.
    endcard = closing_frame(timeline, script) if narr else total - 36
    cuts = starts + [endcard, total]
    changes = set(cuts)
    for start in cuts[1:-1]:
        changes.update(range(start, start + 15))
    cues = {}
    if narr:
        for c in timeline["subtitles"]:
            a, b = (math.ceil(c[k] * FPS / 1000) for k in ("start_ms", "end_ms"))
            cues.update(dict.fromkeys(range(a, b), c["text"]))
            changes.update((a, b))
    else:
        for start in starts:
            changes.update(range(start + 15, start + 24))
            changes.update(range(start + 111, start + 121))
    moving = {i for i in range(2, len(shots)) if len(shots[i]["image_ids"]) == 1}
    for i in moving:
        changes.update(range(cuts[i], cuts[i + 1]))
    ordered = sorted(x for x in changes if 0 <= x <= total)
    with TemporaryDirectory(dir=out) as tmp:
        work = Path(tmp)

        @lru_cache(3)
        def base(i):
            if i < len(shots):
                return board(case, manifest, script, brand, i, narrated=narr)
            if not narr:
                return base(0)
            im = Image.new("RGB", SIZE, brand["colors"]["primary"])
            cn, en = names(brand)
            icon(im, brand, 1862, 858, 116)
            label(im, (1920, 1046), cn, 56, "#F3EDE0", anchor="mm")
            label(im, (1920, 1120), " ".join(en), 26, "#F3EDE0", True, anchor="mm")
            ImageDraw.Draw(im).rectangle((1872, 1180, 1967, 1181), fill=brand["colors"]["accent"])
            label(im, (1920, 1250), brand["closing"].rstrip("。"), 36, "#DED3BE", anchor="mm")
            return im

        def frame(i, elapsed):
            if i in moving:
                return push(
                    base(i), min(1, elapsed / max(1, cuts[i + 1] - cuts[i] - 1))
                )
            return base(i).copy()

        catalog = ["ffconcat version 1.0"]
        for j, (a, b) in enumerate(zip(ordered, ordered[1:])):
            i = sum(x <= a for x in cuts[:-1]) - 1
            elapsed = a - cuts[i]
            im = frame(i, elapsed)
            if i and elapsed < 14 and not (narr and i == len(shots)):
                fraction = (elapsed + 1) / 14
                im = Image.blend(
                    frame(i - 1, cuts[i] - cuts[i - 1] - 1),
                    im,
                    fraction * fraction * (3 - 2 * fraction),
                )
            paper = i < len(shots) and (i == 1 or len(shots[i]["image_ids"]) == 2)
            if narr and i < len(shots):
                if not paper:
                    im.paste("black", (0, 0, *SIZE), shade("bottom"))
                if a in cues:
                    subtitle(im, cues[a], i, paper)
            elif not narr and i < len(shots) and 15 <= elapsed < 120:
                overlay = im.copy()
                if not paper:
                    overlay.paste("black", (0, 0, *SIZE), shade("chapter"))
                label(
                    overlay,
                    (164, 1850),
                    f"{i + 1:02}",
                    38,
                    INK if paper else "#E8DCC6",
                    True,
                )
                ImageDraw.Draw(overlay).rectangle(
                    (164, 1900, 227, 1901), fill="#B08D57"
                )
                label(
                    overlay,
                    (164, 1970),
                    shots[i]["ambient_label"].replace("，", "　"),
                    62,
                    INK if paper else "#F3EDE0",
                )
                im = Image.blend(
                    im, overlay, min(1, (elapsed - 14) / 9, (120 - elapsed) / 9)
                )
            if 0 < i < len(shots):
                logo = brand["logos"]["dark" if paper else "light"]["png"]
                layer = corner(*names(brand), logo, paper)
                im.paste(layer, (3680 - layer.width + 36, 1966 - 65), layer)
            file = f"v{j:04}.jpg"
            im.save(work / file, quality=94, subsampling=1)
            catalog.extend(
                [f"file {file}", "option framerate 30", f"duration {(b - a) / FPS:.9f}"]
            )
        catalog.extend([f"file {file}", "option framerate 30"])
        (work / "sequence").write_text("\n".join(catalog))
        pic = work / "picture.mp4"
        args = [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            work / "sequence",
            "-vf",
            "scale=in_range=full:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709,format=yuv420p,setparams=range=limited,setsar=1,fps=30",
            "-frames:v",
            total,
            "-color_range",
            "tv",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
        ]
        # Extra final IDR exactly repeats the first coded picture on the loop.
        if not narr:
            args[args.index("-frames:v") + 1] = total - 1
        try:
            run(
                *args,
                "-c:v",
                "h264_videotoolbox",
                "-b:v",
                "28M",
                "-maxrate",
                "36M",
                "-bf",
                "0",
                "-tag:v",
                "avc1",
                pic,
            )
        except subprocess.CalledProcessError:
            run(
                *args,
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "faster",
                "-bf",
                "0",
                "-tag:v",
                "avc1",
                pic,
            )
        if not narr:
            first = work / "first.mp4"
            run("-i", pic, "-frames:v", "1", "-c", "copy", first)
            (work / "loop").write_text(
                f"file picture.mp4\nduration {(total - 1) / FPS:.9f}\nfile first.mp4\n"
            )
            run(
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                work / "loop",
                "-c",
                "copy",
                "-video_track_timescale",
                "15360",
                work / "loop.mp4",
            )
            pic = work / "loop.mp4"
        sound = original or master(case, mode, total / FPS, work)
        run(
            "-i",
            pic,
            "-i",
            sound,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            "comment=Wood, Stone, Home | Music credits: ../../../THIRD_PARTY_NOTICES.md",
            work / "final.mp4",
        )
        (work / "final.mp4").replace(dest)
    copy_text(script, out)
    return dest


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=CASE)
    p.add_argument("--brand", type=Path, default=BRAND)
    p.add_argument("--mode", choices=MODES, required=True)
    a = p.parse_args()
    print(render(a.case, a.mode, a.brand).name)
