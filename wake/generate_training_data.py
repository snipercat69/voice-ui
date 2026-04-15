#!/usr/bin/env python3
"""Generate synthetic training data for Trinity wake word detection.

Creates positive clips ("Trinity" in various voices/speeds/noise levels)
and negative clips (common phrases that are NOT the wake word).
"""

import os
import random
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile

PIPER_BIN = "/home/guy/.openclaw/workspace/.venvs/piper/bin/piper"
MODELS = [
    "/home/guy/.openclaw/workspace/models/piper/en_US-lessac-medium.onnx",
    "/home/guy/.openclaw/workspace/models/piper/en_US-ryan-medium.onnx",
]

POSITIVE_PHRASES = [
    "Trinity",
    "Hey Trinity",
    "Trinity can you hear me",
    "Trinity what time is it",
    "Trinity turn on the lights",
    "OK Trinity",
    "Yo Trinity",
    "Trinity help me",
    "Trinity what's the weather",
    "Hey Trinity play some music",
]

NEGATIVE_PHRASES = [
    "What time is it",
    "Turn on the lights",
    "Play some music",
    "What's the weather like",
    "Set a timer for five minutes",
    "Call mom",
    "Send a message",
    "How are you doing today",
    "Tell me a joke",
    "Good morning",
    "What's on my calendar",
    "Remind me to buy groceries",
    "Navigate to the store",
    "Read my emails",
    "Turn off the TV",
    "Open the garage",
    "Lock the door",
    "What's the news today",
    "How tall is Mount Everest",
    "Calculate fifteen percent of eighty",
    "Translate hello to Spanish",
    "Define serendipity",
    "Who won the game last night",
    "Is it going to rain tomorrow",
    "Set the thermostat to seventy two",
    "Dim the bedroom lights",
    "Start the robot vacuum",
    "Order more coffee",
    "Check my bank balance",
    "Find a recipe for pasta",
]


def synthesize(text: str, model: str, output: Path, speed: float = 1.0):
    """Generate a WAV using Piper TTS."""
    cmd = [PIPER_BIN, "--model", model, "--output_file", str(output)]
    if speed != 1.0:
        cmd += ["--length-scale", str(1.0 / speed)]  # piper length_scale: lower = faster
    proc = subprocess.run(
        cmd,
        input=text.encode(),
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Piper failed: {proc.stderr.decode()[:200]}")


def add_noise(wav_path: Path, output: Path, noise_level: float = 0.02):
    """Add white noise to a WAV file."""
    sr, data = wavfile.read(str(wav_path))
    if data.dtype != np.int16:
        data = data.astype(np.int16)
    noise = (np.random.randn(len(data)) * noise_level * 32767).astype(np.int16)
    noisy = np.clip(data.astype(np.int32) + noise.astype(np.int32), -32768, 32767).astype(np.int16)
    wavfile.write(str(output), sr, noisy)


def resample_16k(src: Path, dst: Path):
    """Resample to 16kHz mono WAV."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-f", "wav", str(dst)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main():
    out_dir = Path("/home/guy/.openclaw/workspace/apps/voice-ui/wake/training_data")
    pos_dir = out_dir / "positive"
    neg_dir = out_dir / "negative"
    pos_dir.mkdir(parents=True, exist_ok=True)
    neg_dir.mkdir(parents=True, exist_ok=True)

    speeds = [0.85, 1.0, 1.15]
    noise_levels = [0.0, 0.01, 0.03]

    count = 0

    # Positive samples
    for phrase in POSITIVE_PHRASES:
        for model in MODELS:
            model_tag = "lessac" if "lessac" in model else "ryan"
            for speed in speeds:
                for noise in noise_levels:
                    tag = f"pos_{count:04d}_{model_tag}_s{speed}_n{noise}"
                    raw_wav = pos_dir / f"{tag}_raw.wav"
                    final_wav = pos_dir / f"{tag}.wav"
                    try:
                        synthesize(phrase, model, raw_wav, speed)
                        if noise > 0:
                            tmp_16k = pos_dir / f"{tag}_16k.wav"
                            resample_16k(raw_wav, tmp_16k)
                            add_noise(tmp_16k, final_wav, noise)
                            tmp_16k.unlink(missing_ok=True)
                        else:
                            resample_16k(raw_wav, final_wav)
                        raw_wav.unlink(missing_ok=True)
                        count += 1
                    except Exception as e:
                        print(f"WARN: {tag}: {e}")

    pos_count = count
    print(f"Generated {pos_count} positive clips")

    # Negative samples
    neg_count = 0
    for phrase in NEGATIVE_PHRASES:
        for model in MODELS:
            model_tag = "lessac" if "lessac" in model else "ryan"
            speed = random.choice(speeds)
            noise = random.choice(noise_levels)
            tag = f"neg_{neg_count:04d}_{model_tag}_s{speed}_n{noise}"
            raw_wav = neg_dir / f"{tag}_raw.wav"
            final_wav = neg_dir / f"{tag}.wav"
            try:
                synthesize(phrase, model, raw_wav, speed)
                if noise > 0:
                    tmp_16k = neg_dir / f"{tag}_16k.wav"
                    resample_16k(raw_wav, tmp_16k)
                    add_noise(tmp_16k, final_wav, noise)
                    tmp_16k.unlink(missing_ok=True)
                else:
                    resample_16k(raw_wav, final_wav)
                raw_wav.unlink(missing_ok=True)
                neg_count += 1
            except Exception as e:
                print(f"WARN: {tag}: {e}")

    print(f"Generated {neg_count} negative clips")
    print(f"Total: {pos_count} positive + {neg_count} negative = {pos_count + neg_count}")


if __name__ == "__main__":
    main()
