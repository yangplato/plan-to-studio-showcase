"""Render whole sentences and align the supplied subtitle phrases."""

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "local/showcase-105"
BRAND = ROOT / "assets/brands/atelier-qimu/brand.json"
RATE = 48000


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def letters(value):
    return "".join(ch for ch in value if ch.isalnum())


async def microsoft(text, config):
    import edge_tts

    proxy = urllib.request.getproxies().get("https")
    stream = edge_tts.Communicate(text, **config, boundary="WordBoundary", proxy=proxy)
    encoded = bytearray()
    marks = []
    async for event in stream.stream():
        if event["type"] == "audio":
            encoded.extend(event["data"])
        elif event["type"] == "WordBoundary":
            marks.append(
                [
                    event["text"],
                    round(event["offset"] / 10000),
                    round((event["offset"] + event["duration"]) / 10000),
                ]
            )
    if not marks:
        raise ValueError("微软未返回词边界")
    return bytes(encoded), marks


def hailuo(text, config):
    key = os.getenv("MINIMAX_API_KEY")
    if not key:
        path = Path.home() / ".config/minimax/api_key"
        if path.is_file():
            key = path.read_text().strip()
    if not key:
        raise SystemExit(
            "请在国内海螺开放平台创建 API Key，并设置 MINIMAX_API_KEY 或写入 ~/.config/minimax/api_key。"
        )
    payload = dict(
        text=text,
        model=config["model"],
        stream=False,
        output_format="hex",
        voice_setting=dict(
            voice_id=config["voice"], speed=config["speed"], vol=1, pitch=0
        ),
        audio_setting=dict(sample_rate=32000, bitrate=128000, format="mp3", channel=1),
    )
    request = urllib.request.Request(
        "https://api.minimaxi.com/v1/t2a_v2",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key.strip(),
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    code = result["base_resp"]["status_code"]
    if code:
        raise RuntimeError(f"海螺接口返回状态码 {code}")
    return bytes.fromhex(result["data"]["audio"]), []


def sentence(cache, provider, config, text):
    token = digest(json.dumps([provider, config, text], sort_keys=True).encode())
    meta, audio = (cache / (token + s) for s in (".json", ".mp3"))
    hit = meta.exists() and audio.exists()
    if hit:
        try:
            record = read(meta)
            data = audio.read_bytes()
            hit = bool(data) and record["sha256"] == digest(data)
            if provider == "edge":
                hit = hit and bool(record["marks"])
        except (ValueError, KeyError, TypeError):
            hit = False
    if not hit:
        data, marks = (
            asyncio.run(microsoft(text, config))
            if provider == "edge"
            else hailuo(text, config)
        )
        record = dict(sha256=digest(data), marks=marks)
        audio.with_suffix(".partial").write_bytes(data)
        audio.with_suffix(".partial").replace(audio)
        write(meta.with_suffix(".tmp"), record)
        meta.with_suffix(".tmp").replace(meta)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio),
        "-af",
        "silencedetect=n=-43dB:d=0.10",
        "-ac",
        "1",
        "-ar",
        str(RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    decoded = subprocess.run(command, capture_output=True, check=True)
    events = re.findall(r"silence_(start|end): ([\d.]+)", decoded.stderr.decode())
    return (
        decoded.stdout,
        record["marks"],
        [(k, round(float(t) * 1000)) for k, t in events],
        hit,
    )


def align(parts, marks, events, duration):
    weights = [len(letters(p)) for p in parts]
    if marks:
        if letters("".join(p[0] for p in marks)) != letters("".join(parts)):
            raise ValueError("词边界文本与字幕不符")
        boundaries, endings, offset = {}, {}, 0
        for word, start, end in marks:
            if letters(word):
                boundaries[offset] = start
                offset += len(letters(word))
                endings[offset] = min(end, duration)
        offsets = [sum(weights[:i]) for i in range(len(parts))]
        if any(i not in boundaries for i in offsets):
            raise ValueError("字幕切点落在词内，请调整分块")
        starts = [boundaries[i] for i in offsets]
        stops = [endings[i + n] for i, n in zip(offsets, weights)]
    else:
        pauses = [
            (a, b)
            for (k, a), (j, b) in zip(events, events[1:])
            if k == "start" and j == "end"
        ]
        first = next((b for a, b in pauses if a < 20), 0)
        end = next((a for a, b in reversed(pauses) if b >= duration - 25), duration)
        starts = [first]
        for i in range(1, len(parts)):
            ideal = first + round((end - first) * sum(weights[:i]) / sum(weights))
            candidates = [
                b
                for a, b in pauses
                if starts[-1] + 80 < b < end - 80 and abs(b - ideal) < 320
            ]
            starts.append(
                min(candidates, key=lambda b: abs(b - ideal)) if candidates else ideal
            )
        stops = starts[1:] + [end]
    if any(a >= b for a, b in zip(starts, stops)):
        raise ValueError("字幕时间轴无效")
    return zip(starts, stops)


def stamp(ms):
    sec, milli = divmod(ms, 1000)
    return f"{sec//3600:02}:{sec//60%60:02}:{sec%60:02},{milli:03}"


def copy_text(script, out):
    out.mkdir(exist_ok=True, parents=True)
    (out / "口播稿.md").write_text(
        "\n\n".join(
            "## "
            + s["ambient_label"]
            + "\n\n"
            + "".join(n["text"] for n in s["narration"])
            for s in script["shots"]
        )
        + "\n"
    )
    (out / "发布文案.md").write_text(script["social"].replace(chr(0x33a1), " m²") + "\n")


def synthesize(args):
    script, brand = read(args.case / "script.json"), read(args.brand)["tts"]
    edge = args.provider == "edge"
    config = (
        dict(
            voice=args.voice or brand["voice"],
            rate=args.rate,
            pitch=args.pitch,
            volume=args.volume,
        )
        if edge
        else dict(
            voice=args.voice or brand["alternative"]["voice"],
            model="speech-2.8-hd",
            speed=args.speed,
        )
    )
    out = args.case / "audio" / args.provider
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    with (out / ".lock").open("a") as lock, tempfile.TemporaryDirectory(dir=out) as tmp:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stage = Path(tmp)
        pcm = bytearray(650 * 96)
        cues, shots = [], []
        hits = count = chars = 0
        for i, shot in enumerate(script["shots"]):
            start = len(pcm) // 96 if i else 0
            for j, n in enumerate(shot["narration"]):
                if j:
                    pcm.extend(bytes(250 * 96))
                data, marks, events, hit = sentence(
                    cache, args.provider, config, n["text"]
                )
                parts = [x["text"] for x in n["subtitles"]]
                if "".join(parts) != n["text"]:
                    raise ValueError("字幕与原句不一致")
                offset = len(pcm) // 96
                duration = len(data) // 96
                for part, (a, b) in zip(parts, align(parts, marks, events, duration)):
                    cues.append(
                        dict(
                            shot=shot["id"],
                            text=part,
                            start_ms=offset + a,
                            end_ms=offset + b,
                        )
                    )
                pcm.extend(data)
                hits += int(hit)
                count += 1
                chars += len(letters(n["text"]))
            pcm.extend(bytes((2800 if i == len(script["shots"]) - 1 else 400) * 96))
            shots.append(dict(id=shot["id"], start_ms=start, end_ms=len(pcm) // 96))
            print(shot["id"], flush=True)
        with wave.open(str(stage / "voice.wav"), "wb") as wav:
            wav.setparams((1, 2, RATE, 0, "NONE", ""))
            wav.writeframes(pcm)
        write(
            stage / "timeline.json",
            dict(
                provider=args.provider,
                config=config,
                duration_ms=len(pcm) // 96,
                characters=chars,
                sentences=count,
                cache_hits=hits,
                shots=shots,
                subtitles=cues,
            ),
        )
        (stage / "subtitles.srt").write_text(
            "\n\n".join(
                f"{i}\n{stamp(c['start_ms'])} --> {stamp(c['end_ms'])}\n{c['text']}"
                for i, c in enumerate(cues, 1)
            )
            + "\n"
        )
        copy_text(script, stage)
        for path in stage.iterdir():
            path.replace(out / path.name)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--case", type=Path, default=CASE)
    p.add_argument("--brand", type=Path, default=BRAND)
    p.add_argument("--provider", choices=["edge", "hailuo"], default="edge")
    p.add_argument("--voice")
    p.add_argument("--rate", default="-12%")
    p.add_argument("--pitch", default="-4Hz")
    p.add_argument("--volume", default="+0%")
    p.add_argument("--speed", type=float, default=1.08)
    synthesize(p.parse_args())
