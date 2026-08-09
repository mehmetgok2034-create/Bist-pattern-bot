#!/usr/bin/env python3
"""
BIST Mum Formasyonu Tarayıcısı -> Telegram Bildirim
(Çoklu Zaman Dilimi, her zaman dilimi ayrı mesaj, tıklanabilir grafik linki)
"""

import os
import sys
import json
import requests

SCANNER_URL = "https://scanner.tradingview.com/turkey/scan?label-product=screener-stock"

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


def build_messages_by_timeframe(data):
    rows = data.get("data", [])
    if not rows:
        return {}

    per_tf_lines = {tf_label: [] for tf_label, _, _ in TIMEFRAMES}

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
                    label, direction = PATTERNS[pattern_key]
                    icon = "🟢" if direction == "boğa" else "🔴"
                    link = chart_link(full_symbol, interval_code)
                    line = (
                        f"*{symbol}* — {close} TL ({change_str})\n"
                        f"{icon} {label}\n"
                        f"[Grafiği Aç]({link})"
                    )
                    per_tf_lines[tf_label].append(line)
                idx += 1

    messages = {}
    for tf_label, _suffix, _interval in TIMEFRAMES:
        lines = per_tf_lines[tf_label]
        if lines:
            header = f"*BIST Mum Formasyonu Taraması — {tf_label}*\n"
            messages[tf_label] = header + "\n\n".join(lines)

    return messages


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
    try:
        data = fetch_scan()
    except Exception as e:
        print(f"Scanner sorgu hatası: {e}", file=sys.stderr)
        sys.exit(1)

    messages = build_messages_by_timeframe(data)
    if not messages:
        print("Bugün hiçbir zaman diliminde pattern tetiklenen hisse yok, bildirim gönderilmedi.")
        return

    for tf_label, _suffix, _interval in TIMEFRAMES:
        if tf_label in messages:
            send_telegram(messages[tf_label])
            print(f"{tf_label} için Telegram bildirimi gönderildi.")


if __name__ == "__main__":
    main()
