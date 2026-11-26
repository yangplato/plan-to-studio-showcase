"""Editorial directions on a common 1920 × 1080 artboard."""

import re

THEMES = {
    "atelier-warm": ("暖木画刊", "暖纸留白、橄榄色书衣与满幅室内摄影。"),
    "swiss-grid": ("瑞士建筑网格", "十二栏建筑图集，黑体排印与白底留空。"),
    "nocturne-gallery": ("暗夜展厅", "暗场、黄铜展签与柔光摄影。"),
    "editorial-archive": ("营造档案", "棉纸图版、空间索引与五份实景材质采样。"),
}
ROOMS = ("玄关厨房", "六席餐区", "客厅", "卧室一", "卧室二", "主卧套房")
ROUTE = ("玄关与厨房", "六席餐桌居中", "沿窗起居厅", "主卧床尾长案", "两间次卧", "共用卫浴")
PLAN_POINTS = (
    {"index": 1, "label": "玄关厨房", "x": 0.55, "y": 0.13, "keywords": ["厨房", "玄关", "添碗饭"]},
    {"index": 2, "label": "六席餐区", "x": 0.61, "y": 0.255, "keywords": ["长桌", "餐桌", "两边各坐", "一桌人"]},
    {"index": 3, "label": "客厅", "x": 0.60, "y": 0.48, "keywords": ["客厅", "沙发", "起居"]},
    {"index": 4, "label": "卧室一", "x": 0.235, "y": 0.52, "keywords": ["次卧", "卧室一"]},
    {"index": 5, "label": "卧室二", "x": 0.27, "y": 0.82, "keywords": ["卧室二"]},
    {"index": 6, "label": "主卧套房", "x": 0.68, "y": 0.77, "keywords": ["主卧", "套房"]},
)
SECTIONS = (
    "STILL LIFE",
    "FLOOR PLAN",
    "LIVING ROOM",
    "WINDOW SEAT",
    "HEARTH",
    "SHELVES",
    "PRIMARY BEDROOM",
    "TIMBER",
    "TEXTILES",
    "WARDROBE",
)
MATERIALS = (
    ("headboard-grain", "胡桃竖纹", (0.50, 0.48), 0.44),
    ("living-wide", "深灰石纹", (0.234, 0.892), 0.202),
    ("cover-material", "橄榄织物", (0.669, 0.716), 0.18),
    ("clay-cushions", "陶土绒面", (0.423, 0.457), 0.48),
    ("knit-throw", "针织流苏", (0.537, 0.628), 0.49),
)
ARCHIVE_SECONDS = {6: "tv-front", 8: "bedroom-wide"}
HAN_PUNCTUATION = "，。；：、！？"


def guide_lines(body):
    remaining = body.strip()
    lines = []
    for _ in range(2):
        boundaries = [end.end() for end in re.finditer(r"[。！？；]", remaining[:26])]
        if not boundaries:
            boundaries = [end.end() for end in re.finditer(r"[，、：]", remaining[:26])]
        end = boundaries[-1] if boundaries else 25
        lines.append(remaining[:end].strip(HAN_PUNCTUATION) + "。")
        remaining = remaining[end:].lstrip(HAN_PUNCTUATION + " ")
    return tuple(lines)


class Plate:
    def __init__(self, i, p, m, b, script):
        self.i, self.p, self.m, self.brand = i, p, m, b
        self.nodes = []
        self.ink = "#2B2721"
        self.face = "serif"
        self.area = f"{m['area_m2']:g} m²"
        self.heading = p["title"].replace("，", "　")
        self.note = p["body"]
        self.studio = b["name"].partition(" · ")[2]
        self.assets = {a["id"] for a in m["assets"] if a["adopted"]}
        self.photos = [
            a["id"] for a in m["assets"] if a["adopted"] and a["id"] != m["color_plan"]
        ]
        self.asset_rooms = {}
        for fact in m["facts"]:
            for key in fact["image_ids"]:
                self.asset_rooms.setdefault(key, fact["room"])
        # Keep the original art direction for manifests with the original plates.
        self.legacy = {row[0] for row in MATERIALS}.union(ARCHIVE_SECONDS.values()) <= self.assets
        rooms = list(dict.fromkeys(f["room"] for f in m["facts"]))
        self.plan_points = list(m.get("plan_points") or (PLAN_POINTS if self.legacy else []))
        self.rooms = m.get("rooms") or (
            [pt["label"] for pt in self.plan_points] if self.plan_points else (ROOMS if self.legacy else rooms[:6])
        )
        self.route = m.get("route") or (
            [f"{pt['index']:02}   {pt['label']}" for pt in self.plan_points]
            if self.plan_points
            else (ROUTE if self.legacy else self.rooms)
        )
        sections = m.get("sections") or (SECTIONS if self.legacy else None)
        if sections:
            self.section = sections[i - 1]
        elif i == 2:
            self.section = "FLOOR PLAN"
        else:
            self.section = self.asset_rooms.get(p["image_ids"][0], self.heading)
        self.theme = script.get("theme") or m.get("theme") or ("木色与石纹里的日常" if self.legacy else m["layout"])
        self.captions = p.get("captions") or [
            self.asset_rooms.get(key, p["title"])
            for key in p["image_ids"]
        ]

    def guides(self, legacy):
        explicit = self.p.get("guide") or self.m.get("guide", {}).get(self.p["id"])
        return explicit or (legacy if self.legacy else guide_lines(self.note))

    def materials(self):
        configured = self.m.get("materials")
        if configured and all(item["id"] in self.photos for item in configured):
            return [
                (item["id"], item["label"], tuple(item.get("focus", (0.5, 0.5))), item.get("fraction", 0.55))
                for item in configured
            ]
        return MATERIALS if self.legacy else self.default_materials()

    def default_materials(self):
        photos = list(dict.fromkeys([key for key in self.p["image_ids"] if key in self.photos] + self.photos))
        return [
            (key, self.asset_rooms.get(key, f"图版 {j + 1:02}"), (0.5, 0.5), 0.55)
            for j, key in enumerate(photos[:5])
        ]

    def secondary_photo(self):
        legacy = ARCHIVE_SECONDS.get(self.i)
        if self.legacy and legacy in self.assets:
            return legacy
        current = set(self.p["image_ids"])
        explicit = (
            self.m.get("archive_pairs", {}).get(str(self.i)), self.p.get("companion")
        )
        for key in explicit:
            if key in self.photos and key not in current:
                return key
        room = self.asset_rooms.get(self.p["image_ids"][0])
        if room is None:
            return None
        return next(
            (key for key in self.photos if key not in current and self.asset_rooms.get(key) == room),
            None,
        )

    def text(
        self,
        value,
        x,
        y,
        w,
        size,
        *,
        role="label",
        face=None,
        color=None,
        weight=400,
        spacing=0,
        align="left",
        lines=1,
        leading=1.32,
        ink_top=False,
        ink_bottom=False,
        opt_x=True,
    ):
        self.nodes.append(
            dict(
                kind="text",
                value=value,
                box=(x, y, w, round(size * leading * lines + max(5, size * 0.22))),
                size=size,
                role=role,
                face=face or self.face,
                color=color or self.ink,
                weight=weight,
                spacing=spacing,
                align=align,
                leading=leading, ink_top=ink_top, ink_bottom=ink_bottom, opt_x=opt_x,
            )
        )

    def title(self, x, y, w, size, **kw):
        self.text(self.heading, x, y, w, size, role="title", weight=300, **kw)

    def body(self, x, y, w, size=20, **kw):
        # Reserve one paragraph and about 24 full-width glyphs per line.
        size = round(w / 24.5, 1)
        self.text(self.note, x, y, w, size, role="body", align="justify",
                  leading=1.6, lines=4, **kw)

    def quote(self, lines, x, y, w, size=20, align="left"):
        for j, line in enumerate(lines):
            self.text(line, x, y + j * (size + 12), w, size, role="guide", align=align)

    def eyebrow(self, x, y, w=620, **kw):
        self.text(self.section, x, y, w, 13, spacing=0.2, **kw)

    def rect(self, x, y, w, h, color):
        self.nodes.append(dict(kind="rect", box=(x, y, w, h), color=color))

    def photo(
        self, x, y, w, h, j=0, *, asset=None, mat=None, focus=(0.5, 0.5), sample=None, callout_style=None
    ):
        asset_id = asset or self.p["image_ids"][j]
        node = dict(
            kind="photo",
            box=(x, y, w, h),
            id=asset_id,
            focus=focus,
            mat=mat,
            sample=sample,
        )
        if asset_id == self.m["color_plan"] and self.plan_points and callout_style is not False:
            node["callouts"] = self.plan_points
            node["callout_style"] = callout_style or ("#8E3A2A", "#9B8E7C", "#2B2721", "#F2ECE1")
        self.nodes.append(node)

    def effect(self, name, x=0, y=0, w=1920, h=1080):
        self.nodes.append(dict(kind="effect", id=name, box=(x, y, w, h)))

    def logo(self, x, y, size, light=False):
        self.nodes.append(
            dict(
                kind="logo",
                box=(x, y, size, size),
                path=self.brand["logos"]["light" if light else "dark"]["svg"],
            )
        )

    def imprint(self, x, y, w, light=False):
        self.logo(x, y, 36, light)
        cn, _, en = self.brand["name"].partition(" · ")
        self.text(cn, x + 52, y, w - 52, 18, ink_top=True)
        self.text(en, x + 52, y + 36, w - 52, 12, spacing=0.19, ink_bottom=True)

    def index(self, x, y, w, entries=None):
        for j, entry in enumerate(self.route if entries is None else entries):
            self.text(entry, x, y + j * 37, w, 14)


def atelier(d):
    i = d.i
    deep = d.brand["colors"]["primary"]
    paper = "#F2ECE1"
    if i in (1, 10):
        paper = deep
        d.ink = "#F3EDE0"
    if i == 1:
        d.photo(740, 0, 1180, 1080, focus=(0.34, 0.5))
        d.text(
            f"{d.studio}  ·  RESIDENCE NO.{d.m['area_m2']:g}",
            96,
            90,
            630,
            13,
            spacing=0.22,
            color="#CBBFA6",
        )
        d.text(d.m["name"], 96, 330, 635, 72, weight=300, spacing=0.06)
        d.text(d.area, 96, 440, 635, 112, color="#DED3BE")
        d.rect(96, 638, 48, 1, "#CBBFA6")
        d.text(d.theme, 96, 669, 600, 20)
        d.imprint(96, 966, 590, True)
    elif i == 2:
        d.photo(96, 64, 1044, 952, mat=paper, callout_style=("#8E3A2A", "#9B8E7C", "#2B2721", paper))
        d.rect(1236, 125, 1, 830, "#D8CEC0")
        if d.plan_points:
            d.eyebrow(1300, 260)
            d.text(d.area, 1300, 315, 500, 56, weight=300)
            d.text(d.m["layout"], 1300, 398, 500, 21, color="#6B6052")
            d.rect(1300, 456, 48, 1, "#B8AA95")
            d.title(1300, 492, 560, 42)
            d.body(1300, 592, 500)
        else:
            d.eyebrow(1300, 181)
            d.title(1300, 264, 580, 42)
            d.body(1300, 369, 500)
            d.index(1300, 582, 460)
    elif i in (3, 7):
        d.photo(0, 0, 1920, 1080)
        d.effect("bottom", 0, 660, 1920, 420)
        d.ink = "#FFF8EE"
        x = 96 if i == 3 else 1160
        d.eyebrow(x, 790)
        d.title(x, 827, 700, 42)
        guides = {
            3: (
                "浅色沙发与低茶几留出窗前的视线，沿窗的树影尽收眼底。",
                "木扶手单椅稍稍转向沙发，让窗边的人也能加入谈话。",
            ),
            7: (
                "客厅的木色沿着床头延伸，床尾的长案接住窗外的斜光。",
                "细腿留出案下的光影，一枝绿意在暖木衬托下舒展开来。",
            ),
        }
        for row, line in enumerate(d.guides(guides[i])):
            d.text(line, x, 902 + row * 34, 710, 21, role="body", leading=1.6)
        d.text(f"{i:02}", 1800 if i == 3 else 96, 995, 70, 13)
    elif i in (4, 9):
        d.photo(96, 84, 960, 660)
        d.photo(1128, 288, 696, 456, 1)
        d.eyebrow(96, 759, ink_top=True)
        d.title(96, 796, 972, 44, ink_top=True)
        d.text(d.captions[1], 1128, 252, 696, 13)
        d.body(1128, 796, 696, ink_top=True)
    elif i == 6:
        d.photo(360, 76, 1200, 650)
        d.eyebrow(360, 790, ink_top=True)
        d.title(360, 834, 565, 34)
        d.body(960, 790, 600, ink_top=True)
    elif i == 10:
        d.photo(0, 0, 1120, 1080)
        d.eyebrow(1208, 232)
        d.title(1208, 323, 690, 42)
        d.body(1208, 435, 560)
        d.imprint(1208, 765, 600, True)
        d.text(d.brand["closing"].rstrip("。"), 1208, 872, 640, 18)
    else:
        left = i == 5
        d.photo(0 if left else 720, 0, 1200, 1080)
        x = 1280 if left else 96
        d.eyebrow(x, 350)
        d.title(x, 420, 610, 47 if left else 56)
        d.rect(x, 550, 48, 1, "#AA9680")
        d.body(x, 595, 535)
    return paper


def swiss(d):
    d.face = "sans"
    d.ink = "#111111"
    i = d.i

    def number(x, y, size=110, **kw):
        d.text(f"{i:02}", x, y, 300, size, weight=300, **kw)

    if i == 1:
        d.rect(72, 72, 1776, 1, d.ink)
        d.text(d.studio, 72, 95, 425, 14, weight=500)
        d.text("RESIDENTIAL INTERIORS", 672, 95, 570, 14)
        d.photo(972, 232, 876, 776)
        digits = str(int(d.m["area_m2"]))
        # Helvetica Neue Light at 320 px advances each tabular digit by 177.92 px.
        digit_width = round(len(digits) * 177.92)
        d.text(digits, 72, 265, digit_width + 4, 320, weight=300)
        d.text("m²", 72 + digit_width - 6, 320, 240, 96, weight=300)
        d.text(d.m["name"], 72, 736, 850, 59, weight=500)
        d.imprint(72, 952, 700)
    elif i == 2:
        d.photo(672, 72, 1176, 936, mat="#F7F6F2", callout_style=("#E0451F", "#8C8881", "#111111", "#F7F6F2"))
        d.eyebrow(72, 72)
        if d.plan_points:
            d.text(d.area, 72, 210, 540, 78, weight=300)
            d.text(d.m["layout"], 72, 326, 560, 34, weight=300, color="#5A5751")
            d.rect(72, 408, 48, 2, "#E0451F")
            d.title(72, 456, 560, 40)
            d.body(72, 550, 540)
        else:
            d.text(d.m["layout"], 72, 214, 580, 48, weight=300)
            d.title(72, 430, 600, 40)
            d.body(72, 524, 540)
            d.index(72, 755, 576)
    elif i == 3:
        d.photo(0, 0, 1920, 700)
        number(72, 780, ink_top=True)
        d.title(522, 780, 800, 55, ink_top=True)
        d.body(1360, 780, 488, ink_top=True)
    elif i in (4, 6):
        d.eyebrow(72, 72)
        d.title(672, 68, 1176, 52)
        if i == 4:
            d.photo(72, 200, 876, 548)
            d.photo(972, 200, 876, 548, 1)
            for j, x in enumerate((72, 972)):
                d.text(d.captions[j], x, 762, 870, 15)
            d.body(972, 803, 592)
        else:
            d.photo(372, 232, 1476, 616)
            number(72, 700, 92)
            d.body(372, 863, 560)
    elif i in (5, 8):
        d.photo(672 if i == 5 else 822, 0, 1248 if i == 5 else 1098, 1080)
        d.eyebrow(72, 72)
        number(72, 220)
        d.title(72, 712, 726 if i == 8 else 580, 50)
        d.body(72, 838, 550, 18)
    elif i == 7:
        d.photo(0, 0, 1920, 1080)
        d.rect(72, 732, 726, 276, "#F7F6F2")
        d.eyebrow(96, 756)
        d.title(96, 797, 665, 44)
        d.quote(d.guides(("客厅的木色沿着床头延伸，长案接住窗边的光。", "案下留出连贯的光影，也给一枝绿意留出背景。")), 96, 891, 660)
    elif i == 9:
        d.photo(0, 0, 1170, 1080)
        d.eyebrow(1272, 72)
        d.title(1272, 231, 576, 46)
        d.body(1272, 345, 560, 18)
        d.photo(1272, 552, 576, 456, 1)
    else:
        d.text(str(int(d.m["area_m2"])), 72, 150, 850, 205, weight=300)
        d.photo(972, 72, 876, 600)
        d.rect(0, 736, 1920, 344, d.ink)
        d.ink = "#FFFFFF"
        d.imprint(72, 851, 570, True)
        d.title(672, 842, 1100, 55)
    if i == 9:
        d.text("09", 1272, 512, 80, 14, color="#E0451F")
    elif i not in (1, 7, 10):
        d.text(f"{i:02}", 72, 1018, 80, 14, color="#E0451F")
    return "#F7F6F2"


def nocturne(d):
    i = d.i
    d.ink = "#ECE4D6"
    brass = "#B08D57"
    if i in (1, 3, 5, 7):
        d.photo(0, 0, 1920, 1080)
        d.effect({1: "night-cover", 5: "hearth-top"}.get(i, "night"))
        if i == 1:
            d.logo(940, 721, 40, True)
            d.rect(936, 800, 48, 1, brass)
            d.text(
                d.m["name"],
                260,
                829,
                1400,
                69,
                weight=300,
                spacing=0.18,
                align="center",
            )
            d.text(
                d.studio + "  ·  " + d.area,
                260,
                952,
                1400,
                19,
                spacing=0.12,
                align="center",
            )
        elif i == 5:
            d.rect(1430, 92, 48, 1, brass)
            d.eyebrow(1430, 114, 350)
            d.title(1430, 153, 420, 35, spacing=0.10)
            d.quote(d.guides(("一线暖光贴着木格栅下沿，视线从屏幕回到座位。","晚间合上电视，家人仍能在这里相对而坐。")), 1290, 245, 565)
        else:
            x = 100
            d.rect(x, 756, 48, 1, brass)
            d.eyebrow(x, 785)
            d.title(x, 825, 860, 44, spacing=0.18)
            d.quote(d.guides(("低茶几与浅色沙发放低了重心，沿窗的树影尽收眼底。","木扶手单椅转向沙发，窗边的人也在谈话的圈子里。") if i == 3 else ("客厅的木色延续到床头，床尾长案接住窗边的光。","细腿让案下的光影连成一片，绿意有了舒展的背景。")), x, 923, 760)
    elif i == 2:
        d.effect("spot")
        d.photo(176, 72, 1040, 936, mat="transparent", callout_style=(brass, "#8B7350", "#ECE4D6", "#1D1814"))
        d.rect(1300, 399, 60, 1, brass)
        d.eyebrow(1300, 431, 560)
        d.title(1300, 489, 596, 35, spacing=0.18)
        d.text(d.area, 1300, 591, 500, 47)
        d.body(1300, 691, 525, 18)
    elif i in (4, 9):
        d.photo(0, 0, 1180, 1080)
        d.eyebrow(1260, 138, 564)
        d.rect(1260, 194, 48, 1, brass)
        d.title(1260, 228, 564, 38, spacing=0.18)
        d.photo(1260, 609, 564, 375, 1)
        d.text(d.captions[1], 1260, 573, 564, 15)
        d.body(1260, 330, 564)
    elif i in (6, 8):
        d.effect("spot")
        w, h = (1120, 590) if i == 6 else (900, 590)
        # Set group margins in a 1 : 1.35 ratio. The final guide ends 800 px below the plate top.
        y = round((1080 - 800) / 2.35)
        d.photo((1920 - w) // 2, y, w, h)
        d.rect(936, y + 632, 48, 1, brass)
        d.text(d.heading, 300, y + 660, 1320, 34, align="center", spacing=0.12)
        lines = (("两层搁板给书与器物留出高低，也给木面留一段空白。", "一本正在读的书，就能让客厅有一点新的样子。") if i == 6 else
                 ("斜光经过床头的竖纹，相近的木色便拉开了深浅。", "靠在床头翻几页书，熟悉的房间又显出新的细节。"))
        d.quote(d.guides(lines), 300, y + 742, 1320, align="center")
    else:
        d.logo(932, 385, 56, True)
        d.rect(936, 490, 48, 1, brass)
        d.text(d.brand["name"], 200, 534, 1520, 28, spacing=0.16, align="center")
        d.text(d.brand["closing"].rstrip("。"), 200, 629, 1520, 19, align="center")
    return "#1D1814" if i == 2 else "#13100D"


def archive(d):
    i = d.i
    secondary = d.secondary_photo() if i in (6, 8) else None
    d.ink = "#1E1C19"
    red = "#8E3A2A"
    d.effect("paper")
    d.text(f"{d.studio} / ARCHIVE {d.m['area_m2']:g}", 64, 28, 600, 13, face="mono")
    d.text(d.section, 640, 28, 640, 12, align="center")
    d.text(f"PL.{i:02} / 10", 1340, 28, 516, 13, face="mono", color=red, align="right")
    for y in (64, 1028):
        d.rect(64, y, 1792, 1, "#BCB4A6")
    if i == 1:
        d.text("住宅营造手记", 64, 196, 640, 19, spacing=0.13)
        d.text(d.m["name"], 64, 264, 840, 78, weight=600)
        d.text(d.area, 64, 465, 650, 46)
        d.text(d.m["layout"], 64, 556, 680, 21)
        sequence = "客厅 / 主卧" if d.legacy else " / ".join(d.rooms[:2])
        d.text(f"空间序列　{sequence}", 64, 617, 680, 17)
        d.photo(900, 126, 956, 622)
        d.imprint(64, 765, 460)
        for j, (asset, _, focus, fraction) in enumerate(d.materials()):
            d.nodes.append(dict(kind="swatch", index=j, id=asset, focus=focus, sample=fraction,
                                box=(64 + 55 * j, 924, 48, 24)))
    elif i == 2:
        d.text("空间索引", 64, 160, 600, 52)
        if d.plan_points:
            d.text(d.area, 64, 275, 570, 58)
            d.text(d.m["layout"], 64, 368, 570, 22, color="#6A6359")
            d.rect(64, 434, 48, 1, red)
            d.title(64, 474, 570, 36)
            d.body(64, 568, 535)
        else:
            d.index(64, 320, 560, [f"{j:02}     {room}" for j, room in enumerate(d.rooms, 1)])
            d.body(64, 605, 535)
            d.text(d.area, 64, 844, 570, 55)
        d.photo(808, 92, 1048, 892, mat="clear", callout_style=(red, "#8F877B", "#1E1C19", "#F1ECE1"))
    elif i == 9:
        d.text("材料采样", 168, 91, 800, 40, weight=600)
        detail = "木纹、石纹与三种织物，采自本案实景。" if d.legacy else "从本案采用的效果图中截取细节。"
        d.text(detail, 940, 100, 812, 21, align="right")
        for j, (asset, name, focus, fraction) in enumerate(d.materials()):
            x = 168 + j * 324
            d.photo(x, 178, 288, 600, asset=asset, focus=focus, sample=fraction)
            d.nodes.append(dict(kind="swatch", index=j, id=asset, focus=focus, sample=fraction,
                                box=(x, 778, 288, 23)))
            d.text(f"M-{j + 1:02}", x, 827, 282, 13, face="mono", color=red)
            d.text(name, x, 866, 285, 23)
    elif i == 10:
        d.logo(64, 200, 74)
        d.text(d.brand["name"], 64, 330, 1150, 36)
        d.text(d.area + "   /   " + d.m["layout"], 64, 426, 1130, 22)
        d.text(d.brand["closing"].rstrip("。"), 64, 846, 1100, 21)
        d.photo(1248, 539, 608, 405)
    elif i == 4:
        d.photo(64, 139, 960, 652)
        d.photo(1088, 279, 768, 512, 1)
        d.title(64, 837, 970, 43, ink_top=True)
        d.body(1088, 837, 768, ink_top=True)
    elif i in (3, 5, 7) or (i in (6, 8) and secondary is None):
        x = 64 if i != 7 else 514
        d.photo(x, 110, 1342, 792)
        tx = 1460 if i != 7 else 64
        d.text(f"FIG. {i:02} A", tx, 477, 390, 13, face="mono", color=red)
        d.title(tx, 524, 397, 34, lines=2)
        d.body(tx, 647, 377, 17)
    else:
        d.photo(688, 136, 1168, 650)
        d.photo(64, 371, 568, 415, asset=secondary)
        d.title(64, 832, 568, 36, ink_top=True)
        d.body(688, 832, 618, ink_top=True)
    return "#F1ECE1"


DIRECTORS = {
    "atelier-warm": atelier,
    "swiss-grid": swiss,
    "nocturne-gallery": nocturne,
    "editorial-archive": archive,
}


def compose(slug, index, page, manifest, brand, script=None):
    plate = Plate(index, page, manifest, brand, script or {})
    paper = DIRECTORS[slug](plate)
    return paper, plate.nodes
