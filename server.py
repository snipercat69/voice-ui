#!/usr/bin/env python3
"""
Local Voice UI backend
- Voice turn pipeline (transcribe -> reply -> tts)
- Persistent history logging (JSONL)
- Telegram mirror logging
- Voice provider/model selection (Fish + ElevenLabs + Piper + Kokoro)
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import cgi
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import urllib.error
import urllib.parse

HOST = os.environ.get("VOICE_UI_HOST", "0.0.0.0")
PORT = int(os.environ.get("VOICE_UI_PORT", "8765"))
APP_ROOT = Path(__file__).parent
WEB_ROOT = APP_ROOT / "web"
TMP_ROOT = APP_ROOT / "tmp"
DATA_ROOT = APP_ROOT / "data"
OUT_ROOT = Path("/home/guy/.openclaw/workspace/out")
HISTORY_FILE = DATA_ROOT / "history.jsonl"
VOICE_STATE_FILE = DATA_ROOT / "voice-state.json"
REMINDERS_FILE = DATA_ROOT / "reminders.json"

WAKE_VENV_PYTHON = Path("/home/guy/.openclaw/workspace/.venvs/openwakeword/bin/python")
WAKE_MODEL_PATH = APP_ROOT / "wake" / "trinity_openwakeword.pkl"
WAKE_DETECT_SCRIPT = APP_ROOT / "wake" / "detect_trinity_openwakeword.py"

TRANSCRIBE_CMD = [
    "/home/guy/.openclaw/workspace/.venvs/faster-whisper/bin/python",
    "/home/guy/.openclaw/workspace/scripts/media/local_faster_whisper.py",
]
FISH_TTS_CMD = [
    "python3",
    "/home/guy/.openclaw/workspace/scripts/fish_audio_tts.py",
]

ELEVEN_API_BASE = "https://api.elevenlabs.io"
DEFAULT_REPLY_STYLE = (os.environ.get("VOICE_REPLY_STYLE", "short") or "short").strip().lower()

# Session-local voice preferences for this UI backend.
VOICE_CONFIG = {
    "provider": "fish",            # fish | elevenlabs | local-piper | local-kokoro | minimax
    "fishModel": "s2-pro",        # s1 | s2-pro
    "fishReferenceId": "2a9605eeafe84974b5b20628d42c0060",         # public female Fish voice
    "elevenModelId": "eleven_multilingual_v2",
    "elevenVoiceId": "JBFqnCBsd6RMkjVDRZzb",
    "piperVoiceId": "en_US-lessac-medium",
    "kokoroVoiceId": "af_bella",
    "minimaxSpeechModel": "speech-2.8-turbo",
    "minimaxVoiceId": "English_Graceful_Lady",
    "replyStyle": DEFAULT_REPLY_STYLE,  # short | normal | deep
    "fastAgentId": "voice-fast",  # voice-fast | voice-spark | voice-51mini
    "fastAgentModel": "",         # override model, e.g. minimax/MiniMax-M2.7
    "fastMode": False,
}

FREE_VOICE_CATALOG = [
    {
        "id": "fish-s1-default",
        "provider": "fish",
        "model": "s1",
        "voiceRef": "",
        "label": "Fish S1 (Default)",
        "tags": ["free-tier-api", "neutral", "assistant"],
        "stability": "volatile",
        "notes": "Free-tier API usage; subject to provider credit limits.",
        "tested": True,
    },
    {
        "id": "fish-s2pro",
        "provider": "fish",
        "model": "s2-pro",
        "voiceRef": "",
        "label": "Fish S2 Pro",
        "tags": ["free-tier-api", "expressive", "narration"],
        "stability": "volatile",
        "notes": "Higher quality; still API-credit dependent.",
        "tested": True,
    },
    {
        "id": "eleven-multilingual-default",
        "provider": "elevenlabs",
        "model": "eleven_multilingual_v2",
        "voiceRef": "JBFqnCBsd6RMkjVDRZzb",
        "label": "ElevenLabs Premade (George)",
        "tags": ["free-tier-api", "male", "narration"],
        "stability": "volatile",
        "notes": "Free-tier restrictions apply; may fail depending on account entitlement.",
        "tested": True,
    },
    {
        "id": "eleven-turbo-default",
        "provider": "elevenlabs",
        "model": "eleven_turbo_v2_5",
        "voiceRef": "JBFqnCBsd6RMkjVDRZzb",
        "label": "ElevenLabs Turbo (George)",
        "tags": ["free-tier-api", "male", "assistant", "fast"],
        "stability": "volatile",
        "notes": "Fast model option; account limits still apply.",
        "tested": True,
        "works": True,
    },
    {
        "id": "eleven-mono-default",
        "provider": "elevenlabs",
        "model": "eleven_monolingual_v1",
        "voiceRef": "JBFqnCBsd6RMkjVDRZzb",
        "label": "ElevenLabs Monolingual (George)",
        "tags": ["free-tier-api", "male", "narration"],
        "stability": "volatile",
        "notes": "Blocked on your free tier (deprecated/free-tier unavailable).",
        "tested": True,
        "works": False,
    },
    {
        "id": "fish-s1-comedy-template",
        "provider": "fish",
        "model": "s1",
        "voiceRef": "",
        "label": "Fish S1 Comedy Template",
        "tags": ["free-tier-api", "comedy", "energetic"],
        "stability": "volatile",
        "notes": "Use playful prompt style for comedic reads.",
        "tested": False,
    },
    {
        "id": "fish-s2-narration-template",
        "provider": "fish",
        "model": "s2-pro",
        "voiceRef": "",
        "label": "Fish S2 Narration Template",
        "tags": ["free-tier-api", "narration", "calm"],
        "stability": "volatile",
        "notes": "Good for calmer long-form narration style.",
        "tested": False,
    },
    {
        "id": "local-kokoro",
        "provider": "local-kokoro",
        "model": "kokoro-82m",
        "voiceRef": "",
        "label": "Kokoro-82M (Local/Open)",
        "tags": ["free-local", "male", "female", "neutral", "comedy"],
        "stability": "stable",
        "notes": "Open-source local ONNX model; wired and tested in this app.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-kokoro-bella",
        "provider": "local-kokoro",
        "model": "af_bella",
        "voiceRef": "",
        "label": "Kokoro Bella (Female)",
        "tags": ["free-local", "female", "assistant", "narration"],
        "stability": "stable",
        "notes": "Wired and tested locally.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-kokoro-adam",
        "provider": "local-kokoro",
        "model": "am_adam",
        "voiceRef": "",
        "label": "Kokoro Adam (Male)",
        "tags": ["free-local", "male", "assistant", "narration"],
        "stability": "stable",
        "notes": "Wired and tested locally.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-piper",
        "provider": "local-piper",
        "model": "piper",
        "voiceRef": "",
        "label": "Piper Voice Packs (Local/Open)",
        "tags": ["free-local", "male", "female", "narration"],
        "stability": "stable",
        "notes": "Open-source local voice packs; broad language/style options.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-piper-lessac",
        "provider": "local-piper",
        "model": "en_US-lessac-medium",
        "voiceRef": "",
        "label": "Piper Lessac (Female)",
        "tags": ["free-local", "female", "narration", "assistant"],
        "stability": "stable",
        "notes": "Wired and tested locally.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-piper-ryan",
        "provider": "local-piper",
        "model": "en_US-ryan-medium",
        "voiceRef": "",
        "label": "Piper Ryan (Male)",
        "tags": ["free-local", "male", "narration", "assistant"],
        "stability": "stable",
        "notes": "Wired and tested locally.",
        "tested": True,
        "works": True,
    },
    {
        "id": "local-xttsv2",
        "provider": "local",
        "model": "xtts-v2",
        "voiceRef": "",
        "label": "XTTS-v2 (Local/Open)",
        "tags": ["free-local", "male", "female", "multilingual", "cloning"],
        "stability": "stable",
        "notes": "Strong multilingual local option; not wired yet.",
        "tested": False,
    },
    {
        "id": "local-melotts",
        "provider": "local",
        "model": "melo-tts",
        "voiceRef": "",
        "label": "MeloTTS (Local/Open)",
        "tags": ["free-local", "male", "female", "multilingual"],
        "stability": "stable",
        "notes": "Lightweight multilingual local model; not wired yet.",
        "tested": False,
    },
    {
        "id": "local-styletts2",
        "provider": "local",
        "model": "styletts2",
        "voiceRef": "",
        "label": "StyleTTS2 (Local/Open)",
        "tags": ["free-local", "female", "male", "expressive"],
        "stability": "medium",
        "notes": "High quality expressive local model; more setup effort.",
        "tested": False,
    },
    {
        "id": "local-chattts",
        "provider": "local",
        "model": "chattts",
        "voiceRef": "",
        "label": "ChatTTS (Local/Open)",
        "tags": ["free-local", "assistant", "dialog"],
        "stability": "medium",
        "notes": "Good conversational style; quality varies by checkpoint.",
        "tested": False,
    },
    {
        "id": "local-fish-speech",
        "provider": "local",
        "model": "fish-speech",
        "voiceRef": "",
        "label": "Fish Speech (Local/Open)",
        "tags": ["free-local", "male", "female", "comedy", "expressive", "cloning"],
        "stability": "medium",
        "notes": "Powerful local expressive/cloning stack; heavier setup.",
        "tested": False,
    },
    {
        "id": "local-openvoice-v2",
        "provider": "local",
        "model": "openvoice-v2",
        "voiceRef": "",
        "label": "OpenVoice v2 (Local/Open)",
        "tags": ["free-local", "male", "female", "cloning"],
        "stability": "medium",
        "notes": "Voice style transfer/cloning workflow; not wired yet.",
        "tested": False,
    },
    {
        "id": "local-parler-tts",
        "provider": "local",
        "model": "parler-tts",
        "voiceRef": "",
        "label": "Parler-TTS (Local/Open)",
        "tags": ["free-local", "male", "female", "narration"],
        "stability": "medium",
        "notes": "Instruction-style local voice model; not wired yet.",
        "tested": False,
    },
]

# Mirror every completed voice turn to your Telegram DM.
MIRROR_CONFIG = {
    "enabled": True,
    "channel": "telegram",
    "target": "telegram:935214495",
    "mode": "full",  # concise | full
}

SUPPORTED_MODELS = {
    "fish": ["s1", "s2-pro"],
    "elevenlabs": [
        "eleven_multilingual_v2",
        "eleven_turbo_v2_5",
    ],
    "local-piper": [
        "en_US-lessac-medium",
        "en_US-ryan-medium",
    ],
    "local-kokoro": [
        "af_bella", 
        "am_adam",
    ],
    "minimax": [
        "speech-2.8-turbo",
        "speech-2.8-hd",
        "speech-2.6-turbo",
        "speech-2.6-hd",
    ],
}

MINIMAX_ENGLISH_VOICES = [
    "English_expressive_narrator",
    "English_radiant_girl",
    "English_magnetic_voiced_man",
    "English_compelling_lady1",
    "English_Aussie_Bloke",
    "English_captivating_female1",
    "English_Upbeat_Woman",
    "English_Trustworth_Man",
    "English_CalmWoman",
    "English_UpsetGirl",
    "English_Gentle-voiced_man",
    "English_Whispering_girl",
    "English_Diligent_Man",
    "English_Graceful_Lady",
    "English_ReservedYoungMan",
    "English_PlayfulGirl",
    "English_ManWithDeepVoice",
    "English_MaturePartner",
    "English_FriendlyPerson",
    "English_MatureBoss",
    "English_Debator",
    "English_LovelyGirl",
    "English_Steadymentor",
    "English_Deep-VoicedGentleman",
    "English_Wiselady",
    "English_CaptivatingStoryteller",
    "English_DecentYoungMan",
    "English_SentimentalLady",
    "English_ImposingManner",
    "English_SadTeen",
    "English_PassionateWarrior",
    "English_WiseScholar",
    "English_Soft-spokenGirl",
    "English_SereneWoman",
    "English_ConfidentWoman",
    "English_PatientMan",
    "English_Comedian",
    "English_BossyLeader",
    "English_Strong-WilledBoy",
    "English_StressedLady",
    "English_AssertiveQueen",
    "English_AnimeCharacter",
    "English_Jovialman",
    "English_WhimsicalGirl",
    "English_Kind-heartedGirl",
]

SUPPORTED_REPLY_STYLES = ["short", "normal", "deep"]

PIPER_BIN = Path('/home/guy/.openclaw/workspace/.venvs/piper/bin/piper')
PIPER_MODELS_DIR = Path('/home/guy/.openclaw/workspace/models/piper')
KOKORO_PYTHON = Path('/home/guy/.openclaw/workspace/.venvs/kokoro/bin/python')
KOKORO_TTS_SCRIPT = Path('/home/guy/.openclaw/workspace/scripts/media/kokoro_onnx_tts.py')
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "/home/guy/.npm-global/bin/openclaw")

VOICE_AGENT_ID = (os.environ.get("VOICE_AGENT_ID", "voice-fast") or "voice-fast").strip()
VOICE_AGENT_SESSION_ID = (os.environ.get("VOICE_AGENT_SESSION_ID", "voice-fast-local") or "voice-fast-local").strip()
VOICE_AGENT_THINKING = (os.environ.get("VOICE_AGENT_THINKING", "off") or "off").strip()
VOICE_AGENT_TIMEOUT = int(os.environ.get("VOICE_AGENT_TIMEOUT", "120"))
VOICE_REPLY_HINT_SHORT = os.environ.get(
    "VOICE_REPLY_HINT_SHORT",
    "Reply in one short spoken sentence, ideally under 12 words, unless details are explicitly requested.",
).strip()
VOICE_REPLY_HINT_NORMAL = os.environ.get(
    "VOICE_REPLY_HINT_NORMAL",
    "Reply in 2-4 sentences with practical detail. Keep it clear and spoken-friendly.",
).strip()
VOICE_REPLY_HINT_DEEP = os.environ.get(
    "VOICE_REPLY_HINT_DEEP",
    "Reply with a thorough, spoken-friendly explanation (about 5-8 sentences) unless brevity is requested.",
).strip()

VOICE_TRANSCRIBE_MODEL = os.environ.get("VOICE_TRANSCRIBE_MODEL", "base")
VOICE_TRANSCRIBE_LANGUAGE = os.environ.get("VOICE_TRANSCRIBE_LANGUAGE", "en")
VOICE_TRANSCRIBE_INPROCESS = os.environ.get("VOICE_TRANSCRIBE_INPROCESS", "1") != "0"

MIRROR_ASYNC = os.environ.get("VOICE_MIRROR_ASYNC", "1") != "0"

FAST_WEATHER_CACHE_TTL_SECONDS = int(os.environ.get("VOICE_FAST_WEATHER_CACHE_TTL_SECONDS", "300"))
_FAST_WEATHER_CACHE: dict[str, tuple[float, str]] = {}

_FW_MODEL = None
_FW_MODEL_LOCK = threading.Lock()

_CURRENT_TTS_PROC: subprocess.Popen | None = None
_CURRENT_TTS_PROC_LOCK = threading.Lock()
_CURRENT_AGENT_PROC: subprocess.Popen | None = None
_CURRENT_AGENT_PROC_LOCK = threading.Lock()
_PENDING_TURN_STATE: dict = {}
_PENDING_TURN_STATE_LOCK = threading.Lock()
_CANCEL_REQUESTED = False
_CANCEL_REQUESTED_LOCK = threading.Lock()

if VOICE_CONFIG.get("replyStyle") not in SUPPORTED_REPLY_STYLES:
    VOICE_CONFIG["replyStyle"] = "normal"


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def now_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_slug(text: str, max_len: int = 50) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug[:max_len].rstrip("-") or "reply")


def run_cmd(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def set_pending_turn_state(state: dict | None):
    with _PENDING_TURN_STATE_LOCK:
        _PENDING_TURN_STATE.clear()
        if state:
            _PENDING_TURN_STATE.update(state)


def get_pending_turn_state() -> dict:
    with _PENDING_TURN_STATE_LOCK:
        return dict(_PENDING_TURN_STATE)


def clear_pending_turn_state():
    set_pending_turn_state(None)


def request_cancel_turn(value: bool = True):
    global _CANCEL_REQUESTED
    with _CANCEL_REQUESTED_LOCK:
        _CANCEL_REQUESTED = value


def is_cancel_requested() -> bool:
    with _CANCEL_REQUESTED_LOCK:
        return _CANCEL_REQUESTED


def ensure_not_cancelled():
    if is_cancel_requested():
        raise RuntimeError("Voice turn cancelled")


def stop_current_turn() -> tuple[bool, str]:
    request_cancel_turn(True)
    stopped_any = False
    details: list[str] = []

    with _CURRENT_AGENT_PROC_LOCK:
        proc = _CURRENT_AGENT_PROC
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=2)
                stopped_any = True
                details.append("stopped agent process")
            except Exception:
                details.append("agent stop attempted")

    with _CURRENT_TTS_PROC_LOCK:
        proc = _CURRENT_TTS_PROC
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=2)
                stopped_any = True
                details.append("stopped TTS process")
            except Exception:
                details.append("TTS stop attempted")

    clear_pending_turn_state()

    if not details:
        details.append("No active turn process")
    return stopped_any, "; ".join(details) + "."


def load_openclaw_config() -> dict:
    p = Path("/home/guy/.openclaw/openclaw.json")
    return json.loads(p.read_text())


def get_eleven_key_from_openclaw() -> str:
    cfg = load_openclaw_config()
    key = (
        cfg.get("messages", {})
        .get("tts", {})
        .get("providers", {})
        .get("elevenlabs", {})
        .get("apiKey")
        or ""
    ).strip()
    if not key:
        raise RuntimeError("ElevenLabs apiKey not found in OpenClaw config")
    return key


def _extract_weather_location(user_text: str) -> str | None:
    lowered = user_text.lower()

    raw = ""
    if "weather in " in lowered:
        raw = user_text[lowered.index("weather in ") + len("weather in "):]
    elif "weather for " in lowered:
        raw = user_text[lowered.index("weather for ") + len("weather for "):]

    if not raw:
        return None

    raw = re.split(r"[?.!,;]", raw, maxsplit=1)[0]
    cleaned = re.sub(r"[^\w\s,.-]", "", raw).strip()
    if not cleaned:
        return None

    lowered_clean = cleaned.lower()
    if lowered_clean in {"here", "out there", "today", "tomorrow", "now"}:
        return None

    return cleaned


def _fetch_quick_weather_reply(location: str) -> str | None:
    key = location.strip().lower()
    if not key:
        return None

    now = time.time()
    cached = _FAST_WEATHER_CACHE.get(key)
    if cached and (now - cached[0]) < FAST_WEATHER_CACHE_TTL_SECONDS:
        return cached[1]

    url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
    req = urllib.request.Request(url, headers={"User-Agent": "voice-ui-fast-weather/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
        current = ((data or {}).get("current_condition") or [{}])[0]
        temp_f = (current.get("temp_F") or "?").strip() if isinstance(current, dict) else "?"
        desc = ""
        if isinstance(current, dict):
            wdesc = current.get("weatherDesc") or []
            if wdesc and isinstance(wdesc[0], dict):
                desc = (wdesc[0].get("value") or "").strip()

        if desc:
            reply = f"{location.title()}: {desc}, {temp_f}°F."
        else:
            reply = f"{location.title()}: {temp_f}°F."

        _FAST_WEATHER_CACHE[key] = (now, reply)
        return reply
    except Exception:
        return None


def is_realtime_query(user_text: str) -> bool:
    lowered = (user_text or "").strip().lower()
    realtime_terms = [
        "weather", "forecast", "temperature", "score", "game", "match",
        "price", "stock", "crypto", "bitcoin", "ethereum", "xrp", "solana",
        "latest", "current", "right now", "today", "news", "breaking",
    ]
    return any(term in lowered for term in realtime_terms)


def get_reply_style() -> str:
    if VOICE_CONFIG.get("fastMode") is True:
        return "short"
    style = str(VOICE_CONFIG.get("replyStyle") or DEFAULT_REPLY_STYLE or "normal").strip().lower()
    if style not in SUPPORTED_REPLY_STYLES:
        return "normal"
    return style


def get_reply_hint(style: str) -> str:
    normalized = (style or "normal").strip().lower()
    if normalized == "short":
        return VOICE_REPLY_HINT_SHORT
    if normalized == "deep":
        return VOICE_REPLY_HINT_DEEP
    return VOICE_REPLY_HINT_NORMAL


def _fetch_mlb_score(user_text: str) -> str | None:
    lowered = (user_text or "").strip().lower()
    team_map = {
        "yankees": 147,
        "yankee": 147,
        "mets": 121,
        "mets game": 121,
        "dodgers": 119,
        "dodger": 119,
        "red sox": 111,
        "braves": 144,
        "phillies": 143,
        "cubs": 112,
        "cubs game": 112,
    }
    team_name = None
    team_id = None
    for name, tid in team_map.items():
        if name in lowered:
            team_name = name
            team_id = tid
            break
    if not team_id:
        return None

    date_str = dt.datetime.now().strftime("%Y-%m-%d")
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&teamId={team_id}&hydrate=linescore,team"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "voice-ui-mlb/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        dates = (data or {}).get("dates") or []
        if not dates:
            return None
        games = dates[0].get("games") or []
        if not games:
            return None
        game = games[0]
        teams = ((game.get("teams") or {}))
        away = ((teams.get("away") or {}))
        home = ((teams.get("home") or {}))
        away_name = (((away.get("team") or {}).get("name")) or "Away")
        home_name = (((home.get("team") or {}).get("name")) or "Home")
        away_score = away.get("score")
        home_score = home.get("score")
        status = (((game.get("status") or {}).get("detailedState")) or "Unknown").strip()
        linescore = game.get("linescore") or {}
        inning = linescore.get("currentInning")
        inning_state = (linescore.get("inningState") or "").strip()

        if status.lower() in {"final", "game over"}:
            return f"{away_name} {away_score}, {home_name} {home_score}. Final."
        if "progress" in status.lower() or status.lower() in {"in progress", "manager challenge", "delayed start", "delayed"}:
            inning_text = f" {inning_state} {inning}" if inning else ""
            return f"{away_name} {away_score}, {home_name} {home_score}. {status}{inning_text}."
        if status.lower() in {"scheduled", "pre-game", "warmup"}:
            game_time = (((game.get("gameDate") or "").replace("T", " "))[:16]).strip()
            return f"{away_name} at {home_name}. {status}. {game_time}."
        return f"{away_name} {away_score}, {home_name} {home_score}. {status}."
    except Exception:
        return None


def _sports_score_reply(user_text: str) -> str | None:
    lowered = (user_text or "").strip().lower()
    if not any(term in lowered for term in ["score", "game", "playing"]):
        return None
    mlb = _fetch_mlb_score(user_text)
    if mlb:
        return mlb
    result = _web_search(user_text)
    if not result:
        return None
    return result.split(" || ")[0].strip()[:280]


def _finance_or_crypto_reply(user_text: str) -> str | None:
    lowered = (user_text or "").strip().lower()
    if any(term in lowered for term in ["bitcoin", "btc", "ethereum", "eth", "xrp", "ripple", "solana", "sol", "dogecoin", "doge", "cardano", "ada", "polkadot", "litecoin", "ltc"]):
        coin_info = _fetch_crypto_prices(lowered)
        if coin_info:
            return coin_info
    if any(term in lowered for term in ["stock", "shares", "market cap", "earnings", "price today"]):
        result = _web_search(user_text)
        if result:
            return result.split(" || ")[0].strip()[:320]
    return None


def _live_factual_reply(user_text: str) -> str | None:
    weather_location = _extract_weather_location(user_text)
    if weather_location:
        return _fetch_quick_weather_reply(weather_location)

    finance = _finance_or_crypto_reply(user_text)
    if finance:
        return finance

    sports = _sports_score_reply(user_text)
    if sports:
        return sports

    if is_realtime_query(user_text):
        result = _web_search(user_text)
        if result:
            return result.split(" || ")[0].strip()[:320]
    return None


def quick_intent_reply(user_text: str, *, style: str | None = None) -> str | None:
    text = (user_text or "").strip()
    if not text:
        return None

    lowered = text.lower()
    active_style = (style or get_reply_style()).strip().lower()
    if active_style not in SUPPORTED_REPLY_STYLES:
        active_style = "normal"

    if active_style == "short" and "weather" in lowered:
        location = _extract_weather_location(text)
        if location:
            quick_weather = _fetch_quick_weather_reply(location)
            if quick_weather:
                return quick_weather

    if active_style == "short":
        if re.search(r"\b(what('s| is) your name|who are you)\b", lowered):
            return "I'm Trinity."
        if re.search(r"\b(what('s| is) today'?s date|what date is it)\b", lowered) or lowered == "date":
            return f"Today is {dt.datetime.now().strftime('%A, %B %-d, %Y')}."
        if re.search(r"\b(are you there|you there)\b", lowered):
            return "Yep, I'm here."
        if re.search(r"\bwhat time is it\b", lowered) or lowered in {"time", "whats the time", "what's the time"}:
            now = dt.datetime.now().strftime('%-I:%M %p')
            return f"It's {now}."
        if "calendar" in lowered and any(word in lowered for word in ["add", "create", "schedule"]):
            return "I can do that. Tell me title, date, time, and duration."
        if "remind" in lowered or "reminder" in lowered:
            return "Sure. Tell me what to remember and when."
        if re.search(r"\bhow( are|'re)? you\b", lowered) or re.search(r"\bhow you doing\b", lowered):
            return "Doing pretty good. I'm here and ready - what do you need?"

    action_markers = (
        "add ", "create ", "schedule ", "calendar", "remind", "set ", "book ",
        "send ", "call ", "text ", "message ", "email ", "note ", "search ",
        "find ", "open ", "turn ", "play ", "stop ", "start ", "delete ",
        "make ", "faster", "fix ", "change ", "switch ", "update ", "improve ",
        "weather", "time", "lights", "light ",
    )
    if not any(marker in lowered for marker in action_markers):
        if re.search(r"\bhow are you\b", lowered) or re.search(r"\bwhat'?s up\b", lowered):
            if active_style == "short":
                options = [
                    "Doing good 👍",
                    "All good over here.",
                    "Running smooth ⚡",
                ]
            elif active_style == "deep":
                options = [
                    "Doing well-systems are stable and I'm fully online for whatever you want to tackle next.",
                    "All good here; I'm synced up and ready to go deeper on the next task.",
                ]
            else:
                options = [
                    "Doing well - I'm here and ready to help.",
                    "All good here. What are we getting into?",
                    "Running smooth and ready when you are.",
                ]
            return options[abs(hash(lowered)) % len(options)]

        words = re.findall(r"[a-z']+", lowered)
        if words and len(words) <= 4 and all(word in {"hello", "hi", "hey", "yo"} for word in words):
            if active_style == "short":
                return "Hey 👋"
            if active_style == "deep":
                return "Hey - I'm here and ready when you are."
            return "Hey - I'm here. What do you want to do?"

    if re.search(r"\bthank(s| you)?\b", lowered):
        if active_style == "short":
            return "Anytime 👍"
        return "Anytime-happy to help."

    if active_style == "short":
        if "weather" in lowered and any(word in lowered for word in ["here", "outside", "today", "now"]):
            return "Want the weather for New York, or another city?"
        if "make it faster" in lowered or "still sluggish" in lowered or "too slow" in lowered:
            return "Got it. I'm tuning the voice app to move faster."

    if lowered in {"time", "what time is it", "whats the time", "what's the time"}:
        now = dt.datetime.now().strftime('%-I:%M %p')
        if active_style == "short":
            return f"It is {now}."
        return f"It's {now} right now."

    return None


def _import_faster_whisper_whisper_model():
    try:
        from faster_whisper import WhisperModel  # type: ignore

        return WhisperModel
    except Exception:
        pass

    fw_lib = Path("/home/guy/.openclaw/workspace/.venvs/faster-whisper/lib")
    for site_pkg in sorted(fw_lib.glob("python*/site-packages")):
        p = str(site_pkg)
        if p not in sys.path:
            sys.path.insert(0, p)

    from faster_whisper import WhisperModel  # type: ignore

    return WhisperModel


def _get_faster_whisper_model():
    global _FW_MODEL
    if _FW_MODEL is not None:
        return _FW_MODEL

    with _FW_MODEL_LOCK:
        if _FW_MODEL is not None:
            return _FW_MODEL

        WhisperModel = _import_faster_whisper_whisper_model()
        _FW_MODEL = WhisperModel(VOICE_TRANSCRIBE_MODEL, device="cpu", compute_type="int8")
        return _FW_MODEL


def _transcribe_audio_inprocess(audio_path: Path) -> str:
    model = _get_faster_whisper_model()
    segments, _info = model.transcribe(
        str(audio_path),
        language=VOICE_TRANSCRIBE_LANGUAGE,
        vad_filter=True,
    )
    text = " ".join((getattr(seg, "text", "") or "").strip() for seg in segments).strip()
    if not text:
        raise RuntimeError("In-process transcription produced empty text")
    return text


def transcribe_audio(audio_path: Path) -> str:
    inproc_error: Exception | None = None

    if VOICE_TRANSCRIBE_INPROCESS:
        try:
            return _transcribe_audio_inprocess(audio_path)
        except Exception as e:
            inproc_error = e

    cmd = TRANSCRIBE_CMD + [str(audio_path), "--model", VOICE_TRANSCRIBE_MODEL]
    proc = run_cmd(cmd, timeout=180)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        if inproc_error:
            detail = f"inproc={inproc_error}; cli={detail}"
        raise RuntimeError(f"Transcription failed ({proc.returncode}): {detail}")

    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError("Transcription produced empty text")
    return text


def run_home_assistant_light(action: str, target: str, brightness: int | None = None) -> tuple[bool, str]:
    cmd = [
        "python3",
        "/home/guy/.openclaw/workspace/scripts/integrations/home_assistant.py",
        "light",
        action,
        target,
    ]
    if brightness is not None:
        cmd += ["--brightness", str(brightness)]
    proc = run_cmd(cmd, timeout=30)
    detail = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, detail


COLOR_NAME_MAP = {
    "red": {"xy_color": [0.675, 0.322]},
    "green": {"xy_color": [0.4091, 0.518]},
    "blue": {"xy_color": [0.167, 0.04]},
    "purple": {"xy_color": [0.272, 0.109]},
    "pink": {"xy_color": [0.382, 0.160]},
    "orange": {"xy_color": [0.611, 0.375]},
    "yellow": {"xy_color": [0.4432, 0.5154]},
    "white": {"color_temp_kelvin": 4000},
    "warm white": {"color_temp_kelvin": 2700},
    "cool white": {"color_temp_kelvin": 6500},
    "cyan": {"xy_color": [0.17, 0.34]},
    "teal": {"xy_color": [0.22, 0.33]},
    "magenta": {"xy_color": [0.378, 0.172]},
    "gold": {"xy_color": [0.51, 0.44]},
}

SCENE_NAME_MAP = {
    "movie mode": {"target": "living_room_lights", "brightness": 18, "color": "warm white", "reply": "Setting the living room to movie mode."},
    "relax": {"target": "living_room_lights", "brightness": 35, "color": "warm white", "reply": "Setting the living room to relax mode."},
    "neon": {"target": "living_room_lights", "brightness": 70, "color": "purple", "reply": "Setting the living room to neon mode."},
    "bright": {"target": "living_room_lights", "brightness": 100, "color": "cool white", "reply": "Turning the living room bright."},
}


def run_home_assistant_light_color(target: str, color_name: str, brightness: int | None = None) -> tuple[bool, str]:
    color_payload = COLOR_NAME_MAP.get(color_name.strip().lower())
    if not color_payload:
        return False, f"Unsupported color: {color_name}"

    actual_target = target
    if target == "living_room_lights":
        actual_target = ["light.hue_lamp_1", "light.hue_lamp_2", "light.hue_lamp_3"]
    elif target == "bedroom_lights":
        actual_target = ["light.hue_lightstrip_1"]

    targets = actual_target if isinstance(actual_target, list) else [actual_target]
    details = []
    ok_all = True

    for single_target in targets:
        payload = {
            "entity_id": single_target,
            **color_payload,
        }
        if brightness is not None:
            payload["brightness_pct"] = brightness

        cmd = [
            "python3",
            "/home/guy/.openclaw/workspace/scripts/integrations/home_assistant.py",
            "service",
            "light",
            "turn_on",
            "--data",
            json.dumps(payload),
            "--confirm-sensitive",
        ]
        proc = run_cmd(cmd, timeout=30)
        detail = (proc.stdout or proc.stderr or "").strip()
        details.append(f"{single_target}: {detail}")
        if proc.returncode != 0:
            ok_all = False

    return ok_all, " | ".join(details)


def extract_target_from_text(user_text: str) -> str:
    lowered = (user_text or "").strip().lower()
    if "bedroom" in lowered:
        return "bedroom_lights"
    return "living_room_lights"


def extract_brightness_from_text(user_text: str) -> int | None:
    lowered = (user_text or "").strip().lower()

    pct_match = re.search(r"(\d{1,3})\s*(?:percent|%)", lowered)
    if pct_match:
        value = max(0, min(100, int(pct_match.group(1))))
        return value

    brightness_words = {
        "dim": 20,
        "low": 25,
        "medium": 50,
        "half": 50,
        "bright": 100,
        "full": 100,
    }
    for word, value in brightness_words.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return value

    return None


def extract_color_command(user_text: str) -> tuple[str, str, int | None] | None:
    lowered = (user_text or "").strip().lower()
    if not lowered or "light" not in lowered:
        return None

    target = extract_target_from_text(lowered)
    brightness = extract_brightness_from_text(lowered)

    for color_name in sorted(COLOR_NAME_MAP.keys(), key=len, reverse=True):
        if color_name in lowered:
            return target, color_name, brightness

    return None


def extract_scene_command(user_text: str) -> tuple[str, dict] | None:
    lowered = (user_text or "").strip().lower()
    for scene_name, scene in SCENE_NAME_MAP.items():
        if scene_name in lowered:
            target = extract_target_from_text(lowered)
            scene_copy = dict(scene)
            scene_copy["target"] = target
            return scene_name, scene_copy
    return None


def parse_reminder_request(user_text: str) -> tuple[str, int] | None:
    lowered = (user_text or "").strip().lower()
    if "remind me" not in lowered:
        return None

    minute_match = re.search(r"remind me(?: to)?\s+(.+?)\s+in\s+(\d{1,3})\s+minutes?\b", lowered)
    if minute_match:
        return minute_match.group(1).strip(), int(minute_match.group(2))

    hour_match = re.search(r"remind me(?: to)?\s+(.+?)\s+in\s+(\d{1,2})\s+hours?\b", lowered)
    if hour_match:
        return hour_match.group(1).strip(), int(hour_match.group(2)) * 60

    return None


def apply_voice_followup_target(state: dict, target: str):
    state["lastLightTarget"] = target
    write_voice_state(state)


def resolve_followup_light_target(text: str, state: dict) -> str:
    lowered = (text or "").strip().lower()
    if any(word in lowered for word in ["them", "those", "that"]):
        remembered = state.get("lastLightTarget") if isinstance(state, dict) else None
        if isinstance(remembered, str) and remembered:
            return remembered
    return extract_target_from_text(text)


def quick_action_reply(user_text: str) -> str | None:
    text = (user_text or "").strip()
    if not text:
        return None

    lowered = text.lower()
    state = read_voice_state()

    reminder_cmd = parse_reminder_request(text)
    if reminder_cmd:
        reminder_text, minutes = reminder_cmd
        reminder = create_local_voice_reminder(reminder_text, minutes)
        if reminder:
            unit = "minute" if minutes == 1 else "minutes"
            return f"Okay - I'll remind you to {reminder_text} in {minutes} {unit}."
        return "I tried to set that reminder, but it didn't stick."

    scene_cmd = extract_scene_command(text)
    if scene_cmd and any(phrase in lowered for phrase in ["set", "change", "make", "turn"]):
        _scene_name, scene = scene_cmd
        ok, _detail = run_home_assistant_light_color(scene["target"], scene["color"], scene.get("brightness"))
        if ok:
            apply_voice_followup_target(state, scene["target"])
            return str(scene.get("reply") or "Scene applied.")
        return "I tried, but the scene didn't apply."

    color_cmd = extract_color_command(text)
    if color_cmd and any(phrase in lowered for phrase in ["set", "change", "make", "turn"]):
        target, color_name, brightness = color_cmd
        target = resolve_followup_light_target(text, state)
        ok, _detail = run_home_assistant_light_color(target, color_name, brightness)
        if ok:
            apply_voice_followup_target(state, target)
            room = "living room" if target == "living_room_lights" else "bedroom"
            if brightness is not None:
                return f"Setting the {room} lights to {color_name} at {brightness} percent."
            return f"Turning the {room} lights {color_name} now."
        return f"I tried, but I couldn't set the {color_name} color."

    if lowered in {"yes", "yeah", "yep", "do it", "go ahead", "turn them on now"}:
        pending = state.get("pendingAction") if isinstance(state, dict) else None
        if isinstance(pending, dict) and pending.get("kind") == "light_on":
            target = pending.get("target") or "living_room_lights"
            brightness = pending.get("brightness") if isinstance(pending.get("brightness"), int) else None
            ok, _detail = run_home_assistant_light("on", target, brightness)
            state.pop("pendingAction", None)
            write_voice_state(state)
            if ok:
                apply_voice_followup_target(state, target)
                return "Turning them on now."
            return "I tried, but the lights didn't respond."

    if (("turn on" in lowered or "lights on" in lowered or "set" in lowered or "make" in lowered) and "light" in lowered):
        target = resolve_followup_light_target(text, state)
        brightness = extract_brightness_from_text(lowered)
        if any(word in lowered for word in ["now", "please", "right now", "set", "make"]):
            ok, _detail = run_home_assistant_light("on", target, brightness)
            room = "living room" if target == "living_room_lights" else "bedroom"
            if ok:
                apply_voice_followup_target(state, target)
                if brightness is not None:
                    return f"Setting the {room} lights to {brightness} percent now."
                return "Turning on the living room lights now." if target == "living_room_lights" else "Turning on the bedroom lights now."
            return "I tried, but the lights didn't turn on."
        state["pendingAction"] = {"kind": "light_on", "target": target, "brightness": brightness, "createdAt": now_iso()}
        write_voice_state(state)
        return "I can do that-want me to turn them on now?"

    if ("turn off" in lowered or "lights off" in lowered) and "light" in lowered:
        target = resolve_followup_light_target(text, state)
        ok, _detail = run_home_assistant_light("off", target)
        if ok:
            apply_voice_followup_target(state, target)
            return "Turning off the living room lights now." if target == "living_room_lights" else "Turning off the bedroom lights now."
        return "I tried, but the lights didn't turn off."

    if re.search(r"\b(calendar|schedule|add event)\b", lowered) and any(word in lowered for word in ["add", "create", "schedule"]):
        return "I can add that. Tell me the title, date, start time, and how long it should last."

    if "weather" in lowered and any(word in lowered for word in ["here", "outside", "today", "now"]):
        return "I can do that - say a city, or ask for weather here and I'll give you the current read plus the high and low."

    return None


def _get_minimax_api_key() -> str | None:
    """Extract minimax API key from agent models.json."""
    try:
        for agent_dir in ["voice-fast", "voice-spark", "voice-51mini"]:
            models_path = Path(f"~/.openclaw/agents/{agent_dir}/agent/models.json").expanduser()
            if models_path.exists():
                txt = models_path.read_text()
                m = re.search(r'"minimax"[\s\S]{0,2000}?"apiKey":\s*"([^"]+)"', txt)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _get_tavily_api_key() -> str | None:
    try:
        cfg = load_openclaw_config()
        key = (
            (((cfg.get("plugins") or {}).get("entries") or {}).get("tavily") or {})
            .get("config", {})
            .get("webSearch", {})
            .get("apiKey")
            or ""
        ).strip()
        return key or None
    except Exception:
        return None

def _call_minimax_direct(message: str, model: str, api_key: str) -> str:
    """Call minimax API directly, bypassing openclaw agent system."""
    # Inject live web context when needed
    message = _inject_live_context(message)
    body = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": message}],
    }
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    content = data.get("content", [])
    if content and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block["text"]
        # Fall back to thinking block if no text found
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thinking = block.get("thinking", "")
                if thinking:
                    return f"[reasoning] {thinking[:300]}"
    raise RuntimeError(f"No text in minimax response: {data}")


def _inject_live_context(message: str) -> str:
    """Prepend live web data for queries that need real-time info."""
    lowered = message.lower()
    ctx_parts = []

    # Crypto prices via CoinGecko
    crypto_terms = ["bitcoin", "btc", "ethereum", "eth", "xrp", "ripple", "solana", "sol", "dogecoin", "doge", "cardano", "ada", "polkadot", "litecoin", "ltc"]
    price_terms = ["price", "cost", "worth", "trading at", "trading around", "valued at"]
    if any(c in lowered for c in crypto_terms) and any(p in lowered for p in price_terms):
        coin_info = _fetch_crypto_prices(lowered)
        if coin_info:
            ctx_parts.append(f"[Live crypto data] {coin_info}")

    # General web search for other real-time queries
    rt_patterns = ["score", "game", "weather", "temperature", "forecast", "stock", "price today", "latest news", "current "]
    if any(p in lowered for p in rt_patterns) and not ctx_parts:
        if VOICE_CONFIG.get("fastMode") is True and not any(p in lowered for p in ["score", "game", "stock", "latest news"]):
            search_result = None
        else:
            search_result = _web_search(message)
        if search_result:
            ctx_parts.append(f"[Web search] {search_result}")


    if ctx_parts:
        return ' '.join(ctx_parts) + '\n\n' + message
    return message


def _fetch_crypto_prices(query: str) -> str | None:
    """Fetch crypto prices from CoinGecko (free, no API key)."""
    # Map query terms to CoinGecko IDs
    id_map = {
        "bitcoin": "bitcoin", "btc": "bitcoin",
        "ethereum": "ethereum", "eth": "ethereum",
        "xrp": "ripple", "ripple": "ripple",
        "solana": "solana", "sol ": "solana",
        "dogecoin": "dogecoin", "doge": "dogecoin",
        "cardano": "cardano", "ada": "cardano",
        "polkadot": "polkadot", "litecoin": "litecoin", "ltc": "litecoin",
    }
    coin_ids = []
    for term, coin_id in id_map.items():
        if term in query:
            coin_ids.append(coin_id)
    coin_ids = list(dict.fromkeys(coin_ids))  # deduplicate, preserve order
    if not coin_ids:
        return None
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(coin_ids)}&vs_currencies=usd&include_24hr_change=true"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        parts = []
        for coin_id in coin_ids:
            if coin_id in data:
                price = data[coin_id].get("usd", "N/A")
                change = data[coin_id].get("usd_24h_change", 0)
                sign = "+" if change >= 0 else ""
                parts.append(f"{coin_id.title()}: ${price:,.4f} ({sign}{change:.2f}% 24h)")
        return ", ".join(parts) if parts else None
    except Exception:
        return None


def _web_search(query: str) -> str | None:
    """Use Tavily for live web search, with DuckDuckGo fallback."""
    tavily_key = _get_tavily_api_key()
    lowered = query.lower()

    if tavily_key:
        try:
            topic = "general"
            if any(term in lowered for term in ["score", "game", "match", "latest news", "breaking", "headline"]):
                topic = "news"
            elif any(term in lowered for term in ["stock", "shares", "market cap", "earnings"]):
                topic = "finance"

            payload = {
                "query": query[:400],
                "search_depth": "advanced",
                "topic": topic,
                "max_results": 5,
                "include_answer": True,
            }
            if topic == "news":
                payload["time_range"] = "day"

            req = urllib.request.Request(
                "https://api.tavily.com/search",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {tavily_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())

            answer = (data.get("answer") or "").strip() if isinstance(data, dict) else ""
            results = data.get("results") or [] if isinstance(data, dict) else []
            snippets = []
            for item in results[:3]:
                if not isinstance(item, dict):
                    continue
                title = (item.get("title") or "").strip()
                content = (item.get("content") or "").strip()
                if title and content:
                    snippets.append(f"{title}: {content}")
                elif content:
                    snippets.append(content)

            parts = []
            if answer:
                parts.append(answer)
            if snippets:
                parts.append(" | ".join(snippets))
            joined = " || ".join(parts).strip()
            if joined:
                return joined[:1200]
        except Exception:
            pass

    try:
        url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_redirect=1"
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        ans = data.get("Answer", "")
        if ans:
            return ans
        topics = data.get("RelatedTopics", [])
        for t in topics:
            if t.get("Text"):
                return t["Text"][:300]
        return None
    except Exception:
        return None

def get_agent_reply(user_text: str) -> str:
    ensure_not_cancelled()
    message = user_text.strip()
    if not message:
        return "Could you repeat that?"

    lowered = message.lower()
    if any(phrase in lowered for phrase in ["make it faster", "still sluggish", "too slow", "speed it up", "slower than it should"]):
        return "Got it. I'm tuning the voice app to move faster."

    action = quick_action_reply(message)
    if action:
        return action

    live = _live_factual_reply(message)
    if live:
        return live

    style = get_reply_style()
    quick = quick_intent_reply(message, style=style)
    if quick:
        return quick

    reply_hint = get_reply_hint(style)
    if reply_hint:
        formatter = "Answer first. Be direct, specific, and spoken-friendly. For ordinary questions, keep it tight. For factual questions, lead with the fact, then 1-2 useful details. Avoid apologies unless something actually failed."
        message = f"{message}\n\n{reply_hint}\n\n{formatter}"

    # Direct minimax API call when minimax model is selected
    fast_model = (VOICE_CONFIG.get("fastAgentModel") or "").strip()
    if fast_model.startswith("minimax/"):
        model_name = fast_model.split("/")[1]
        minimax_key = _get_minimax_api_key()
        if minimax_key:
            ensure_not_cancelled()
            return _call_minimax_direct(message, model_name, minimax_key)

    # Fall back to openclaw agent command
    cmd = [
        OPENCLAW_BIN,
        "agent",
        "--json",
    ]
    agent_id = VOICE_CONFIG.get("fastAgentId", VOICE_AGENT_ID or "voice-fast")
    cmd += ["--agent", agent_id]
    cmd += [
        "--session-id",
        VOICE_AGENT_SESSION_ID,
        "--message",
        message,
    ]
    if VOICE_AGENT_THINKING:
        cmd += ["--thinking", VOICE_AGENT_THINKING]

    ensure_not_cancelled()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with _CURRENT_AGENT_PROC_LOCK:
        global _CURRENT_AGENT_PROC
        _CURRENT_AGENT_PROC = proc
    try:
        stdout, stderr = proc.communicate(timeout=VOICE_AGENT_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError("Agent call timed out")
    finally:
        with _CURRENT_AGENT_PROC_LOCK:
            if _CURRENT_AGENT_PROC is proc:
                _CURRENT_AGENT_PROC = None

    ensure_not_cancelled()
    if proc.returncode != 0:
        raise RuntimeError(f"Agent call failed ({proc.returncode}): {stderr.strip() or stdout.strip()}")

    raw = (stdout or "").strip()
    try:
        data = json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"Agent JSON parse failed: {e}; raw={raw[:400]}") from e

    payloads = (((data or {}).get("result") or {}).get("payloads") or [])
    if payloads and isinstance(payloads[0], dict):
        text = (payloads[0].get("text") or "").strip()
        if text:
            lowered = text.lower()
            if "gpt-5.3-codex-spark" in lowered and "not supported" in lowered:
                return "The old Spark voice agent path was unsupported. I switched this app back to the default model path."
            if "invalidated oauth token" in lowered or "usage limit" in lowered:
                return "The voice app is back on the default path, but the main OpenClaw login needs to be refreshed before I can answer normally."
            return text

    text = (data.get("text") or "").strip() if isinstance(data, dict) else ""
    if text:
        lowered = text.lower()
        if "gpt-5.3-codex-spark" in lowered and "not supported" in lowered:
            return "The old Spark voice agent path was unsupported. I switched this app back to the default model path."
        if "invalidated oauth token" in lowered or "usage limit" in lowered:
            return "The voice app is back on the default path, but the main OpenClaw login needs to be refreshed before I can answer normally."
        return text

    raise RuntimeError("Agent response did not include text payload")


def synthesize_fish_tts(text: str) -> Path:
    out_path = OUT_ROOT / f"voice-ui-{now_stamp()}-{safe_slug(text)}.mp3"
    model = (VOICE_CONFIG.get("fishModel") or "s1").strip() or "s1"
    ref_id = (VOICE_CONFIG.get("fishReferenceId") or "").strip()

    cmd = FISH_TTS_CMD + [text, str(out_path), model]
    if ref_id:
        cmd += ["--reference-id", ref_id]

    ensure_not_cancelled()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    with _CURRENT_TTS_PROC_LOCK:
        global _CURRENT_TTS_PROC
        _CURRENT_TTS_PROC = proc
    try:
        stdout, stderr = proc.communicate(timeout=240)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise RuntimeError("Fish TTS timed out")
    finally:
        with _CURRENT_TTS_PROC_LOCK:
            if _CURRENT_TTS_PROC is proc:
                _CURRENT_TTS_PROC = None

    if proc.returncode != 0:
        raise RuntimeError(f"Fish TTS failed ({proc.returncode}): {stderr.strip() or stdout.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Fish TTS did not produce an output file")
    return out_path


def synthesize_elevenlabs_tts(text: str) -> Path:
    api_key = get_eleven_key_from_openclaw()
    voice_id = (VOICE_CONFIG.get("elevenVoiceId") or "JBFqnCBsd6RMkjVDRZzb").strip()
    model_id = (VOICE_CONFIG.get("elevenModelId") or "eleven_multilingual_v2").strip()

    url = f"{ELEVEN_API_BASE}/v1/text-to-speech/{voice_id}"
    payload = {
        "text": text,
        "model_id": model_id,
        "output_format": "mp3_44100_128",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0,
            "use_speaker_boost": False,
        },
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"ElevenLabs API error ({e.code}): {body}") from e
    except Exception as e:
        raise RuntimeError(f"ElevenLabs request failed: {e}") from e

    out_path = OUT_ROOT / f"voice-ui-{now_stamp()}-{safe_slug(text)}.mp3"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(audio)
    return out_path


def synthesize_piper_tts(text: str) -> Path:
    voice_id = (VOICE_CONFIG.get("piperVoiceId") or "en_US-lessac-medium").strip()
    model = PIPER_MODELS_DIR / f"{voice_id}.onnx"
    config = PIPER_MODELS_DIR / f"{voice_id}.onnx.json"

    if not PIPER_BIN.exists():
        raise RuntimeError(f"Piper binary not found: {PIPER_BIN}")
    if not model.exists() or not config.exists():
        raise RuntimeError(f"Piper voice files missing for {voice_id}")

    out_path = OUT_ROOT / f"voice-ui-{now_stamp()}-{safe_slug(text)}.wav"
    cmd = [str(PIPER_BIN), "-m", str(model), "-c", str(config), "-f", str(out_path)]

    proc = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"Piper TTS failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Piper did not produce an output file")
    return out_path


def synthesize_kokoro_tts(text: str) -> Path:
    voice_id = (VOICE_CONFIG.get("kokoroVoiceId") or "af_bella").strip()
    if voice_id not in SUPPORTED_MODELS["local-kokoro"]:
        raise RuntimeError(f"Unsupported kokoroVoiceId: {voice_id}")

    if not KOKORO_PYTHON.exists():
        raise RuntimeError(f"Kokoro python runtime not found: {KOKORO_PYTHON}")
    if not KOKORO_TTS_SCRIPT.exists():
        raise RuntimeError(f"Kokoro script not found: {KOKORO_TTS_SCRIPT}")

    out_path = OUT_ROOT / f"voice-ui-{now_stamp()}-{safe_slug(text)}.wav"
    cmd = [
        str(KOKORO_PYTHON),
        str(KOKORO_TTS_SCRIPT),
        "--text",
        text,
        "--out",
        str(out_path),
        "--voice",
        voice_id,
    ]
    proc = run_cmd(cmd, timeout=240)
    if proc.returncode != 0:
        raise RuntimeError(f"Kokoro TTS failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError("Kokoro TTS did not produce an output file")
    return out_path


def synthesize_minimax_tts(text: str) -> Path:
    api_key = _get_minimax_api_key()
    if not api_key:
        raise RuntimeError("MiniMax API key not found")

    model = (VOICE_CONFIG.get("minimaxSpeechModel") or "speech-2.8-turbo").strip()
    voice_id = (VOICE_CONFIG.get("minimaxVoiceId") or "English_Graceful_Lady").strip()
    if model not in SUPPORTED_MODELS["minimax"]:
        raise RuntimeError(f"Unsupported minimaxSpeechModel: {model}")
    if voice_id not in MINIMAX_ENGLISH_VOICES:
        raise RuntimeError(f"Unsupported minimaxVoiceId: {voice_id}")

    payload = {
        "model": model,
        "text": text,
        "stream": False,
        "output_format": "hex",
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1,
            "vol": 1,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 32000,
            "bitrate": 128000,
            "format": "mp3",
            "channel": 1,
        },
    }
    req = urllib.request.Request(
        "https://api.minimax.io/v1/t2a_v2",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"MiniMax TTS API error ({e.code}): {body}") from e
    except Exception as e:
        raise RuntimeError(f"MiniMax TTS request failed: {e}") from e

    audio_hex = ((data or {}).get("data") or {}).get("audio") or ""
    if not audio_hex:
        raise RuntimeError(f"MiniMax TTS response missing audio: {str(data)[:500]}")

    out_path = OUT_ROOT / f"voice-ui-{now_stamp()}-{safe_slug(text)}.mp3"
    out_path.write_bytes(bytes.fromhex(audio_hex))
    return out_path


def synthesize_tts(text: str, provider_override: str | None = None) -> Path:
    provider = (provider_override or VOICE_CONFIG.get("provider") or "fish").strip().lower()
    if provider == "elevenlabs":
        return synthesize_elevenlabs_tts(text)
    if provider == "local-piper":
        return synthesize_piper_tts(text)
    if provider == "local-kokoro":
        return synthesize_kokoro_tts(text)
    if provider == "minimax":
        return synthesize_minimax_tts(text)
    return synthesize_fish_tts(text)


def _friendly_runtime_error_message(error: Exception) -> str:
    msg = str(error).strip()
    lowered = msg.lower()

    if "quota_exceeded" in lowered or "credits remaining" in lowered:
        return "Default speech is selected, but ElevenLabs is out of credits right now."
    if "invalidated oauth token" in lowered:
        return "The main OpenClaw login needs to be refreshed before I can answer normally."
    if "usage limit" in lowered:
        return "The current OpenClaw model account hit its usage limit, so voice replies are temporarily unavailable."

    return msg or "Something went wrong."


def get_voice_model_and_ref(provider: str) -> tuple[str, str]:
    provider = (provider or "fish").strip().lower()
    if provider == "elevenlabs":
        return VOICE_CONFIG.get("elevenModelId", ""), VOICE_CONFIG.get("elevenVoiceId", "")
    if provider == "local-piper":
        voice = VOICE_CONFIG.get("piperVoiceId", "")
        return voice, voice
    if provider == "local-kokoro":
        voice = VOICE_CONFIG.get("kokoroVoiceId", "")
        return voice, voice
    if provider == "minimax":
        return VOICE_CONFIG.get("minimaxSpeechModel", ""), VOICE_CONFIG.get("minimaxVoiceId", "")
    return VOICE_CONFIG.get("fishModel", ""), VOICE_CONFIG.get("fishReferenceId", "")


def parse_multipart_audio(handler: BaseHTTPRequestHandler) -> tuple[bytes, str]:
    ctype, pdict = cgi.parse_header(handler.headers.get("Content-Type", ""))
    if ctype != "multipart/form-data":
        raise ValueError("Expected multipart/form-data")

    pdict["boundary"] = pdict["boundary"].encode("utf-8")
    content_length = int(handler.headers.get("Content-Length", "0"))

    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
        "CONTENT_LENGTH": str(content_length),
    }

    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ=environ,
        keep_blank_values=True,
    )

    field = form["audio"] if "audio" in form else None
    if field is None or not getattr(field, "file", None):
        raise ValueError("Missing multipart field: audio")

    data = field.file.read()
    filename = (getattr(field, "filename", None) or "audio.webm").strip() or "audio.webm"
    return data, filename


def parse_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data
    except Exception as e:
        raise ValueError(f"Invalid JSON body: {e}") from e


def append_history(entry: dict):
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_voice_state() -> dict:
    if not VOICE_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(VOICE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_voice_state(state: dict):
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    VOICE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def read_history(limit: int = 100) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def read_reminders() -> list[dict]:
    if not REMINDERS_FILE.exists():
        return []
    try:
        data = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def write_reminders(reminders: list[dict]):
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    REMINDERS_FILE.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")


def create_local_voice_reminder(reminder_text: str, minutes: int) -> dict:
    reminders = read_reminders()
    reminder_id = f"voice-local-{safe_slug(reminder_text, 24)}-{int(time.time())}"
    due_at = (dt.datetime.now() + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
    entry = {
        "id": reminder_id,
        "text": reminder_text,
        "minutes": minutes,
        "dueAt": due_at,
        "spoken": False,
        "createdAt": now_iso(),
        "snoozeCount": 0,
    }
    reminders.append(entry)
    write_reminders(reminders)
    return entry


def get_due_local_reminders() -> list[dict]:
    reminders = read_reminders()
    now = dt.datetime.now()
    due = []
    changed = False

    for item in reminders:
        if not isinstance(item, dict):
            continue
        if item.get("spoken") is True or item.get("dismissed") is True:
            continue
        due_at_raw = str(item.get("dueAt") or "").strip()
        if not due_at_raw:
            continue
        try:
            due_at = dt.datetime.fromisoformat(due_at_raw)
        except Exception:
            continue
        if due_at <= now:
            reminder_text = str(item.get("text") or "something").strip() or "something"
            minutes = int(item.get("minutes") or 0)
            spoken_text = f"Reminder: {reminder_text}. This is your reminder from {minutes} minute{'s' if minutes != 1 else ''} ago."
            try:
                audio_path = synthesize_tts(spoken_text)
                item["spoken"] = True
                item["spokenAt"] = now_iso()
                item["audioPath"] = str(audio_path)
                item["spokenText"] = spoken_text
                due.append(item)
                changed = True
            except Exception:
                continue

    if changed:
        write_reminders(reminders)

    return due


def list_local_reminders() -> dict:
    reminders = [item for item in read_reminders() if isinstance(item, dict)]
    upcoming = []
    history = []
    now = dt.datetime.now()

    for item in reminders:
        due_at_raw = str(item.get("dueAt") or "").strip()
        try:
            due_at = dt.datetime.fromisoformat(due_at_raw) if due_at_raw else None
        except Exception:
            due_at = None

        normalized = dict(item)
        normalized["isDue"] = bool(due_at and due_at <= now)

        if item.get("spoken") or item.get("dismissed"):
            history.append(normalized)
        else:
            upcoming.append(normalized)

    upcoming.sort(key=lambda x: str(x.get("dueAt") or ""))
    history.sort(key=lambda x: str(x.get("spokenAt") or x.get("createdAt") or ""), reverse=True)
    return {"upcoming": upcoming, "history": history[:30]}


def dismiss_local_reminder(reminder_id: str) -> bool:
    reminders = read_reminders()
    changed = False
    for item in reminders:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == reminder_id:
            item["dismissed"] = True
            item["dismissedAt"] = now_iso()
            changed = True
            break
    if changed:
        write_reminders(reminders)
    return changed


def snooze_local_reminder(reminder_id: str, minutes: int = 5) -> bool:
    reminders = read_reminders()
    changed = False
    for item in reminders:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == reminder_id:
            item["spoken"] = False
            item["dismissed"] = False
            item["audioPath"] = None
            item["spokenText"] = None
            item["spokenAt"] = None
            item["dueAt"] = (dt.datetime.now() + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
            item["snoozeCount"] = int(item.get("snoozeCount") or 0) + 1
            item["lastSnoozedAt"] = now_iso()
            changed = True
            break
    if changed:
        write_reminders(reminders)
    return changed


def mirror_to_telegram(entry: dict):
    if not MIRROR_CONFIG.get("enabled"):
        return

    mode = MIRROR_CONFIG.get("mode", "full")
    if mode == "concise":
        msg = (
            f"🎙️ Voice UI Turn\n"
            f"Provider: {entry.get('voiceProvider')}\n"
            f"Transcript: {entry.get('transcript','')[:180]}\n"
            f"Reply: {entry.get('reply','')[:180]}"
        )
    else:
        msg = (
            f"🎙️ Voice UI Turn\n"
            f"Time: {entry.get('timestamp')}\n"
            f"Provider: {entry.get('voiceProvider')}\n"
            f"Model: {entry.get('voiceModel')}\n\n"
            f"Transcript:\n{entry.get('transcript','')}\n\n"
            f"Reply:\n{entry.get('reply','')}"
        )

    cmd = [
        OPENCLAW_BIN,
        "message",
        "send",
        "--channel",
        MIRROR_CONFIG.get("channel", "telegram"),
        "--target",
        MIRROR_CONFIG.get("target", "telegram:935214495"),
        "--message",
        msg,
    ]

    # Best-effort; don't fail voice turn if mirror fails.
    try:
        if MIRROR_ASYNC:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        else:
            run_cmd(cmd, timeout=30)
    except Exception:
        return


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send_json(self, data, status=200):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not Found")
            return
        if path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        else:
            ctype = "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, e: Exception, status=500):
        self._send_json(
            {
                "ok": False,
                "error": str(e),
                "trace": traceback.format_exc(limit=2),
            },
            status=status,
        )

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path

        if route == "/" or route.startswith("/index.html"):
            return self._serve_file(WEB_ROOT / "index.html")
        if route.startswith("/app.js"):
            return self._serve_file(WEB_ROOT / "app.js")
        if route.startswith("/assets/"):
            rel = route.removeprefix("/assets/")
            candidate = (WEB_ROOT / "assets" / rel).resolve()
            try:
                candidate.relative_to((WEB_ROOT / "assets").resolve())
            except Exception:
                return self._send_json({"ok": False, "error": "Invalid asset path"}, status=400)
            return self._serve_file(candidate)
        if route.startswith("/health"):
            return self._send_json({"ok": True, "service": "voice-ui", "stage": "history+mirror+voice-catalog"})
        if route.startswith("/api/voice-config"):
            return self._send_json(
                {
                    "ok": True,
                    **VOICE_CONFIG,
                    "supportedModels": SUPPORTED_MODELS,
                    "supportedReplyStyles": SUPPORTED_REPLY_STYLES,
                    "minimaxEnglishVoices": MINIMAX_ENGLISH_VOICES,
                }
            )
        if route.startswith("/api/history"):
            return self._send_json({"ok": True, "history": read_history(limit=200)})
        if route.startswith("/api/free-voice-catalog"):
            return self._send_json({"ok": True, "catalog": FREE_VOICE_CATALOG})
        if route.startswith("/api/stop/status"):
            pending = get_pending_turn_state()
            return self._send_json({
                "ok": True,
                "ttsActive": bool(_CURRENT_TTS_PROC is not None),
                "agentActive": bool(_CURRENT_AGENT_PROC is not None),
                "cancelRequested": is_cancel_requested(),
                "pending": pending,
            })
        if route.startswith("/api/reminders") and route.endswith("/list"):
            return self._send_json({"ok": True, **list_local_reminders()})
        if route.startswith("/api/reminders/due"):
            due = get_due_local_reminders()
            return self._send_json({"ok": True, "reminders": due})
        if route.startswith("/api/audio"):
            qs = urllib.parse.parse_qs(parsed.query)
            req_path = (qs.get("path") or [""])[0]
            if not req_path:
                return self._send_json({"ok": False, "error": "Missing path query parameter"}, status=400)

            candidate = Path(req_path).expanduser().resolve()
            try:
                out_root = OUT_ROOT.resolve()
                candidate.relative_to(out_root)
            except Exception:
                return self._send_json({"ok": False, "error": "Audio path must be under OUT_ROOT"}, status=400)

            if not candidate.exists() or not candidate.is_file():
                return self._send_json({"ok": False, "error": "Audio file not found"}, status=404)

            if candidate.suffix.lower() == ".wav":
                ctype = "audio/wav"
            else:
                ctype = "audio/mpeg"

            data = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if (qs.get("download") or [""])[0] in {"1", "true", "yes"}:
                self.send_header("Content-Disposition", f"attachment; filename=\"{candidate.name}\"")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(404, "Not Found")

    def do_POST(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)

        try:
            if self.path == "/api/voice-config":
                body = parse_json_body(self)
                provider = str(body.get("provider", VOICE_CONFIG["provider"])).strip().lower() or "fish"
                if provider not in {"fish", "elevenlabs", "local-piper", "local-kokoro", "minimax"}:
                    return self._send_json({"ok": False, "error": "provider must be fish, elevenlabs, local-piper, local-kokoro, or minimax"}, status=400)

                fish_model = str(body.get("fishModel", VOICE_CONFIG["fishModel"])).strip() or "s1"
                if fish_model not in SUPPORTED_MODELS["fish"]:
                    return self._send_json({"ok": False, "error": f"fishModel must be one of {SUPPORTED_MODELS['fish']}"}, status=400)

                eleven_model = str(body.get("elevenModelId", VOICE_CONFIG["elevenModelId"])).strip() or "eleven_multilingual_v2"
                if eleven_model not in SUPPORTED_MODELS["elevenlabs"]:
                    return self._send_json({"ok": False, "error": f"elevenModelId must be one of {SUPPORTED_MODELS['elevenlabs']}"}, status=400)

                reply_style = str(body.get("replyStyle", VOICE_CONFIG.get("replyStyle", "normal"))).strip().lower() or "normal"
                if reply_style not in SUPPORTED_REPLY_STYLES:
                    return self._send_json({"ok": False, "error": f"replyStyle must be one of {SUPPORTED_REPLY_STYLES}"}, status=400)

                VOICE_CONFIG["provider"] = provider
                piper_voice = str(body.get("piperVoiceId", VOICE_CONFIG["piperVoiceId"])).strip() or "en_US-lessac-medium"
                if piper_voice not in SUPPORTED_MODELS["local-piper"]:
                    return self._send_json({"ok": False, "error": f"piperVoiceId must be one of {SUPPORTED_MODELS['local-piper']}"}, status=400)

                kokoro_voice = str(body.get("kokoroVoiceId", VOICE_CONFIG["kokoroVoiceId"])).strip() or "af_bella"
                if kokoro_voice not in SUPPORTED_MODELS["local-kokoro"]:
                    return self._send_json({"ok": False, "error": f"kokoroVoiceId must be one of {SUPPORTED_MODELS['local-kokoro']}"}, status=400)

                VOICE_CONFIG["fishModel"] = fish_model
                VOICE_CONFIG["fishReferenceId"] = str(body.get("fishReferenceId", VOICE_CONFIG["fishReferenceId"])).strip()
                VOICE_CONFIG["elevenModelId"] = eleven_model
                VOICE_CONFIG["elevenVoiceId"] = str(body.get("elevenVoiceId", VOICE_CONFIG["elevenVoiceId"])).strip() or VOICE_CONFIG["elevenVoiceId"]
                VOICE_CONFIG["piperVoiceId"] = piper_voice
                VOICE_CONFIG["kokoroVoiceId"] = kokoro_voice
                minimax_speech_model = str(body.get("minimaxSpeechModel", VOICE_CONFIG.get("minimaxSpeechModel", "speech-2.8-turbo"))).strip() or "speech-2.8-turbo"
                if minimax_speech_model not in SUPPORTED_MODELS["minimax"]:
                    return self._send_json({"ok": False, "error": f"minimaxSpeechModel must be one of {SUPPORTED_MODELS['minimax']}"}, status=400)
                minimax_voice_id = str(body.get("minimaxVoiceId", VOICE_CONFIG.get("minimaxVoiceId", "English_Graceful_Lady"))).strip() or "English_Graceful_Lady"
                if minimax_voice_id not in MINIMAX_ENGLISH_VOICES:
                    return self._send_json({"ok": False, "error": "minimaxVoiceId must be a supported English MiniMax voice"}, status=400)
                VOICE_CONFIG["minimaxSpeechModel"] = minimax_speech_model
                VOICE_CONFIG["minimaxVoiceId"] = minimax_voice_id
                VOICE_CONFIG["replyStyle"] = reply_style

                fast_agent_id = str(body.get("fastAgentId", VOICE_CONFIG.get("fastAgentId", "voice-fast"))).strip().lower() or "voice-fast"
                if fast_agent_id not in {"voice-fast", "voice-spark", "voice-51mini"}:
                    return self._send_json({"ok": False, "error": f"fastAgentId must be voice-fast, voice-spark, or voice-51mini"}, status=400)
                VOICE_CONFIG["fastAgentId"] = fast_agent_id

                fast_agent_model = str(body.get("fastAgentModel", VOICE_CONFIG.get("fastAgentModel", ""))).strip()
                allowed_models = {
                    "",  # no override — use agent default
                    "openai-codex/gpt-5.4-mini",
                    "openai-codex/gpt-5.3-codex",
                    "openai-codex/gpt-5.1",
                    "minimax/MiniMax-M2.7",
                }
                if fast_agent_model not in allowed_models:
                    return self._send_json({"ok": False, "error": f"fastAgentModel must be one of {allowed_models}"}, status=400)
                VOICE_CONFIG["fastAgentModel"] = fast_agent_model
                VOICE_CONFIG["fastMode"] = bool(body.get("fastMode", VOICE_CONFIG.get("fastMode", False)))

                return self._send_json(
                    {
                        "ok": True,
                        **VOICE_CONFIG,
                        "supportedModels": SUPPORTED_MODELS,
                        "supportedReplyStyles": SUPPORTED_REPLY_STYLES,
                        "minimaxEnglishVoices": MINIMAX_ENGLISH_VOICES,
                    }
                )

            if self.path == "/api/wake-detect":
                # Run openWakeWord detection on an uploaded audio chunk.
                # Returns {ok, detected, score, threshold, engine} JSON.
                data, filename = parse_multipart_audio(self)
                suffix = Path(filename).suffix or ".webm"
                with tempfile.NamedTemporaryFile(prefix="voice-ui-wake-", suffix=suffix, delete=False, dir=TMP_ROOT) as f:
                    f.write(data)
                    wake_audio_path = Path(f.name)
                try:
                    if not WAKE_VENV_PYTHON.exists():
                        return self._send_json({"ok": False, "error": "openwakeword venv not found"}, status=500)
                    if not WAKE_MODEL_PATH.exists():
                        return self._send_json({"ok": False, "error": "wake model not found"}, status=500)
                    result = subprocess.run(
                        [str(WAKE_VENV_PYTHON), str(WAKE_DETECT_SCRIPT),
                         "--audio", str(wake_audio_path),
                         "--model", str(WAKE_MODEL_PATH)],
                        capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0 or not result.stdout.strip():
                        return self._send_json({"ok": False, "error": result.stderr.strip() or "wake detection failed"}, status=500)
                    wake_result = json.loads(result.stdout.strip())
                    return self._send_json(wake_result)
                except subprocess.TimeoutExpired:
                    return self._send_json({"ok": False, "error": "wake detection timed out"}, status=500)
                except Exception as exc:
                    return self._send_json({"ok": False, "error": str(exc)}, status=500)
                finally:
                    try:
                        wake_audio_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            if self.path == "/api/transcribe":
                data, filename = parse_multipart_audio(self)
                suffix = Path(filename).suffix or ".webm"
                with tempfile.NamedTemporaryFile(prefix="voice-ui-", suffix=suffix, delete=False, dir=TMP_ROOT) as f:
                    f.write(data)
                    audio_path = Path(f.name)
                try:
                    transcript = transcribe_audio(audio_path)
                    return self._send_json({"ok": True, "transcript": transcript, "audioPath": str(audio_path)})
                except RuntimeError as e:
                    msg = str(e)
                    lowered = msg.lower()
                    if "empty text" in lowered or "invalid data found" in lowered:
                        # For short/silent/corrupt tiny chunks (e.g., wake-word polling), treat this as a benign empty transcript.
                        return self._send_json({"ok": True, "transcript": "", "audioPath": str(audio_path)})
                    raise

            if self.path == "/api/stop":
                stopped, detail = stop_current_turn()
                return self._send_json({
                    "ok": True,
                    "stopped": stopped,
                    "detail": detail,
                    "pending": get_pending_turn_state(),
                })

            if self.path == "/api/reminders/dismiss":
                body = parse_json_body(self)
                reminder_id = str(body.get("id") or "").strip()
                if not reminder_id:
                    return self._send_json({"ok": False, "error": "Missing id"}, status=400)
                ok = dismiss_local_reminder(reminder_id)
                return self._send_json({"ok": ok})

            if self.path == "/api/reminders/snooze":
                body = parse_json_body(self)
                reminder_id = str(body.get("id") or "").strip()
                minutes = int(body.get("minutes") or 5)
                if not reminder_id:
                    return self._send_json({"ok": False, "error": "Missing id"}, status=400)
                ok = snooze_local_reminder(reminder_id, minutes)
                return self._send_json({"ok": ok})

            if self.path == "/api/reply":
                body = parse_json_body(self)
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send_json({"ok": False, "error": "Missing text"}, status=400)
                reply = get_agent_reply(text)
                return self._send_json({"ok": True, "reply": reply})

            if self.path == "/api/text-turn":
                turn_start = time.perf_counter()

                body = parse_json_body(self)
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send_json({"ok": False, "error": "Missing text"}, status=400)

                request_cancel_turn(False)
                set_pending_turn_state({"kind": "text-turn", "input": text, "startedAt": now_iso()})

                t1 = time.perf_counter()
                reply = get_agent_reply(text)
                reply_ms = int((time.perf_counter() - t1) * 1000)

                try:
                    t2 = time.perf_counter()
                    out_path = synthesize_tts(reply)
                    tts_ms = int((time.perf_counter() - t2) * 1000)

                    t3 = time.perf_counter()
                    audio_bytes = out_path.read_bytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    encode_ms = int((time.perf_counter() - t3) * 1000)

                    timings_ms = {
                        "transcribe": 0,
                        "reply": reply_ms,
                        "tts": tts_ms,
                        "encode": encode_ms,
                        "total": int((time.perf_counter() - turn_start) * 1000),
                    }

                    entry = {
                        "timestamp": now_iso(),
                        "transcript": text,
                        "reply": reply,
                        "audioPath": str(out_path),
                        "mimeType": "audio/mpeg",
                        "voiceProvider": VOICE_CONFIG.get("provider"),
                        "voiceModel": get_voice_model_and_ref(str(VOICE_CONFIG.get("provider") or "fish"))[0],
                        "voiceRef": get_voice_model_and_ref(str(VOICE_CONFIG.get("provider") or "fish"))[1],
                        "replyStyle": get_reply_style(),
                        "timingsMs": timings_ms,
                    }
                    append_history(entry)
                    mirror_to_telegram(entry)

                    return self._send_json(
                        {
                            "ok": True,
                            "transcript": text,
                            "reply": reply,
                            "audioPath": str(out_path),
                            "mimeType": "audio/mpeg",
                            "audioBase64": audio_b64,
                            "voiceConfig": VOICE_CONFIG,
                            "timingsMs": timings_ms,
                            "entry": entry,
                        }
                    )
                except Exception as e:
                    timings_ms = {
                        "transcribe": 0,
                        "reply": reply_ms,
                        "tts": 0,
                        "encode": 0,
                        "total": int((time.perf_counter() - turn_start) * 1000),
                    }
                    return self._send_json(
                        {
                            "ok": False,
                            "transcript": text,
                            "reply": reply,
                            "error": _friendly_runtime_error_message(e),
                            "voiceConfig": VOICE_CONFIG,
                            "timingsMs": timings_ms,
                        },
                        status=200,
                    )
                finally:
                    clear_pending_turn_state()
                    request_cancel_turn(False)

            if self.path == "/api/tts":
                body = parse_json_body(self)
                text = (body.get("text") or "").strip()
                if not text:
                    return self._send_json({"ok": False, "error": "Missing text"}, status=400)
                request_cancel_turn(False)
                try:
                    out_path = synthesize_tts(text)
                    audio_bytes = out_path.read_bytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    return self._send_json(
                        {
                            "ok": True,
                            "audioPath": str(out_path),
                            "mimeType": "audio/mpeg",
                            "audioBase64": audio_b64,
                            "text": text,
                            "voiceConfig": VOICE_CONFIG,
                        }
                    )
                finally:
                    request_cancel_turn(False)

            if self.path == "/api/voice-preview":
                body = parse_json_body(self)
                text = (body.get("text") or "This is a quick voice preview.").strip()
                provider = str(body.get("provider") or VOICE_CONFIG.get("provider") or "fish").strip().lower()
                if provider not in {"fish", "elevenlabs", "local-piper", "local-kokoro", "minimax"}:
                    return self._send_json({"ok": False, "error": "provider must be fish, elevenlabs, local-piper, local-kokoro, or minimax for preview"}, status=400)

                # Best-effort temporary overrides for preview only
                original = dict(VOICE_CONFIG)
                request_cancel_turn(False)
                try:
                    if provider == "fish":
                        VOICE_CONFIG["fishModel"] = str(body.get("fishModel") or VOICE_CONFIG.get("fishModel") or "s1").strip()
                        VOICE_CONFIG["fishReferenceId"] = str(body.get("fishReferenceId") or VOICE_CONFIG.get("fishReferenceId") or "").strip()
                    elif provider == "elevenlabs":
                        VOICE_CONFIG["elevenModelId"] = str(body.get("elevenModelId") or VOICE_CONFIG.get("elevenModelId") or "eleven_multilingual_v2").strip()
                        VOICE_CONFIG["elevenVoiceId"] = str(body.get("elevenVoiceId") or VOICE_CONFIG.get("elevenVoiceId") or "JBFqnCBsd6RMkjVDRZzb").strip()
                    elif provider == "local-piper":
                        VOICE_CONFIG["piperVoiceId"] = str(body.get("piperVoiceId") or VOICE_CONFIG.get("piperVoiceId") or "en_US-lessac-medium").strip()
                    elif provider == "local-kokoro":
                        VOICE_CONFIG["kokoroVoiceId"] = str(body.get("kokoroVoiceId") or VOICE_CONFIG.get("kokoroVoiceId") or "af_bella").strip()
                    else:
                        VOICE_CONFIG["minimaxSpeechModel"] = str(body.get("minimaxSpeechModel") or VOICE_CONFIG.get("minimaxSpeechModel") or "speech-2.8-turbo").strip()
                        VOICE_CONFIG["minimaxVoiceId"] = str(body.get("minimaxVoiceId") or VOICE_CONFIG.get("minimaxVoiceId") or "English_Graceful_Lady").strip()

                    out_path = synthesize_tts(text, provider_override=provider)
                    audio_bytes = out_path.read_bytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    return self._send_json(
                        {
                            "ok": True,
                            "audioPath": str(out_path),
                            "mimeType": "audio/mpeg",
                            "audioBase64": audio_b64,
                            "provider": provider,
                            "text": text,
                        }
                    )
                except Exception as e:
                    return self._send_json(
                        {
                            "ok": False,
                            "provider": provider,
                            "text": text,
                            "error": _friendly_runtime_error_message(e),
                        },
                        status=200,
                    )
                finally:
                    VOICE_CONFIG.update(original)
                    request_cancel_turn(False)

            if self.path == "/api/voice-turn":
                turn_start = time.perf_counter()

                data, filename = parse_multipart_audio(self)
                suffix = Path(filename).suffix or ".webm"
                with tempfile.NamedTemporaryFile(prefix="voice-ui-", suffix=suffix, delete=False, dir=TMP_ROOT) as f:
                    f.write(data)
                    audio_path = Path(f.name)

                request_cancel_turn(False)
                set_pending_turn_state({"kind": "voice-turn", "filename": filename, "startedAt": now_iso(), "audioPath": str(audio_path)})

                try:
                    t0 = time.perf_counter()
                    transcript = transcribe_audio(audio_path)
                    transcribe_ms = int((time.perf_counter() - t0) * 1000)

                    t1 = time.perf_counter()
                    reply = get_agent_reply(transcript)
                    reply_ms = int((time.perf_counter() - t1) * 1000)

                    t2 = time.perf_counter()
                    out_path = synthesize_tts(reply)
                    tts_ms = int((time.perf_counter() - t2) * 1000)

                    t3 = time.perf_counter()
                    audio_bytes = out_path.read_bytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
                    encode_ms = int((time.perf_counter() - t3) * 1000)

                    timings_ms = {
                        "transcribe": transcribe_ms,
                        "reply": reply_ms,
                        "tts": tts_ms,
                        "encode": encode_ms,
                        "total": int((time.perf_counter() - turn_start) * 1000),
                    }

                    entry = {
                        "timestamp": now_iso(),
                        "transcript": transcript,
                        "reply": reply,
                        "audioPath": str(out_path),
                        "mimeType": "audio/mpeg",
                        "voiceProvider": VOICE_CONFIG.get("provider"),
                        "voiceModel": get_voice_model_and_ref(str(VOICE_CONFIG.get("provider") or "fish"))[0],
                        "voiceRef": get_voice_model_and_ref(str(VOICE_CONFIG.get("provider") or "fish"))[1],
                        "replyStyle": get_reply_style(),
                        "timingsMs": timings_ms,
                    }
                    append_history(entry)
                    mirror_to_telegram(entry)

                    return self._send_json(
                        {
                            "ok": True,
                            "transcript": transcript,
                            "reply": reply,
                            "audioPath": str(out_path),
                            "mimeType": "audio/mpeg",
                            "audioBase64": audio_b64,
                            "voiceConfig": VOICE_CONFIG,
                            "timingsMs": timings_ms,
                            "entry": entry,
                        }
                    )
                finally:
                    clear_pending_turn_state()
                    request_cancel_turn(False)

            self.send_error(404, "Not Found")
        except ValueError as e:
            return self._error(e, status=400)
        except Exception as e:
            return self._error(e, status=500)


def main():
    if VOICE_TRANSCRIBE_INPROCESS:
        # Warm Whisper model in the background so first request avoids full cold-load latency.
        threading.Thread(target=_get_faster_whisper_model, daemon=True, name="voice-ui-whisper-warm").start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Voice agent: {VOICE_AGENT_ID} (session: {VOICE_AGENT_SESSION_ID})")
    print(f"Voice UI backend listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
