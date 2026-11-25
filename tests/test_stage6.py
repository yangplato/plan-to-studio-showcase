"""Regression checks for the right-rail subtitle and case-specific plate content."""

import json
from pathlib import Path
import sys
import unittest

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_brochure import THEMES, compose
from render_video import PAPER, SIZE, board, corner, names, plan_caption_lines, subtitle

EXAMPLE = ROOT / "examples/courtyard-105-sample"


def fixtures():
    return [json.loads((EXAMPLE / name).read_text()) for name in (
        "case-manifest.example.json", "script.example.json", "brand.json",
    )]


class StageSix(unittest.TestCase):
    def test_plan_caption_ink_stays_on_right_axis(self):
        manifest, script, brand = fixtures()
        case = ROOT / "local/showcase-105"
        if case.exists():
            background = board(case, manifest, script, brand, 1)
        else:
            background = Image.new("RGB", SIZE, PAPER)
        logo = brand["logos"]["dark"]["png"]
        badge = corner(*names(brand), logo, True)
        badge_xy = (3680 - badge.width + 36, 1901)
        background.paste(badge, badge_xy, badge)
        badge_top = badge_xy[1] + badge.getchannel("A").getbbox()[1]
        single = "两边各坐三个人"
        double = "沿着餐桌走到客厅，窗边留了两个能一起谈话的座位"
        for cue, lines in ((single, 1), (double, 2)):
            with self.subTest(cue=cue):
                self.assertEqual(len(plan_caption_lines(cue)), lines)
                rendered = background.copy()
                subtitle(rendered, cue, 1)
                left, top, right, bottom = ImageChops.difference(background, rendered).getbbox()
                self.assertEqual(left, 2463)
                self.assertLessEqual(right, 3680)
                self.assertLess(top, 1560)
                self.assertGreater(bottom, 1560)
                self.assertGreater(badge_top - bottom, 280)
                self.assertLess(bottom, 1840)

    def test_fresh_case_composes_all_templates(self):
        manifest, script, brand = fixtures()
        asset_ids = {
            entry["id"]: f"trial-image-{i:02}" for i, entry in enumerate(manifest["assets"], 1)
        }
        manifest.update(name="测试住宅", area_m2=95, color_plan=asset_ids[manifest["color_plan"]])
        manifest["facts"] = [
            {"room": f"测试空间 {index}", "fact": "浅色台面可见。", "image_ids": [item["id"]]}
            for index, item in enumerate(manifest["assets"], 1) if item["adopted"]
        ]
        for entry in manifest["assets"]:
            entry["id"] = asset_ids[entry["id"]]
        for fact in manifest["facts"]:
            fact["image_ids"] = [asset_ids[k] for k in fact["image_ids"]]
        script["theme"] = "测试住宅的材质与光线"
        for page in script["pages"]:
            page["body"] = "这里采用了整面的素色板。" * 8
            page["image_ids"] = [asset_ids[k] for k in page["image_ids"]]
            page.pop("captions", None)
        adopted = {item["id"] for item in manifest["assets"] if item["adopted"]}
        old = set(asset_ids)
        for slug in THEMES:
            for i, page in enumerate(script["pages"], 1):
                with self.subTest(template=slug, plate=i):
                    _, nodes = compose(slug, i, page, manifest, brand, script)
                    photos = [node for node in nodes if node["kind"] == "photo"]
                    self.assertTrue(all(n["id"] in adopted - old for n in photos))
                    guides = [n["value"] for n in nodes if n.get("role") == "guide"]
                    if slug == "atelier-warm" and i in (3, 7):
                        guides = [n["value"] for n in nodes if n.get("role") == "body"]
                    if guides:
                        self.assertEqual(len(guides), 2)
                        self.assertTrue(all(len(line) <= 26 and line in page["body"] for line in guides))
                    if slug == "atelier-warm" and i == 1:
                        self.assertIn(script["theme"], [n.get("value") for n in nodes])
                    if slug == "editorial-archive" and i in (6, 8):
                        self.assertEqual([n["id"] for n in photos], page["image_ids"])
                        self.assertEqual(photos[0]["box"][2:], (1342, 792))
                    if slug == "editorial-archive" and i == 9:
                        self.assertEqual(len({n["id"] for n in photos}), 5)
                        self.assertTrue(all(n["focus"] == (0.5, 0.5) for n in photos))
                        self.assertNotIn(manifest["color_plan"], [n["id"] for n in photos])

        rooms = {fact["image_ids"][0]: fact["room"] for fact in manifest["facts"]}
        companion = asset_ids["window-chair"]
        excluded = asset_ids["framed-art"]
        for i in (6, 8):
            page = script["pages"][i - 1]
            primary = page["image_ids"][0]
            same_room = {
                **manifest,
                "facts": [
                    {**fact, "room": rooms[primary]} if companion in fact["image_ids"] else fact
                    for fact in manifest["facts"]
                ],
            }
            pairings = (
                ("same room", same_room, page, companion),
                ("manifest pair", {**manifest, "archive_pairs": {str(i): companion}}, page, companion),
                ("page companion", manifest, {**page, "companion": companion}, companion),
                (
                    "excluded or plan",
                    {**manifest, "archive_pairs": {str(i): excluded}},
                    {**page, "companion": manifest["color_plan"]},
                    None,
                ),
                ("unclassified", {**manifest, "facts": []}, page, None),
            )
            for source, case, plate, expected in pairings:
                with self.subTest(plate=i, source=source):
                    _, nodes = compose("editorial-archive", i, plate, case, brand, script)
                    photos = [node["id"] for node in nodes if node["kind"] == "photo"]
                    self.assertEqual(photos, [primary, expected] if expected else [primary])
                    if source == "same room":
                        paired_rooms = {
                            fact["room"] for fact in case["facts"] if fact["image_ids"][0] in photos
                        }
                        self.assertEqual(paired_rooms, {rooms[primary]})
