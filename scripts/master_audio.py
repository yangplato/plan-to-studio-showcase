"""Build a circular instrumental bed and normalize the final stereo mix in two passes."""

import json
from pathlib import Path
import re
import subprocess

import numpy as np
from scipy.ndimage import gaussian_filter1d
from synthesize_voice import ROOT

HZ = 48000
CREDIT = "Wood, Stone, Home | Salamander Grand Piano V3: Alexander Holm, CC BY 3.0 | VSCO 2 CE: Versilian Studios / Sam Gossner, CC0 | edited and mixed"


def decode(path, channels, filters="anull"):
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-af",
            filters,
            "-ar",
            str(HZ),
            "-ac",
            str(channels),
            "-f",
            "f32le",
            "-",
        ]
    )
    return np.frombuffer(raw, dtype="<f4").reshape(-1, channels)


def source_bed(mode, duration, work):
    stem = ROOT / "assets/music/chamber-stem.m4a"
    if not stem.is_file():
        from compose_music import compose

        stem = work / "score.wav"
        compose(stem)
    music = decode(stem, 2)
    n = round(duration * HZ)
    overlap = 2 * HZ
    # Keep the cadence; tempo-fit the entire performance before joining the seam.
    if mode == "tv":
        speed = len(music) / (n + overlap)
        music = decode(stem, 2, f"atempo={speed:.9f}")
        music = np.pad(music, ((0, max(0, n + overlap - len(music))), (0, 0)))[
            : n + overlap
        ]
    length = len(music) - overlap
    ramp = np.linspace(0, 1, overlap, dtype=np.float32)[:, None]
    ring = np.concatenate(
        [music[overlap:length], music[length:] * (1 - ramp) + music[:overlap] * ramp]
    )
    bed = np.tile(ring, (int(np.ceil(n / len(ring))), 1))[:n]
    return bed


BLOCK = HZ // 100
REFERENCE = 10 ** (-18 / 20)


def frame_power(signal):
    padded = np.pad(signal.reshape(len(signal), -1), ((0, -len(signal) % BLOCK), (0, 0)))
    return np.mean(padded.reshape(-1, BLOCK, padded.shape[1]) ** 2, axis=(1, 2))


def active_rms(power, threshold):
    active = power[power > threshold]
    if not active.size:
        raise ValueError("音轨缺少有效信号")
    return np.sqrt(active.mean())


def sample_curve(values, length):
    return np.interp(np.arange(length) / BLOCK, np.arange(len(values)), values).astype("float32")


def separated(case, mode, duration, work):
    bed = source_bed(mode, duration, work)
    n = len(bed)
    voice = decode(case / "audio" / mode / "voice.wav", 1)[:, 0]
    voice = np.pad(voice, (0, max(0, n - len(voice))))[:n]
    power = frame_power(voice)
    voice *= REFERENCE / active_rms(power, 10 ** (-42 / 10))
    # Relative gate keeps a quietly recorded performance eligible for calibration.
    power = frame_power(bed)
    bed *= REFERENCE / active_rms(power, power.max() * 1e-5)
    # Ride sustained musical dynamics slowly, preserving individual piano transients.
    level = gaussian_filter1d(frame_power(bed), 38)
    ride = np.clip(REFERENCE / np.sqrt(level + 1e-12), 0.5, 3)
    bed *= sample_curve(ride, n)[:, None]
    bed *= REFERENCE / np.sqrt(np.mean(bed ** 2))
    activity = np.clip((10 * np.log10(frame_power(voice) + 1e-12) + 38) / 20, 0, 1)
    envelope = gaussian_filter1d(activity, 2)
    state = 0.
    for i, value in enumerate(envelope):
        coefficient = np.exp(-0.01 / (0.08 if value > state else 0.45))
        state = coefficient * state + (1 - coefficient) * value
        envelope[i] = state
    bed *= sample_curve(10 ** ((-7.5 - 6.5 * envelope) / 20), n)[:, None]
    tail = min(round(1.2 * HZ), n)
    fade = np.linspace(1, 0, tail)
    bed[-tail:] *= fade[:, None]
    voice[-tail:] *= fade
    return voice, bed


def master(case, mode, duration, work):
    if mode == "tv":
        bed = source_bed(mode, duration, work)
    else:
        voice, bed = separated(case, mode, duration, work)
        bed += voice[:, None]
    raw = work / "mix.f32"
    bed.astype("<f4").tofile(raw)
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
        "-f",
        "f32le",
        "-ar",
        str(HZ),
        "-ac",
        "2",
        "-i",
        str(raw),
    ]
    target = "-18" if mode == "tv" else "-16"
    peak = "-2.4" if mode == "tv" else "-1.8"
    norm = f"loudnorm=I={target}:TP={peak}:LRA=11"
    log = subprocess.run(
        args + ["-af", norm + ":print_format=json", "-f", "null", "-"],
        capture_output=True,
        check=True,
    ).stderr.decode()
    stats = json.loads(re.search(r'\{[^{}]*"input_i"[^{}]*\}', log)[0])
    for dst, src in [
        ("measured_I", "input_i"),
        ("measured_TP", "input_tp"),
        ("measured_LRA", "input_lra"),
        ("measured_thresh", "input_thresh"),
        ("offset", "target_offset"),
    ]:
        norm += f":{dst}={stats[src]}"
    out = work / "master.m4a"
    gain_db = 0.0
    ceiling = float(peak) - 0.4
    for attempt in range(5):
        filters = norm + ":linear=true"
        if mode != "tv":
            # AAC can reconstruct higher inter-sample peaks than the PCM master.
            # Limit at 192 kHz and compensate latency before the 48 kHz encode.
            filters += (f",aresample=192000,volume={gain_db:.3f}dB,"
                        f"alimiter=limit={10 ** (ceiling / 20):.7f}:level=false:latency=true")
        subprocess.run(args + ["-loglevel", "error", "-af", filters,
                       "-ar", "48000", "-c:a", "aac", "-b:a", "256k", str(out)], check=True)
        if mode == "tv":
            return out
        measured = subprocess.run(["ffmpeg", "-nostats", "-i", str(out), "-af",
            "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
            check=True, capture_output=True).stderr.decode().rsplit("Summary:", 1)[1]
        actual_i = float(re.search(r"I:\s+([-\d.]+) LUFS", measured)[1])
        actual_tp = float(re.search(r"Peak:\s+([-\d.]+) dBFS", measured)[1])
        if abs(actual_i - float(target)) <= 0.08 and actual_tp <= float(peak):
            return out
        gain_db += float(target) - actual_i
        if actual_tp > float(peak):
            ceiling -= actual_tp - float(peak) + 0.2
    raise ValueError(f"AAC 母带校验失败 {actual_i} LUFS, {actual_tp} dBTP")
