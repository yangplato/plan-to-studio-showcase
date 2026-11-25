"""Read the bundled F-major ABC sketch and render a sample-free harmonic fallback."""

from array import array
from math import exp, pi, sin
from pathlib import Path
import argparse
import re
import wave

SCORE = Path(__file__).resolve().parents[1] / "assets/music/wood-stone-home.abc"


def compose(output, score=SCORE, rate=24000):
    tracks = re.split(r"(?m)^V:", score.read_text())[1:]
    mix = array("f")
    for track in tracks:
        header, notes = track.split("\n", 1)
        gain = float(header.split("gain=")[1])
        piano = "piano" in header
        pos = 0
        for group, length in re.findall(
            r"(\[[A-Ga-g,']+\]|[A-Ga-gz][,']*)(\d*)", re.sub(r"![^!]*!", "", notes)
        ):
            count = round((int(length or 1) * 60 / 96 / 2) * rate)
            end = pos + count
            if len(mix) < end:
                mix.extend(array("f", [0]) * (end - len(mix)))
            for letter, octave in re.findall(r"([A-Ga-g])([,']*)", group):
                midi = dict(C=60, D=62, E=64, F=65, G=67, A=69, B=70)[letter.upper()]
                midi += 12 * (letter.islower() + octave.count("'") - octave.count(","))
                omega = 2 * pi * 440 * 2 ** ((midi - 69) / 12) / rate
                for i in range(count):
                    t = i / rate
                    env = min(
                        1, t / (0.008 if piano else 0.18), (count - i) / (rate * 0.09)
                    )
                    tone = (
                        sin(omega * i)
                        + 0.27 * sin(2 * omega * i)
                        + 0.12 * sin(3 * omega * i)
                    )
                    mix[pos + i] += gain * env * tone * (exp(-2 * t) if piano else 0.55)
            pos = end
    scale = 24000 / max(map(abs, mix))
    with wave.open(str(output), "wb") as w:
        w.setparams((1, 2, rate, 0, "NONE", ""))
        w.writeframes(array("h", (round(v * scale) for v in mix)).tobytes())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    compose(p.parse_args().output)
