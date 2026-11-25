"""Stage 5 regression checks for actual separated stems, layout and publication."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_brochure import optical_Shift
from layouts import compose
from master_audio import HZ
from verify_video import check_levels, stem_levels

CASE = ROOT / "local/showcase-105"
EXAMPLE = ROOT / "examples/courtyard-105-sample"


def sample(slug, number):
    manifest, script, brand = [json.loads((EXAMPLE / f).read_text()) for f in
                               ("case-manifest.example.json", "script.example.json", "brand.json")]
    return compose(slug, number, script["pages"][number - 1], manifest, brand)[1]


class StageFive(unittest.TestCase):
    def test_stem_measurement_rejects_missing_and_masking_music(self):
        voice = np.zeros(HZ * 2, dtype="float32")
        voice[HZ:] = 10 ** (-18 / 20)
        music = np.full((len(voice), 2), 10 ** (-25.5 / 20))
        music[HZ:] = 10 ** (-32 / 20)
        levels = stem_levels(voice, voice, music)
        check_levels(levels)
        self.assertAlmostEqual(levels["bed_minus_voice_db"], -14, places=1)
        for gain in (.12, 4):
            with self.assertRaises(ValueError):
                check_levels(stem_levels(voice, voice, music * gain))
        music[:HZ] *= .1
        with self.assertRaises(ValueError):
            check_levels(stem_levels(voice, voice, music))

    def test_mixed_editions(self):
        if not CASE.exists():
            self.skipTest("本地样案可选")
        records = json.loads((CASE / "videos/video-qa-report.json").read_text())
        for mode in ("edge", "hailuo"):
            check_levels(records[mode]["premaster_stems"])
            self.assertTrue(records[mode]["premaster_stems"]["verified_muxed_aac"])
            self.assertEqual(len(records[mode]["logo_luma_contrast"]), 12)
        self.assertEqual(records["tv"]["loop_rgb"]["changed_pixels"], 0)
        self.assertEqual(len(records["tv"]["logo_luma_contrast"]), 11)

    def test_double_image_anchors_and_type(self):
        for num in (4, 9):
            nodes = sample("atelier-warm", num)
            left, right = [n["box"] for n in nodes if n["kind"] == "photo"]
            body = next(n for n in nodes if n.get("role") == "body")
            self.assertEqual(left[1] + left[3], right[1] + right[3])
            self.assertEqual(right[1] + right[3], 744)
            self.assertEqual(body["box"][:3], (right[0], 796, right[2]))
            self.assertEqual(body["align"], "justify")
            self.assertTrue(body["ink_top"])
            self.assertGreater(len(body["value"]), 85)
        self.assertLess(optical_Shift("105", "sans", 320, 300)[0], -1)

    def test_exported_ink_hits_reference_lines(self):
        books = CASE / "brochures"
        if not books.exists():
            self.skipTest("本地画册可选")
        def gray(slug, page):
            with Image.open(books / slug / "pages" / (page + ".png")) as image:
                return np.asarray(image.convert("L"))
        cover = gray("swiss-grid", "p01")
        for top, bottom in ((360, 620), (92, 125), (750, 832), (950, 1000)):
            self.assertEqual(np.flatnonzero((cover[top:bottom, :530] < 128).any(axis=0))[0], 72)
        for slug, page, band, spans, expected in (
            ("swiss-grid", "p03", 743, ((72, 225), (522, 690), (1360, 485)), 780),
            ("atelier-warm", "p04", 790, ((96, 960), (1128, 696)), 796),
        ):
            pixels = gray(slug, page)
            for x, width in spans:
                ink = pixels[band:expected + 30, x:x + width] < 128
                self.assertEqual(band + np.flatnonzero(ink.any(axis=1))[0], expected)

    def test_public_readme_art_and_links(self):
        from urllib.parse import unquote, urlsplit
        for file in ("README.md", "SKILL.md"):
            doc = (ROOT / file).read_text()
            for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', doc):
                parsed = urlsplit(link)
                if not parsed.scheme:
                    self.assertTrue((ROOT / unquote(parsed.path)).is_file(), link)
        images = list((ROOT / "assets/readme").glob("*.jpg"))
        self.assertEqual(len(images), 3)
        for path in images:
            self.assertTrue(350000 <= path.stat().st_size <= 700000)
            with Image.open(path) as im:
                self.assertEqual(im.size, (2400, 1350))
                self.assertFalse(im.getexif())
                self.assertFalse(im.info.get("comment"))
        media = ROOT / "assets/readme/media"
        gif = media / "showcase-preview.gif"
        mp4 = media / "showcase-highlight-720p.mp4"
        readme = (ROOT / "README.md").read_text()
        for path, budget in ((gif, (2_500_000, 4_800_000)), (mp4, (4_500_000, 7_500_000))):
            self.assertIn(f"]({path.relative_to(ROOT).as_posix()})", readme)
            self.assertTrue(budget[0] <= path.stat().st_size <= budget[1], path.name)
        with Image.open(gif) as im:
            self.assertEqual((im.size, im.info["loop"]), ((960, 540), 0))
            duration = 0
            for i in range(im.n_frames):
                im.seek(i)
                duration += im.info["duration"]
            self.assertTrue(16_000 <= duration <= 20_000)
            self.assertAlmostEqual(im.n_frames * 1000 / duration, 12, delta=0.1)
        probe = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4),
        ]))
        video, audio = probe["streams"]
        self.assertEqual(
            (video["width"], video["height"], video["avg_frame_rate"], video["codec_name"], video["pix_fmt"]),
            (1280, 720, "30/1", "h264", "yuv420p"),
        )
        self.assertEqual((audio["codec_name"], audio["channels"]), ("aac", 2))
        self.assertTrue(24 <= float(probe["format"]["duration"]) <= 28)
