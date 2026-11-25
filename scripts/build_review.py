"""Build the case review desk with relative, offline links."""

import argparse
from html import escape
from pathlib import Path
import math
from PIL import Image
from render_video import CASE, MODES, filename, read


def build(case):
    manifest, script = read(case / "case-manifest.json"), read(case / "script.json")

    def link(path, text):
        return f'<a href="{escape(str(path), quote=True)}">{escape(text)}</a>'

    def img(path, alt):
        return f'<a href="{escape(path)}"><img loading="lazy" src="{escape(path)}" alt="{escape(alt)}"></a>'

    def section(id, title, body):
        return f'<section id="{id}"><h2>{title}</h2>{body}</section>'

    names = {}
    for page in script["pages"]:
        names.update(zip(page["image_ids"], page.get("captions", [page["title"]])))
    assets = []
    for item in manifest["assets"]:
        if not item["adopted"]:
            continue
        w, h = item["output_size"]
        path = item["output"]
        factor = max(3840 / max(w, h), 2160 / min(w, h))
        method = "原生尺寸" if item["scale"] == 1 else f"{item['scale']}× 超分"
        if factor > 1:
            # Preserve the accepted P1 files. Supply a correctly sized viewing copy.
            export = Path("assets/uhd-review") / (item["id"] + ".jpg")
            dest = case / export
            dest.parent.mkdir(exist_ok=True)
            w, h = math.ceil(w * factor), math.ceil(h * factor)
            with Image.open(case / path) as image:
                image.convert("RGB").resize((w, h), Image.Resampling.LANCZOS).save(
                    dest, quality=96, subsampling=0
                )
            path = export.as_posix()
            method += "，Lanczos 补至 UHD"
        title = names.get(item["id"], item["id"])
        raw_w, raw_h = item["size"]
        assets.append(
            f'<details><summary>{escape(title)} · {escape(item["id"])}</summary><div class="pair">'
            f"<figure>{img(item['raw'], title)}<figcaption>原图 · {raw_w} × {raw_h}</figcaption></figure>"
            f"<figure>{img(path, title)}<figcaption>{method} · {w} × {h}</figcaption></figure></div></details>"
        )
    book = Path("brochures")
    links = []
    for deck in sorted((case / book).glob("*/*.pptx")):
        p = deck.relative_to(case)
        links.append(
            f"<p>{escape(p.stem.split('-')[-1])}　{link(p.with_suffix('.pdf'), 'PDF')}　{link(p, 'PPTX')}</p>"
        )
    films = []
    report_path = case / "videos/video-qa-report.json"
    records = read(report_path) if report_path.is_file() else {}
    titles = {shot["id"]: shot["ambient_label"] for shot in script["shots"]}
    sample_labels = {
        "first_frame": "片头", "plan": "彩平", "living": "客厅", "diptych": "单椅与织毯",
        "hearth": "炉火", "shelves": "搁板", "bedroom": "主卧", "timber": "床头木纹",
        "textiles": "床品", "wardrobe": "衣柜", "brand_closing": "品牌尾句起声", "last_frame": "片尾",
    }
    for mode in MODES:
        path = "videos/" + filename(manifest, mode)
        if not (case / path).is_file():
            continue
        attr = "loop" if mode == "tv" else ""
        poster = f"video-checks/{mode}/first_frame.png"
        frames = []
        for key in records.get(mode, {}).get("frame_samples_s", sample_labels):
            still = f"video-checks/{mode}/{key}.png"
            if (case / still).is_file():
                frames.append(link(still, sample_labels.get(key, titles.get(key, key))))
        films.append(
            f'<article><h3>{MODES[mode]} · 4K</h3><video controls playsinline preload="metadata" {attr} poster="{poster}" src="{escape(path)}"></video><details><summary>查看镜头抽帧</summary><nav>{"".join(frames)}</nav></details></article>'
        )
    body = section(
        "films",
        "横屏成片",
        "".join(films)
        + link("videos/video-qa-report.json", "成片检测数据")
        + ("　" + link("video-checks/stage4-mix-baseline.json", "旧版混音分轨实测")
           if (case / "video-checks/stage4-mix-baseline.json").is_file() else "")
        + "　"
        + link("../../THIRD_PARTY_NOTICES.md", "配乐与采样署名"),
    )
    body += section(
        "books",
        "四套空间画册",
        link(book / "模板对比总览.html", "打开同页四版对比")
        + "　"
        + (link("brochure-checks/交付检查.md", "画册检查记录")
           if (case / "brochure-checks/交付检查.md").is_file() else "")
        + "".join(links)
        + '<div class="grid">'
        + "".join(
            img(f"brochures/comparison-4k/{p['id']}.jpg", p["title"])
            for p in script["pages"]
        )
        + "</div>",
    )
    body += section("assets", "原图与交付图", "".join(assets))
    body += section(
        "copy",
        "口播稿与发布文案",
        "".join(
            f"<details><summary>{name}</summary><pre>{escape((case / 'videos' / name).read_text().replace(chr(0x33A1), ' m²'))}</pre></details>"
            for name in ("口播稿.md", "发布文案.md")
        ),
    )
    (case / "审阅总览.html").write_text(
        f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(manifest["name"])} · 审阅总览</title><style>
*{{box-sizing:border-box}}body{{margin:auto;max-width:1240px;padding:4vw;color:#332e27;background:#f4efe6;font:17px/1.7 system-ui}}
h1,h2,h3{{font-family:"Songti SC",serif;font-weight:500}}h1{{font-size:clamp(36px,6vw,80px);margin-bottom:8px}}h2{{font-size:34px}}
nav{{display:flex;gap:22px;flex-wrap:wrap}}a{{color:#674a33}}section{{margin:56px 0;border-top:1px solid #bea992}}video,img{{width:100%;display:block}}video{{aspect-ratio:16/9;background:#24211d}}
article{{margin-bottom:32px}}.pair,.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}figure{{margin:0}}details{{border-top:1px solid #cec2af;padding:14px 0}}summary{{cursor:pointer}}pre{{white-space:pre-wrap;font:inherit;max-width:50em}}@media(max-width:620px){{.grid{{grid-template-columns:1fr}}}}
</style><h1>{escape(manifest["name"])} · {manifest["area_m2"]:g} m²</h1><nav><a href="#films">成片</a><a href="#books">画册</a><a href="#assets">素材</a><a href="#copy">文案</a></nav>{body}</html>"""
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=CASE)
    build(p.parse_args().case)
