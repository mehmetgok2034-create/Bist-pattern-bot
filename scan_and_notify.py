#!/usr/bin/env python3
"""
BIST30 Mum Formasyonu Tarayıcısı -> Telegram Bildirim

- 5dk / 15dk / 1s / 4s  -> metin mesajı + tıklanabilir grafik linki
- Günlük / Haftalık     -> gerçek grafik GÖRSELİ (ilgili mum(lar) vurgulanmış)
- Aynı gün aynı sinyal TEKRAR GÖNDERİLMEZ
- Sadece BIST30 hisseleri

Ortam değişkenleri (GitHub Secrets):
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import sys
import io
import json
import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import yfinance as yf
from datetime import datetime, timezone, timedelta

SCANNER_URL = "https://scanner.tradingview.com/turkey/scan?label-product=screener-stock"
STATE_FILE = "state/sent_today.json"
MAX_MSG_LEN = 3500

BIST30_SYMBOLS = {
    "AEFES", "AKBNK", "ASELS", "BIMAS", "EKGYO", "ENKAI", "EREGL", "FROTO",
    "GARAN", "GUBRF", "ISCTR", "KCHOL", "KOZAL", "KRDMD", "MGROS", "PETKM",
    "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TTKOM",
    "TUPRS", "VAKBN", "YKBNK", "PGSUS", "ASTOR", "DSTKF",
}

PATTERNS = {
    "Candle.Hammer": ("Çekiç", "boğa", 1),
    "Candle.InvertedHammer": ("Ters Çekiç", "boğa", 1),
    "Candle.Engulfing.Bullish": ("Yutan Boğa", "boğa", 2),
    "Candle.MorningStar": ("Sabah Yıldızı", "boğa", 3),
    "Candle.Harami.Bullish": ("Hamile (Boğa)", "boğa", 2),
    "Candle.ShootingStar": ("Kayan Yıldız", "ayı", 1),
    "Candle.HangingMan": ("Asılı Adam", "ayı", 1),
    "Candle.Engulfing.Bearish": ("Yutan Ayı", "ayı", 2),
    "Candle.EveningStar": ("Akşam Yıldızı", "ayı", 3),
}

TEXT_TIMEFRAMES = [
    ("5 Dakika", "|5", "5"),
    ("15 Dakika", "|15", "15"),
    ("1 Saat", "|60", "60"),
    ("4 Saat", "|240", "240"),
]
IMAGE_TIMEFRAMES = [
    ("Günlük", "", "D"),
    ("Haftalık", "|1W", "W"),
]
ALL_TIMEFRAMES = TEXT_TIMEFRAMES + IMAGE_TIMEFRAMES

YF_INTERVAL = {"Günlük": "1d", "Haftalık": "1wk"}
YF_PERIOD = {"Günlük": "1y", "Haftalık": "3y"}
CHART_LOOKBACK = 60

BASE_COLUMNS = ["name", "close", "change", "volume", "market_cap_basic"]

PATTERN_TF_COLUMNS = []
for pattern_key in PATTERNS:
    for tf_label, tf_suffix, _interval in ALL_TIMEFRAMES:
        PATTERN_TF_COLUMNS.append(f"{pattern_key}{tf_suffix}")

ALL_COLUMNS = BASE_COLUMNS + PATTERN_TF_COLUMNS

TRT = timezone(timedelta(hours=3))

BG, FG = "#0d1117", "#e6edf3"
GREEN, RED, GRID, ACCENT = "#3fb950", "#f85149", "#21262d", "#58a6ff"


def today_str():
    return datetime.now(TRT).strftime("%Y-%m-%d")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"date": today_str(), "sent": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return {"date": today_str(), "sent": []}
    if state.get("date") != today_str():
        return {"date": today_str(), "sent": []}
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def build_payload():
    pattern_or = {
        "operation": {
            "operator": "or",
            "operands": [
                {"expression": {"left": col, "operation": "equal", "right": 1}}
                for col in PATTERN_TF_COLUMNS
            ],
        }
    }
    stock_only = {
        "operation": {
            "operator": "and",
            "operands": [
                {"expression": {"left": "type", "operation": "equal", "right": "stock"}},
                {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}},
            ],
        }
    }
    pre_ipo_exclude = {
        "expression": {"left": "typespecs", "operation": "has_none_of", "right": ["pre-ipo"]}
    }
    return {
        "columns": ALL_COLUMNS,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 250],
        "markets": ["turkey"],
        "options": {"lang": "tr"},
        "filter2": {
            "operator": "and",
            "operands": [stock_only, pre_ipo_exclude, pattern_or],
        },
    }


def fetch_scan():
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/",
    }
    resp = requests.post(SCANNER_URL, headers=headers, data=json.dumps(build_payload()), timeout=25)
    resp.raise_for_status()
    return resp.json()


def chart_link(full_symbol, interval_code):
    return f"https://www.tradingview.com/chart/?symbol={full_symbol}&interval={interval_code}"


def collect_signals(data, already_sent):
    rows = data.get("data", [])
    text_signals = {tf_label: [] for tf_label, _, _ in TEXT_TIMEFRAMES}
    image_signals = {tf_label: [] for tf_label, _, _ in IMAGE_TIMEFRAMES}
    newly_sent_keys = []

    text_tf_set = {tf_label for tf_label, _, _ in TEXT_TIMEFRAMES}

    for row in rows:
        full_symbol = row.get("s", "")
        d = row.get("d", [])
        if len(d) < len(BASE_COLUMNS):
            continue
        symbol = d[0]
        if symbol not in BIST30_SYMBOLS:
            continue

        close = d[1]
        change = d[2]
        change_str = f"{change:+.2f}%" if change is not None else "N/A"

        idx = len(BASE_COLUMNS)
        for pattern_key in PATTERNS:
            for tf_label, _tf_suffix, interval_code in ALL_TIMEFRAMES:
                if idx < len(d) and d[idx] == 1:
                    signal_key = f"{symbol}|{pattern_key}|{tf_label}"
                    if signal_key in already_sent:
                        idx += 1
                        continue

                    label, direction, n_candles = PATTERNS[pattern_key]
                    icon = "🟢" if direction == "boğa" else "🔴"
                    link = chart_link(full_symbol, interval_code)
                    newly_sent_keys.append(signal_key)

                    if tf_label in text_tf_set:
                        line = (
                            f"*{symbol}* — {close} TL ({change_str})\n"
                            f"{icon} {label}\n"
                            f"[Grafiği Aç]({link})"
                        )
                        text_signals[tf_label].append(line)
                    else:
                        image_signals[tf_label].append({
                            "symbol": symbol, "pattern_key": pattern_key, "label": label,
                            "icon": icon, "n_candles": n_candles, "close": close,
                            "change_str": change_str, "link": link, "signal_key": signal_key,
                        })
                idx += 1

    return text_signals, image_signals, newly_sent_keys


def chunk_lines(header, lines, max_len=MAX_MSG_LEN):
    chunks = []
    current = [header]
    current_len = len(header)
    for line in lines:
        if current_len + len(line) + 2 > max_len and len(current) > 1:
            chunks.append("\n\n".join(current))
            current = [header]
            current_len = len(header)
        current.append(line)
        current_len += len(line) + 2
    if len(current) > 1:
        chunks.append("\n\n".join(current))
    return chunks


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bo
