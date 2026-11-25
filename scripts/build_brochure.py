"""同源排版，导出独立 HTML、Chrome PDF、原生 PowerPoint 和逐页预览。"""

import argparse
import base64
import hashlib
from contextlib import contextmanager
from html import escape
from io import BytesIO
import json
import os
import re
from functools import lru_cache
from xml.etree import ElementTree as ET
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageFont, ImageOps, ImageColor, ImageEnhance
import numpy as np
from scipy.ndimage import binary_propagation
from playwright.sync_api import sync_playwright
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Emu, Pt
from jsonschema import validate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "templates/brochure"))
from layouts import THEMES, compose

FONTS = {
    "serif": ('Baskerville,"Songti SC",serif', "Songti SC", "Baskerville"),
    "sans": (
        '"Helvetica Neue","PingFang SC",sans-serif',
        "PingFang SC",
        "Helvetica Neue",
    ),
    "mono": ('"SF Mono",Menlo,monospace', "PingFang SC", "Menlo"),
}
CSS = """
@page { size: 1920px 1080px; margin: 0; }
* { box-sizing:border-box; }
body { margin:0; font-family:Baskerville,"Songti SC",serif; line-height:1.8; }
.slide { position:relative;width:1920px;height:1080px;overflow:hidden;break-after:page; }
.slide:last-child {break-after:auto}
.slide>* {position:absolute;margin:0;padding:0;font-weight:400}
.text {line-break:strict;word-break:normal;text-autospace:normal}
sup {font-size:.55em;vertical-align:baseline;position:relative;top:-.48em;line-height:0}
@media print { * {print-color-adjust:exact;-webkit-print-color-adjust:exact} }
"""


@lru_cache(64)
def metric_font(face, cjk, weight, size):
    if cjk:
        name = "Songti.ttc" if face == "serif" else "PingFang.ttc"
        index = ({300: 3, 600: 1}.get(weight, 6) if face == "serif"
                 else {300: 15, 500: 7, 600: 11}.get(weight, 3))
    else:
        name = {"serif": "Baskerville.ttc", "sans": "HelveticaNeue.ttc", "mono": "Menlo.ttc"}[face]
        index = {300: 7, 500: 10, 600: 1}.get(weight, 0) if face == "sans" else int(weight >= 600)
    try:
        return ImageFont.truetype(name, size, index=index)
    except OSError:
        assets = Path("/System/Library/AssetsV2")
        path = next(assets.glob("com_apple_MobileAsset_Font*/*/AssetData/" + name), None)
        if path is None:
            raise RuntimeError(f"安装排版字体 {name} 后重试")
        return ImageFont.truetype(str(path), size, index=index)


def optical_Shift(text, face, size, weight):
    """Signed horizontal compensation and ink edges relative to the baseline."""
    glyph = text[0]
    font = metric_font(face, ord(glyph) > 0x2fff, weight, round(size * 4))
    left, top, _, _ = font.getbbox(glyph, anchor="ls")
    mask = font.getmask(glyph).getbbox()
    if not mask:
        return 0, 0, 0
    return -(left + mask[0]) / 4, (top + mask[1]) / 4, (top + mask[3]) / 4


def first_line_ink(browser, node):
    """Rasterize one line in the export browser to include CJK fallback and hinting."""
    family = FONTS[node["face"]][0]
    browser.evaluate("""([family,n])=>{
      const e=document.createElement("div");e.id="optical-probe";
      Object.assign(e.style,{position:"fixed",left:"0",top:"0",zIndex:"2147483647",
        margin:"0",padding:"0",background:"white",color:"black",overflow:"hidden",
        fontFamily:family,fontSize:n.size+"px",fontWeight:n.weight,lineHeight:String(n.leading),
        letterSpacing:n.spacing+"em",width:n.box[2]+"px",height:Math.ceil(n.size*n.leading)+"px",
        textAlign:n.align,lineBreak:"strict",textAutospace:"none"});
      e.textContent=n.value;document.body.append(e);
    }""", [family, node])
    probe = browser.locator("#optical-probe")
    try:
        pixels = np.asarray(Image.open(BytesIO(probe.screenshot())).convert("L"))
        rows = np.flatnonzero((pixels < 128).any(axis=1))
        if not rows.size:
            raise ValueError("无法测量首行墨迹")
        return int(rows[0]), int(rows[-1]) + 1
    finally:
        probe.evaluate("e=>e.remove()")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def mount_plan(image, mat):
    """Tint paper only where neutral pixels connect to the outside border."""
    pixels = np.array(image.convert("RGB"))
    # A scan can carry a thin grey rail beyond a full white gutter.
    # Only clear strips separated from the architecture by a full white line.
    for view in (
        pixels,
        pixels[:, ::-1],
        pixels.transpose(1, 0, 2),
        pixels.transpose(1, 0, 2)[:, ::-1],
    ):
        margin = max(1, view.shape[1] // 100)
        white = np.flatnonzero(view[:, :margin].min(axis=(0, 2)) >= 250)
        if white.size:
            view[:, : white[-1] + 1] = 255
    low, high = pixels.min(axis=2), pixels.max(axis=2)
    paper = (low >= 205) & (high - low <= 12)
    seeds = np.zeros_like(paper)
    seeds[[0, -1], :] = paper[[0, -1], :]
    seeds[:, [0, -1]] = paper[:, [0, -1]]
    outside = binary_propagation(seeds, mask=paper)
    # Feather the tint through the pale cast shadow, preserving its density.
    alpha = np.clip((low.astype(np.float32) - 205) / 40, 0, 1) * outside
    if mat is None:
        out = Image.fromarray(pixels).convert("RGBA")
        out.putalpha(Image.fromarray(np.uint8(255 * (1 - alpha))))
        return out
    tint = np.array(ImageColor.getrgb(mat)) - 255
    return Image.fromarray(np.uint8(np.clip(pixels + alpha[..., None] * tint, 0, 255)))


def sample_crop(source, focus, fraction):
    cx, cy = focus
    with Image.open(source) as im:
        height = min(round(im.height * fraction), im.width * 600 // 288)
        width = round(height * 288 / 600)
        x = max(0, min(im.width - width, round(im.width * cx - width / 2)))
        y = max(0, min(im.height - height, round(im.height * cy - height / 2)))
        if width < 288 or height < 600:
            raise ValueError("材质取样像素不足")
        return im.crop((x, y, x + width, y + height)).convert("RGB")


def photo_image(n, sources):
    w, h = n["box"][2:]
    size = (w * 2, h * 2)
    if n["sample"] is not None:
        return sample_crop(sources[n["id"]], n["focus"], n["sample"])
    with Image.open(sources[n["id"]]) as original:
        im = original.convert("RGB")
    if not n["mat"]:
        return ImageOps.fit(
            im, size, method=Image.Resampling.LANCZOS, centering=n["focus"]
        )
    im.thumbnail(size, Image.Resampling.LANCZOS)
    if n["mat"] in ("transparent", "clear"):
        im = mount_plan(
            ImageEnhance.Color(im).enhance(0.92 if n["mat"] == "transparent" else 1),
            None,
        )
        mount = Image.new("RGBA", size)
    else:
        im = mount_plan(im, n["mat"])
        mount = Image.new("RGB", size, n["mat"])
    mount.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    return mount


@lru_cache(4)
def overlay(name, w, h):
    y, x = np.ogrid[:h, :w]
    rng = np.random.default_rng(105)
    r = ((x / w - 0.44) / 0.63) ** 2 + ((y / h - 0.45) / 0.75) ** 2
    if name == "paper":
        im = Image.new("RGBA", (w, h), "#655237")
        a = rng.uniform(0, 0.045, (h, w))
    elif name == "spot":
        im = Image.new("RGBA", (w, h), "#FFE0AF")
        a = 0.11 * np.exp(-3 * r)
    else:
        im = Image.new("RGBA", (w, h), "#13100D")
        if name == "bottom":
            a = 0.31 * (1 - np.cos(np.pi * y / (h - 1)))
        elif name == "hearth-top":
            a = 0.82 * np.exp(-((x / w - 0.90) / 0.26) ** 2 - ((y / h - 0.10) / 0.29) ** 2)
        else:
            stops = [0, 0, 0.36, 0.72, 0.87] if name == "night-cover" else [0, 0, 0.18, 0.48, 0.8]
            lower = np.interp(y / h, [0, 0.55, 0.7, 0.84, 1], stops)
            a = 1 - (1 - 0.4 * np.clip(r - 0.32, 0, 1)) * (1 - lower)
        a = np.clip(a + rng.uniform(-0.002, 0.002, (h, w)), 0, 1)
    im.putalpha(Image.fromarray(np.uint8(a * 255)))
    return im


def rgb(value):
    return RGBColor.from_string(value.lstrip("#"))


def vector_logo(slide, path, coords, view):
    """Convert the brand's straight-line SVG strokes to one editable freeform."""
    root = ET.parse(path).getroot()
    group = root.find("{*}g")
    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", group.find("{*}path").attrib["d"])
    pen = None
    points = []
    x = y = 0
    start = (0, 0)
    j = 0
    left, top, w, h = coords
    scale = w / view[2]
    left -= round(view[0] * scale)
    top -= round(view[1] * scale)
    while j < len(tokens):
        if tokens[j].isalpha():
            cmd = tokens[j]
            j += 1
        kind = cmd.upper()
        if kind == "Z":
            pen.add_line_segments(points, close=True)
            points = []
            x, y = start
            continue
        count = {"M": 2, "H": 1, "V": 1, "L": 2}[kind]
        args = list(map(float, tokens[j : j + count]))
        j += count
        rel = cmd.islower()
        if kind != "V":
            x = args[0] + (x if rel else 0)
        if kind != "H":
            y = args[-1] + (y if rel else 0)
        if kind == "M":
            if pen is None:
                pen = slide.shapes.build_freeform(x, y, scale=scale)
            else:
                if points:
                    pen.add_line_segments(points, close=False)
                pen.move_to(x, y)
            start = (x, y)
            points = []
            cmd = "l" if rel else "L"
        else:
            points.append((x, y))
    if points:
        pen.add_line_segments(points, close=False)
    shape = pen.convert_to_shape(left, top)
    shape.fill.background()
    shape.line.color.rgb = rgb(group.attrib["stroke"])
    shape.line.width = Emu(round(float(group.attrib["stroke-width"]) * scale))
    shape.name = "brand-logo"


def render_slide(prs, page, paper, nodes, sources, media, browser):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(paper)
    parts = [f'<section class="slide" id="{page["id"]}" style="background:{paper}">']
    for n in nodes:
        x, y, w, h = n["box"]
        anchor_x = x
        shift = 0
        if n["kind"] == "text":
            ink_x, _, _ = optical_Shift(n["value"], n["face"], n["size"], n["weight"])
            if n["opt_x"] and n["align"] in ("left", "justify"):
                shift = ink_x
                x += shift
            if n["ink_top"] or n["ink_bottom"]:
                top, bottom = first_line_ink(browser, n)
                y -= top if n["ink_top"] else bottom
        coords = [Emu(round(v * 6350)) for v in (x, y, w, h)]
        pos = f"left:{x}px;top:{y}px;width:{w}px;height:{h}px;"
        kind = n["kind"]
        if kind in ("photo", "effect"):
            im = photo_image(n, sources) if kind == "photo" else overlay(n["id"], w, h)
            buf = BytesIO()
            fmt = "PNG" if im.mode == "RGBA" else "JPEG"
            im.save(buf, fmt, quality=94, subsampling=0)
            data = buf.getvalue()
            slide.shapes.add_picture(BytesIO(data), *coords).name = n["id"]
            name = (
                n["id"]
                + "-"
                + hashlib.sha256(data).hexdigest()[:12]
                + "."
                + fmt.lower()
            )
            (media / name).write_bytes(data)
            parts.append(
                f'<img data-kind="{kind}" alt="{n["id"]}" style="{pos}" src="media/{name}">'
            )
        elif kind == "logo":
            path = ROOT / n["path"]
            svg = ET.parse(path).getroot()
            with Image.open(path.with_suffix(".png")) as raster:
                l, t, r, b = raster.getchannel("A").getbbox()
                unit = float(svg.attrib["viewBox"].split()[-1]) / raster.width
                view = [v * unit for v in (l, t, r-l, b-t)]
            vector_logo(slide, path, coords, view)
            svg.set("viewBox", " ".join(map(str, view)))
            src = base64.b64encode(ET.tostring(svg)).decode()
            parts.append(
                f'<img class="logo" alt="徽标" style="{pos}" src="data:image/svg+xml;base64,{src}">'
            )
        elif kind in ("rect", "swatch"):
            if kind == "swatch":
                index = n["index"]
                sample = sample_crop(sources[n["id"]], n["focus"], n["sample"])
                color = "#%02X%02X%02X" % sample.resize(
                    (1, 1), Image.Resampling.BOX
                ).getpixel((0, 0))
            else:
                color = n["color"]
            shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, *coords)
            shape.name = f"M-{index + 1:02}" if kind == "swatch" else "rule"
            shape.fill.solid()
            shape.fill.fore_color.rgb = rgb(color)
            shape.line.fill.background()
            parts.append(f'<div style="{pos}background:{color}"></div>')
        else:
            size = n["size"]
            leading = n["leading"]
            css, ea, latin = FONTS[n["face"]]
            weight = n["weight"]
            tag = {"body": "p", "title": "h2"}.get(n["role"], "div")
            role = "measure-prose" if n["role"] == "body" else n["role"]
            style = (
                pos
                + f"font-family:{css};font-size:{size}px;line-height:{leading};color:{n['color']};font-weight:{weight};letter-spacing:{n['spacing']}em;text-align:{n['align']};text-align-last:{'left' if n['align']=='justify' else 'auto'}"
            )
            value = n["value"]
            markup = escape(value).replace("²", "<sup>2</sup>")
            parts.append(
                f'<{tag} class="text {role}" data-axis="{anchor_x}" data-shift="{shift}" style="{escape(style)}">{markup}</{tag}>'
            )
            shape = slide.shapes.add_textbox(*coords)
            shape.name = n["role"]
            frame = shape.text_frame
            frame.word_wrap = True
            frame.auto_size = MSO_AUTO_SIZE.NONE
            frame.margin_left = frame.margin_right = frame.margin_top = (
                frame.margin_bottom
            ) = 0
            para = frame.paragraphs[0]
            para.alignment = {
                "left": PP_ALIGN.LEFT,
                "justify": PP_ALIGN.JUSTIFY,
                "center": PP_ALIGN.CENTER,
                "right": PP_ALIGN.RIGHT,
            }[n["align"]]
            para.line_spacing = Pt(size * leading / 2)
            for token in filter(None, re.split("(²)", value)):
                run = para.add_run()
                run.text = "2" if token == "²" else token
                run.font.size = Pt(size * (0.9 if token == "²" else 1) / 2)
                run.font.color.rgb = rgb(n["color"])
                run.font.name = latin + (
                    " Light" if weight == 300 and latin == "Helvetica Neue" else ""
                )
                run.font.bold = weight >= 600
                props = run._r.get_or_add_rPr()
                props.set("lang", "zh-CN")
                props.set("spc", str(round(n["spacing"] * size * 50)))
                if token == "²":
                    props.set("baseline", "30000")
                east = OxmlElement("a:ea")
                east.set(
                    "typeface",
                    ea
                    + ({300: " Light", 500: " Medium", 600: " Bold"}.get(weight, "")),
                )
                props.append(east)
    for shape in slide.shapes:
        for ref in shape._element.xpath("./p:style/a:effectRef"):
            ref.set("idx", "0")
    return "".join(parts) + "</section>"


@contextmanager
def chrome(work):
    # Keep Chrome profile, Playwright artifacts and subprocess temp files local.
    env = os.environ.copy()
    env.update(
        TMPDIR=str(work), TMP=str(work), TEMP=str(work), XDG_CACHE_HOME=str(work)
    )
    binary = (
        os.getenv("CHROME_BIN")
        or shutil.which("google-chrome")
        or shutil.which("chromium")
        or str(
            Path(Path.home().anchor)
            / "Applications"
            / "Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    )
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(work / "profile"),
            executable_path=binary,
            headless=True,
            env=env,
            viewport={"width": 1920, "height": 1080},
            args=[
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--disable-component-update",
            ],
        )
        try:
            yield context.new_page()
        finally:
            context.close()


def export(slug, case, script, brand, sources, root, browser, only, staging):
    destination = root / slug
    out = staging / slug
    pages = out / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    stem = f"{case['name']}-{case['area_m2']:g} m²-{THEMES[slug][0]}"
    if Path(stem).name != stem:
        raise ValueError("案名须为文件名")
    deck = out / (stem + ".html")
    if only and any(
        not (destination / "pages" / (p["id"] + ".png")).exists()
        for p in script["pages"]
        if p["id"] != only
    ):
        raise ValueError("请先生成完整画册，再按页返修")
    prs = Presentation()
    prs.slide_width = Emu(1920 * 6350)
    prs.slide_height = Emu(1080 * 6350)
    prs.core_properties.author = brand["name"]
    prs.core_properties.title = stem
    prs.core_properties.last_modified_by = brand["name"]
    prs.core_properties.comments = ""
    parts = []
    media = out / "media"
    media.mkdir()
    for i, p in enumerate(script["pages"], 1):
        paper, nodes = compose(slug, i, p, case, brand, script)
        parts.append(render_slide(prs, p, paper, nodes, sources, media, browser))
    if only:
        from lxml import html

        original = html.fromstring((destination / deck.name).read_text()).xpath(
            "//section"
        )
        if len(original) != len(parts):
            raise ValueError("已有画册页数不同，请完整导出")
        for previous, part in zip(original, parts):
            if previous.get("id") == only:
                continue
            current = html.fromstring(part)
            if html.tostring(previous) != html.tostring(current):
                raise ValueError("其他页面也有改动，请完整导出以同步预览")
    deck.write_text(
        f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{escape(stem)}</title><style>{CSS}</style><body>'
        + "".join(parts)
        + "</body></html>"
    )
    prs.save(out / (stem + ".pptx"))
    browser.goto(deck.resolve().as_uri())
    browser.evaluate("document.fonts.ready")
    errors = browser.locator(".text").evaluate_all("""els=>els.filter(e=>{
      const r=document.createRange();r.selectNodeContents(e);
      const b=r.getBoundingClientRect(),a=e.getBoundingClientRect();
      return b.bottom>a.bottom+1 || b.right>a.right+1
    }).map(e=>e.textContent)""")
    if errors:
        raise ValueError(f"{slug} 文字超框 {errors}")
    browser.evaluate("Promise.all([...document.images].map(img=>img.decode()))")
    browser.pdf(
        path=out / (stem + ".pdf"),
        prefer_css_page_size=True,
        print_background=True,
        display_header_footer=False,
    )
    for p in script["pages"]:
        if not only or only == p["id"]:
            browser.locator("#" + p["id"]).screenshot(
                path=pages / (p["id"] + ".png"), animations="disabled"
            )
    # Publish only after the entire template has passed layout and export checks.
    lint = out / "measure.json"
    shutil.copyfile(
        ROOT / "templates/brochure/measure.config.json", out / "measure.config.json"
    )
    subprocess.run(
        ["measure-cjk", "lint", str(deck), "--root", str(out), "--out", str(lint)],
        check=True,
    )
    report = load(lint)
    lint.write_text(
        json.dumps(report, ensure_ascii=False, indent=2).replace(str(out) + "/", "")
    )
    summary = report["summary"]
    if summary["cjk_files"] != 1 or summary["errors"] or summary["warns"]:
        raise ValueError("Measure 未完整检查源稿")
    (destination / "pages").mkdir(parents=True, exist_ok=True)
    (destination / "media").mkdir(exist_ok=True)
    for artifact in [
        *media.iterdir(),
        lint,
        out / "measure.config.json",
        deck,
        deck.with_suffix(".pdf"),
        deck.with_suffix(".pptx"),
        *pages.glob("*.png"),
    ]:
        artifact.replace(destination / artifact.relative_to(out))
    print(slug, flush=True)


def overview(root, script, only):
    from urllib.parse import quote

    catalog = []
    for slug, theme in THEMES.items():
        decks = sorted((root / slug).glob("*.pptx"))
        if not decks:
            continue
        stem = decks[-1].stem
        catalog.append(
            dict(
                slug=slug, name=theme[0], about=theme[-1], link=slug + "/" + quote(stem)
            )
        )
    ids = [p["id"] for p in script["pages"]]
    if len(catalog) == 4:
        folder = root / "comparison-4k"
        folder.mkdir(exist_ok=True)
        for pid in ids:
            if only and pid != only:
                continue
            canvas = Image.new("RGB", (3840, 2160))
            for j, item in enumerate(catalog):
                with Image.open(root / item["slug"] / "pages" / (pid + ".png")) as im:
                    canvas.paste(im, ((j % 2) * 1920, (j // 2) * 1080))
            canvas.save(folder / (pid + ".jpg"), quality=94)
    data = json.dumps(
        dict(templates=catalog, pages=[(p["id"], p["title"]) for p in script["pages"]]),
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    template = (ROOT / "templates/brochure/review.html").read_text()
    (root / "模板对比总览.html").write_text(template.replace("/*CATALOG*/null", data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("manifest", "script", "brand", "assets-dir", "out-dir"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--template", choices=[*THEMES, "all"], required=True)
    parser.add_argument("--only-page")
    args = parser.parse_args()
    case, script, brand = map(load, (args.manifest, args.script, args.brand))
    for doc, name in [(case, "case-manifest"), (script, "script"), (brand, "brand")]:
        validate(doc, load(ROOT / "schemas" / (name + ".schema.json")))
    ids = [p["id"] for p in script["pages"]]
    if (
        len(set(ids)) != 10
        or len(ids) != 10
        or (args.only_page and args.only_page not in ids)
    ):
        parser.error("需要十个不同的页面 ID，返修 ID 必须存在")
    sources = {}
    for a in case["assets"]:
        if a["adopted"]:
            rel = Path(a["output"])
            sources[a["id"]] = next(
                (
                    p
                    for p in (args.assets_dir / rel, args.assets_dir / rel.name)
                    if p.is_file()
                ),
                None,
            )
    for i, p in enumerate(script["pages"]):
        if len(p["image_ids"]) != (2 if i in (3, 8) else 1) or any(
            not sources.get(k) for k in p["image_ids"]
        ):
            parser.error(f"{p['id']} 图数或素材路径有误")
    if script["pages"][1]["image_ids"] != [case["color_plan"]]:
        parser.error("第二页须采用完整彩平")
    root = args.out_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        os.environ["TMPDIR"] = tmp
        with chrome(Path(tmp)) as browser:
            for slug in THEMES if args.template == "all" else [args.template]:
                export(
                    slug,
                    case,
                    script,
                    brand,
                    sources,
                    root,
                    browser,
                    args.only_page,
                    Path(tmp),
                )
    overview(root, script, args.only_page)


if __name__ == "__main__":
    main()
