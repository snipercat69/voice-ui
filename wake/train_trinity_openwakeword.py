#!/usr/bin/env python3
"""
Train a lightweight wake-word classifier for "Trinity" using openWakeWord embeddings.

Approach:
- Pull recent mic chunks from apps/voice-ui/tmp
- Auto-transcribe chunks via local Voice UI /api/transcribe
- Label clips as positive when transcript contains wake-word variants
- Train logistic regression on openWakeWord embedding summaries (mean/std/max)

This is intentionally pragmatic for fast local iteration and can be retrained often.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
import pickle

from openwakeword.utils import AudioFeatures


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--audio-dir", required=True, help="Directory with recent mic chunks (webm/wav)")
    p.add_argument("--model-out", required=True, help="Output pickle path")
    p.add_argument("--meta-out", required=True, help="Output metadata JSON path")
    p.add_argument("--wake-word", default="trinity", help="Primary wake word")
    p.add_argument("--transcribe-url", default="http://127.0.0.1:8765/api/transcribe")
    p.add_argument("--max-files", type=int, default=220)
    p.add_argument("--cache-path", default="", help="Optional JSON cache for transcriptions")
    return p.parse_args()


def wake_regex(wake_word: str) -> re.Pattern[str]:
    ww = re.escape((wake_word or "trinity").strip().lower())
    variants = [ww, "trinty", "trini", "trinite", "trinitys"]
    return re.compile(r"\b(" + "|".join(variants) + r")\b", re.IGNORECASE)


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


def clip_feature_vec(pre: AudioFeatures, pcm16: np.ndarray) -> np.ndarray:
    emb = pre._get_embeddings(pcm16)  # type: ignore[attr-defined]
    if emb.ndim == 1:
        emb = emb[None, :]
    return np.concatenate([emb.mean(axis=0), emb.std(axis=0), emb.max(axis=0)]).astype(np.float32)


def transcribe_file(audio_path: Path, transcribe_url: str) -> str:
    # Use curl for multipart simplicity (same mechanism used elsewhere in workspace)
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "-X",
            "POST",
            "-F",
            f"audio=@{audio_path};type=audio/webm",
            transcribe_url,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ""
    try:
        j = json.loads(proc.stdout)
    except Exception:
        return ""
    if not j.get("ok"):
        return ""
    return (j.get("transcript") or "").strip()


def main() -> int:
    args = parse_args()

    audio_dir = Path(args.audio_dir)
    model_out = Path(args.model_out)
    meta_out = Path(args.meta_out)
    model_out.parent.mkdir(parents=True, exist_ok=True)
    meta_out.parent.mkdir(parents=True, exist_ok=True)

    if not audio_dir.exists():
        raise SystemExit(f"Audio dir not found: {audio_dir}")

    rx = wake_regex(args.wake_word)

    # Optional transcription cache
    cache_path = Path(args.cache_path) if args.cache_path else None
    cache: dict[str, str] = {}
    if cache_path and cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    files = sorted(audio_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)[: args.max_files]

    positives: list[tuple[Path, str]] = []
    negatives: list[tuple[Path, str]] = []
    empty = 0

    for p in files:
        key = f"{p.name}:{int(p.stat().st_mtime)}:{p.stat().st_size}"
        transcript = cache.get(key, "")
        if not transcript:
            transcript = transcribe_file(p, args.transcribe_url)
            cache[key] = transcript

        if not transcript:
            empty += 1
            continue

        if rx.search(transcript.lower()):
            positives.append((p, transcript))
        else:
            negatives.append((p, transcript))

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    if len(positives) < 3:
        raise SystemExit(f"Need >=3 positive examples; found {len(positives)} (empty clips: {empty})")
    if len(negatives) < 8:
        raise SystemExit(f"Need >=8 negative examples; found {len(negatives)} (empty clips: {empty})")

    random.shuffle(negatives)
    negatives = negatives[: max(16, len(positives) * 4)]

    pre = AudioFeatures(ncpu=1)

    X: list[np.ndarray] = []
    y: list[int] = []

    for p, _ in positives:
        try:
            vec = clip_feature_vec(pre, to_wav_16k_int16(p))
            X.append(vec)
            y.append(1)
        except Exception:
            continue

    for p, _ in negatives:
        try:
            vec = clip_feature_vec(pre, to_wav_16k_int16(p))
            X.append(vec)
            y.append(0)
        except Exception:
            continue

    X_arr = np.asarray(X, dtype=np.float32)
    y_arr = np.asarray(y, dtype=np.int32)

    if X_arr.shape[0] < 12 or y_arr.sum() < 3:
        raise SystemExit("Insufficient usable examples after preprocessing")

    X_tr, X_te, y_tr, y_te = train_test_split(
        X_arr, y_arr, test_size=0.35, random_state=42, stratify=y_arr
    )

    clf = make_pipeline(
        StandardScaler(with_mean=True),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )
    clf.fit(X_tr, y_tr)

    probs = clf.predict_proba(X_te)[:, 1]
    auc = float(roc_auc_score(y_te, probs)) if len(set(y_te.tolist())) > 1 else None

    candidates = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]
    best_th = 0.45
    best_score = -1.0
    for th in candidates:
        pred = (probs >= th).astype(np.int32)
        tp = int(((pred == 1) & (y_te == 1)).sum())
        fp = int(((pred == 1) & (y_te == 0)).sum())
        fn = int(((pred == 0) & (y_te == 1)).sum())
        precision = tp / max(1, tp + fp)
        recall = tp / max(1, tp + fn)
        f1 = 2 * precision * recall / max(1e-9, precision + recall)
        score = f1 - (0.35 * fp)
        if score > best_score:
            best_score = score
            best_th = th

    final_clf = make_pipeline(
        StandardScaler(with_mean=True),
        LogisticRegression(max_iter=2000, class_weight="balanced"),
    )
    final_clf.fit(X_arr, y_arr)

    payload = {
        "wakeWord": args.wake_word,
        "modelType": "openwakeword-embedding-logreg",
        "threshold": float(best_th),
        "classifier": final_clf,
    }
    with model_out.open("wb") as f:
        pickle.dump(payload, f)

    meta = {
        "wakeWord": args.wake_word,
        "audioDir": str(audio_dir),
        "filesScanned": len(files),
        "nonEmptyTranscripts": len(positives) + len(negatives),
        "emptyOrSkipped": empty,
        "positivesUsed": int(sum(1 for yy in y if yy == 1)),
        "negativesUsed": int(sum(1 for yy in y if yy == 0)),
        "threshold": float(best_th),
        "aucHoldout": auc,
        "modelPath": str(model_out),
    }
    meta_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, **meta}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
