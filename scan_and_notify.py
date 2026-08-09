#!/usr/bin/env python3
"""
BIST Mum Formasyonu Tarayıcısı -> Telegram Bildirim
(Çoklu Zaman Dilimi, her zaman dilimi ayrı mesaj, grafik linki,
 aynı gün aynı sinyali TEKRAR GÖNDERMEZ)

Ortam değişkenleri (GitHub Secrets üzerinden verilecek):
  TELEGRAM_BOT_TOKEN  -> BotFather'dan aldığın token
  TELEGRAM_CHAT_ID    -> Mesajın gideceği chat/grup/kanal ID'si
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta

SCANNER_URL = "https://scanner.tradingview.com/turkey/scan?label-product=screener-stock"
STATE_FILE = "state/sent_today.json"

PATTERNS = {
    "Candle.Hammer": ("Çekiç", "boğa"),
    "Candle.InvertedHammer": ("Ters Çekiç", "boğa"),
    "Candle.Engulfing.Bullish": ("Yutan Boğa", "boğa"),
    "Candle.MorningStar": ("Sabah Yıldızı", "boğa"),
    "Candle.Harami.Bullish": ("Hamile (Boğa)", "boğa"),
    "Candle.ShootingStar": ("Kayan Yıldız", "ayı"),
    "Candle.HangingMan": ("Asılı Adam", "ayı"),
    "Candle.Engulfing.Bearish": ("Yutan Ayı", "ayı"),
    "Candle.EveningStar": ("Akşam Yıldızı", "ayı"),
}

TIMEFRAMES = [
    ("5 Dakika", "|5", "5"),
    ("15 Dakika", "|15", "15"),
    ("1 Saat", "|60", "60"),
    ("4 Saat", "|240", "240"),
    ("Günlük", "", "D"),
    ("Haftalık", "|1W", "W"),
]

BASE_COLUMNS = ["name", "close", "change", "volume", "market_cap_basic"]

PATTERN_TF_COLUMNS = []
for pattern_key in PATTERNS:
    for tf_label, tf_suffix, _interval in TIMEFRAMES:
        PATTERN_TF_COLUMNS.append(f"{pattern_key}{tf_suffix}")

ALL_COLUMNS = BASE_COLUMNS + PATTERN_TF_COLUMNS

TRT = timezone(timedelta(hours=3))


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
        "range": [0, 80],
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


def build_messages_by_timeframe(data, already_sent):
    rows = data.get("data", [])
    if not rows:
        return {}, []

    per_tf_lines = {tf_label: [] for tf_label, _, _ in TIMEFRAMES}
    newly_sent_keys = []

    for row in rows:
        full_symbol = row.get("s", "")
        d = row.get("d", [])
        if len(d) < len(BASE_COLUMNS):
            continue
        symbol = d[0]
        close = d[1]
        change = d[2]
        change_str = f"{change:+.2f}%" if change is not None else "N/A"

        idx = len(BASE_COLUMNS)
        for pattern_key in PATTERNS:
            for tf_label, _tf_suffix, interval_code in TIMEFRAMES:
                if idx < len(d) and d[idx] == 1:
                    signal_key = f"{symbol}|{pattern_key}|{tf_label}"
                    if signal_key in already_sent:
                        idx += 1
                        continue

                    label, direction = PATTERNS[pattern_key]
                    icon = "🟢" if direction == "boğa" else "🔴"
                    link = chart_link(full_symbol, interval_code)
                    line = (
                        f"*{symbol}* — {close} TL ({change_str})\n"
                        f"{icon} {label}\n"
                        f"[Grafiği Aç]({link})"
                    )
                    per_tf_lines[tf_label].append(line)
                    newly_sent_keys.append(signal_key)
                idx += 1

    messages = {}
    for tf_label, _suffix, _interval in TIMEFRAMES:
        lines = per_tf_lines[tf_label]
        if lines:
            header = f"*BIST Mum Formasyonu Taraması — {tf_label}*\n"
            messages[tf_label] = header + "\n\n".join(lines)

    return messages, newly_sent_keys


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    max_len = 4000
    chunks = [message[i:i + max_len] for i in range(0, len(message), max_len)]

    for chunk in chunks:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }, timeout=15)
        if not resp.ok:
            print(f"Telegram gönderim hatası: {resp.status_code} {resp.text}", file=sys.stderr)
            resp.raise_for_status()


def main():
    state = load_state()
    already_sent = set(state.get("sent", []))

    try:
        data = fetch_scan()
    except Exception as e:
        print(f"Scanner sorgu hatası: {e}", file=sys.stderr)
        sys.exit(1)

    messages, newly_sent_keys = build_messages_by_timeframe(data, already_sent)

    if not messages:
        print("Yeni sinyal yok (ya hiç pattern tetiklenmedi ya da hepsi zaten bugün gönderildi).")
        return

    for tf_label, _suffix, _interval in TIMEFRAMES:
        if tf_label in messages:
            send_telegram(messages[tf_label])
            print(f"{tf_label} için Telegram bildirimi gönderildi.")

    state["sent"] = list(already_sent.union(newly_sent_keys))
    state["date"] = today_str()
    save_state(state)


if __name__ == "__main__":
    main()
