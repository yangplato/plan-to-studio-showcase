"""独立读取导出包，按页核对可编辑文字、图框、原生形状与预览。"""

import argparse
from collections import Counter
import json
import re
import os
import tempfile
from pathlib import Path
from zipfile import ZipFile

from lxml import html
from PIL import Image
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE_TYPE

if __package__:
    from .build_brochure import ROOT, chrome, compose, load
else:
    from build_brochure import ROOT, chrome, compose, load


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(folder, script, browser=None, manifest=None, brand=None):
    case = folder.parent.parent
    if manifest is None:
        manifest = load(case / "case-manifest.json")
    if brand is None:
        brand = load(ROOT / "assets/brands/atelier-qimu/brand.json")
    decks = list(folder.glob("*.pptx"))
    require(len(decks) == 1, f"{folder.name} 需要一本 PPTX")
    path = decks[0]
    with ZipFile(path) as z:
        require(z.testzip() is None, "PPTX 压缩包损坏")
    prs = Presentation(path)
    slides = html.parse(str(path.with_suffix(".html"))).xpath(
        '//section[@class="slide"]'
    )
    require(len(prs.slides) == len(script["pages"]) == len(slides) == 10, "页数错误")
    require((prs.slide_width, prs.slide_height) == (12192000, 6858000), "页面尺寸错误")
    lint = json.loads((folder / "measure.json").read_text())["summary"]
    require(lint["errors"] == lint["warns"] == 0 and lint["cjk_files"] == 1, "排版检查未通过")
    full = {
        "atelier-warm": {2, 4, 5, 6, 8, 9, 10},
        "swiss-grid": {2, 3, 4, 5, 6, 8, 9},
        "nocturne-gallery": {2, 4, 9},
        "editorial-archive": {2, 3, 4, 5, 6, 7, 8},
    }[folder.name]
    counts = Counter()
    metrics = []
    for index, (slide, source, page) in enumerate(zip(prs.slides, slides, script["pages"]), 1):
        pid = page["id"]
        require(source.get("id") == pid, "页序错误")
        textboxes = [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.TEXT_BOX]
        text = [s.text for s in textboxes]
        if index in full:
            body = [s for s in textboxes if s.name == "body"]
            require(len(body) == 1 and body[0].text == page["body"], f"{pid} 正文不完整")
            require(body[0].text_frame.paragraphs[0].alignment == PP_ALIGN.JUSTIFY,
                    f"{pid} PPTX 正文未两端对齐")
            paragraphs = source.xpath('./p[contains(@style,"text-align:justify")]')
            require(len(paragraphs) == 1 and "text-align-last:left" in paragraphs[0].get("style"),
                    f"{pid} HTML 正文未两端对齐")
            counts["full_paragraphs"] += 1
        for shape in textboxes:
            para = shape.text_frame.paragraphs[0]
            run = para.runs[0]
            east = run._r.xpath("./a:rPr/a:ea")[0].get("typeface")
            latin = run.font.name
            weight = 600 if run.font.bold else 400
            if latin == "Helvetica Neue Light":
                # PPTX names the face. Chromium resolves the same face by family and weight.
                latin = "Helvetica Neue"
                east = east.removesuffix(" Light")
                weight = 300
            metrics.append(
                dict(
                    page=pid,
                    text=shape.text,
                    width=shape.width / 6350,
                    height=shape.height / 6350,
                    size=run.font.size.pt * 2,
                    line=para.line_spacing.pt * 2,
                    latin=latin,
                    east=east,
                    weight=weight,
                )
            )
        expected_text = source.xpath('./*[contains(@class,"text")]')
        require(
            Counter(text) == Counter(e.text_content() for e in expected_text),
            f"{pid} 双轨文字不一致",
        )
        require(not any(chr(0x33A1) in t for t in text), f"{pid} 面积单位有误")
        supers = [
            r
            for shape in textboxes
            for para in shape.text_frame.paragraphs
            for r in para.runs
            if r._r.xpath('./a:rPr[@baseline="30000"]')
        ]
        require(
            len(supers) == len(source.xpath(".//sup"))
            and all(r.text == "2" for r in supers),
            f"{pid} 面积上标丢失",
        )
        counts["superscripts"] += len(supers)
        for shape in textboxes:
            frame = shape.text_frame
            require(
                not any(
                    (
                        frame.margin_top,
                        frame.margin_bottom,
                        frame.margin_left,
                        frame.margin_right,
                    )
                ),
                f"{pid} 文框边距不为零",
            )
        logos = [s for s in slide.shapes if s.name == "brand-logo"]
        require(
            len(logos) == int(index in (1, 10))
            and all(s.shape_type == MSO_SHAPE_TYPE.FREEFORM for s in logos),
            f"{pid} 缺少矢量徽标",
        )
        counts["logos"] += len(logos)
        images = [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
        require(
            [s.name for s in images] == source.xpath("./img[@data-kind]/@alt"),
            f"{pid} 图片图层不一致",
        )
        photos = [
            s
            for s in images
            if s.name in source.xpath('./img[@data-kind="photo"]/@alt')
        ]
        _, nodes = compose(folder.name, index, page, manifest, brand, script)
        expected = [n["id"] for n in nodes if n["kind"] == "photo"]
        if folder.name == "editorial-archive":
            if index in (1, 9):
                swatches = [s for s in slide.shapes if s.name.startswith("M-")]
                expected_swatches = sum(n["kind"] == "swatch" for n in nodes)
                require(
                    len(swatches) == expected_swatches
                    and all(
                        s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE for s in swatches
                    ),
                    "缺少原生色卡",
                )
                counts["swatches"] += len(swatches)
        require([s.name for s in photos] == expected, f"{pid} 图版素材不符")
        # Read both published representations. Check the physical image objects,
        # stacking order and coordinates, including the shared transparency masks.
        require(len(source) == len(slide.shapes), f"{pid} 双轨对象数量不同")
        for node, shape in zip(source, slide.shapes):
            style = node.get("style", "")
            box = [
                float(re.search(rf"(?:^|;){key}:([\d.]+)px", style)[1])
                for key in ("left", "top", "width", "height")
            ]
            if node.get("data-axis") is not None:
                require(abs(box[0] - float(node.get("data-axis")) - float(node.get("data-shift"))) < .01,
                        f"{pid} 墨迹补偿坐标不一致")
                if folder.name == "swiss-grid" and index == 1 and "font-size:320px;" in style:
                    require(float(node.get("data-shift")) < -1, "封面数字光学补偿缺失")
            if shape.name != "brand-logo":
                actual = [
                    v / 6350 for v in (shape.left, shape.top, shape.width, shape.height)
                ]
                require(
                    all(abs(a - b) < 0.01 for a, b in zip(actual, box)),
                    f"{pid} {shape.name} 坐标不同",
                )
            if node.get("data-kind") in ("photo", "effect"):
                require(
                    (folder / node.get("src")).read_bytes() == shape.image.blob,
                    f"{pid} 媒介图片数据不同",
                )
            if shape.name.startswith("M-"):
                fill = str(shape.fill.fore_color.rgb)
                require(fill.lower() in style.lower(), f"{pid} 色卡填色不同")
        for s in slide.shapes:
            require(
                s.left >= 0
                and s.top >= 0
                and s.left + s.width <= prs.slide_width + 1
                and s.top + s.height <= prs.slide_height + 1,
                f"{pid} {s.name} 越界",
            )
        for s in photos:
            w, h = s.image.size
            require(abs((w / h) / (s.width / s.height) - 1) < 0.002, f"{pid} 图片拉伸")
        counts["text"] += len(textboxes)
        counts["photos"] += len(photos)
        counts["vectors"] += (
            len(slide.shapes) - len(textboxes) - len(images) - len(logos)
        )
        with Image.open(folder / "pages" / (pid + ".png")) as im:
            require(im.size == (1920, 1080), f"{pid} 预览尺寸错误")
    if browser is not None:
        browser.goto(path.with_suffix(".html").resolve().as_uri())
        browser.evaluate("document.fonts.ready")
        rows = browser.locator('p[style*="text-align:justify"]').evaluate_all(r"""els=>els.map(e=>{
          const n=e.firstChild, counts=new Map(), range=document.createRange();
          for(let i=0;i<n.length;i++){
            range.setStart(n,i);range.setEnd(n,i+1);
            const y=range.getBoundingClientRect().top;
            counts.set(y,(counts.get(y)||0)+Number(/\p{Script=Han}/u.test(n.data[i])));
          }
          return [e.closest('section').id,[...counts.values()]];
        })""")
        for pid, lines in rows:
            require(len(lines) == 4 and lines[-1] >= 5, f"{pid} 正文分行异常 {lines}")
        require(len(rows) == counts["full_paragraphs"], "浏览器正文计数不符")
        counts["min_last_line_han"] = min(lines[-1] for _, lines in rows)
        # Independently reconstruct the saved PPTX text metrics at canvas scale.
        overflow = browser.evaluate(
            """items=>items.filter(m=>{
          const box=document.createElement('div');
          Object.assign(box.style,{position:'absolute',width:m.width+'px',
            fontFamily:JSON.stringify(m.latin)+','+JSON.stringify(m.east),
            fontSize:m.size+'px',fontWeight:m.weight,lineHeight:m.line+'px',lineBreak:'strict'});
          box.textContent=m.text;document.body.append(box);
          const range=document.createRange();range.selectNodeContents(box);
          const r=range.getBoundingClientRect(), b=box.getBoundingClientRect();
          const bad=r.right>b.right+1 || r.bottom>b.top+m.height+1;
          box.remove();return bad;
        }).map(m=>[m.page,m.text])""",
            metrics,
        )
        require(not overflow, f"按 PPTX 字号与行距回算超框 {overflow}")
    require(counts["superscripts"] > 0, "未找到原生上标")
    return dict(counts)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--script", type=Path, required=True)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--brand", type=Path)
    args = p.parse_args()
    script = json.loads(args.script.read_text())
    folders = sorted({f.parent for f in args.out_dir.glob("*/*.pptx")})
    require(folders, "目录中没有画册")
    manifest = json.loads((args.manifest or args.script.with_name("case-manifest.json")).read_text())
    brand = json.loads(args.brand.read_text()) if args.brand else None
    with tempfile.TemporaryDirectory(dir=args.out_dir) as tmp:
        work = Path(tmp).resolve()
        os.environ["TMPDIR"] = str(work)
        with chrome(work) as browser:
            for folder in folders:
                print(folder.name, verify(folder, script, browser, manifest, brand))
