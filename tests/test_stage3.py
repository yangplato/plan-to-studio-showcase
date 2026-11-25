import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_brochure import mount_plan
from synthesize_voice import align, stamp


class StageThree(unittest.TestCase):
    def test_tint_preserves_enclosed_white(self):
        im = Image.new("RGB", (360, 120), "white")
        draw = ImageDraw.Draw(im)
        draw.rectangle((30, 30, 100, 100), outline="black", width=4)
        draw.line((359, 0, 359, 119), fill="#cacaca")
        out = mount_plan(im, "#d1c8bb")
        self.assertEqual(out.getpixel((50, 50)), (255, 255, 255))
        self.assertEqual(out.getpixel((359, 55)), (209, 200, 187))
        self.assertEqual(out.getpixel((30, 30)), (0, 0, 0))
        self.assertEqual(out.getpixel((0, 0)), (209, 200, 187))

    def test_word_pause(self):
        self.assertEqual(
            list(
                align(
                    ["先看，", "窗边。"],
                    [("先看", 20, 220), ("窗边", 340, 610)],
                    [],
                    720,
                )
            ),
            [(20, 220), (340, 610)],
        )
        with self.assertRaises(ValueError):
            list(align(["窗", "边"], [("窗边", 25, 500)], [], 550))

    def test_breath_alignment(self):
        intervals = list(
            align(
                ["甲乙，", "丙丁。"],
                [],
                [
                    ("start", 0),
                    ("end", 100),
                    ("start", 450),
                    ("end", 560),
                    ("start", 940),
                    ("end", 1000),
                ],
                1000,
            )
        )
        self.assertEqual(intervals, [(100, 560), (560, 940)])
        self.assertEqual(stamp(60123), "00:01:00,123")

    def test_exported_voice_tracks(self):
        import json
        import wave
        from synthesize_voice import digest, letters

        case = ROOT / "local/showcase-105"
        if not (case / "audio").exists():
            self.skipTest("本地配音样板可选")
        shots = json.loads((case / "script.json").read_text())["shots"]
        phrases = [n for shot in shots for n in shot["narration"]]
        parts = [s["text"] for n in phrases for s in n["subtitles"]]
        for mode in ["edge", "hailuo"]:
            with self.subTest(provider=mode):
                folder = case / "audio" / mode
                t = json.loads((folder / "timeline.json").read_text())
                self.assertEqual(t["sentences"], len(phrases))
                self.assertEqual(
                    t["characters"], sum(len(letters(n["text"])) for n in phrases)
                )
                cues = t["subtitles"]
                self.assertEqual([c["text"] for c in cues], parts)
                self.assertTrue(all(c["start_ms"] < c["end_ms"] for c in cues))
                self.assertTrue(
                    all(a["end_ms"] <= b["start_ms"] for a, b in zip(cues, cues[1:]))
                )
                with wave.open(str(folder / "voice.wav")) as voice:
                    self.assertEqual(
                        (voice.getframerate(), voice.getnchannels()), (48000, 1)
                    )
                    self.assertEqual(t["duration_ms"], voice.getnframes() // 48)
                for n in phrases:
                    key = digest(
                        json.dumps(
                            [mode, t["config"], n["text"]], sort_keys=True
                        ).encode()
                    )
                    meta = json.loads((folder / "cache" / (key + ".json")).read_text())
                    self.assertEqual(
                        meta["sha256"],
                        digest((folder / "cache" / (key + ".mp3")).read_bytes()),
                    )

    def test_area_unit_renders(self):
        from render_video import label

        im = Image.new("RGB", (160, 140), "white")
        label(im, (0, 70), "㎡", 90)
        self.assertGreater(len(im.getcolors(20000)), 1)
