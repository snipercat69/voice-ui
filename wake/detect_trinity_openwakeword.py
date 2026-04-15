#!/usr/bin/env python3
"""Run Trinity wake-word detection on an input audio clip.

Outputs JSON:
  {"ok": true, "score": 0.73, "threshold": 0.55, "detected": true, ...}
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from openwakeword.utils import AudioFeatures


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--audio", required=True, help="Input audio file (webm/wav/...)" )
    p.add_argument("--model", required=True, help="Path to trained pickle model")
    return p.parse_args()


def to_wav_16k_int16(src: Path) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_tmp = Path(tf.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", "-f", "wav", str(wav_tmp)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _, data = wavfile.read(str(wav_tmp))
        if data.dtype != np.int16:
            data = data.astype(np.int16)
        return data
    finally:
        try:
            wav_tmp.unlink(missing_ok=True)
        except Exception:
            pass


MIN_SAMPLES_16K = 24000  # 1.5 seconds at 16kHz – minimum for stable embeddings


def pad_audio(pcm16: np.ndarray, min_samples: int = MIN_SAMPLES_16K) -> np.ndarray:
    """Pad short clips with silence so the embedding model gets enough frames."""
    if len(pcm16) >= min_samples:
        return pcm16
    padded = np.zeros(min_samples, dtype=np.int16)
    padded[:len(pcm16)] = pcm16
    return padded


def clip_feature_vec(pre: AudioFeatures, pcm16: np.ndarray) -> np.ndarray:
    pcm16 = pad_audio(pcm16)
    emb = pre._get_embeddings(pcm16)  # type: ignore[attr-defined]
    if emb.ndim == 1:
        emb = emb[None, :]
    return np.concatenate([emb.mean(axis=0), emb.std(axis=0), emb.max(axis=0)]).astype(np.float32)


def main() -> int:
    args = parse_args()
    audio = Path(args.audio)
    model = Path(args.model)

    if not audio.exists():
        print(json.dumps({"ok": False, "error": f"audio not found: {audio}"}))
        return 2
    if not model.exists():
        print(json.dumps({"ok": False, "error": f"model not found: {model}"}))
        return 3

    with model.open("rb") as f:
        payload = pickle.load(f)

    threshold = float(payload.get("threshold", 0.55))
    clf = payload["classifier"]
    wake_word = payload.get("wakeWord", "trinity")

    pre = AudioFeatures(ncpu=1)
    vec = clip_feature_vec(pre, to_wav_16k_int16(audio))[None, :]
    score = float(clf.predict_proba(vec)[0][1])

    out = {
        "ok": True,
        "wakeWord": wake_word,
        "score": score,
        "threshold": threshold,
        "detected": bool(score >= threshold),
        "engine": "openwakeword-embedding-logreg",
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
