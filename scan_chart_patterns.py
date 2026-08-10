#!/usr/bin/env python3
"""
BIST30 Grafik Formasyonu Tarayıcısı -- SADECE BOĞA YÖNLÜ

- Boğa CHoCH / Boğa BOS  -> 1 Dakika, 5 Dakika, 15 Dakika
- Çift Dip                -> 1 Saat, 4 Saat, Günlük, Haftalık

Telegram'a grafik görseli + özet çizelge gönderir.
Sadece BIST30 hisselerini tarar. Aynı gün aynı sinyal
(sembol+pattern+zaman dilimi) TEKRAR GÖNDERİLMEZ.

Ortam değişkenleri:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import sys
import io
import json
import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import yfinance as yf
from scipy.signal import argrelextrema
from datetime import datetime, timezone, timedelta

STATE_FILE = "state/sent_daily_patterns.json"
STRUCTURE_ORDER = 5
TRT = timezone(timedelta(hours=3))

BIST30_SYMBOLS = [
    "AEFES", "AKBNK", "ASELS", "BIMAS", "EKGYO", "ENKAI", "EREGL", "FROTO",
    "GARAN", "GUBRF", "ISCTR", "KCHOL", "KOZAL", "KRDMD", "MGROS", "PETKM",
    "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TTKOM",
    "TUPRS", "VAKBN", "YKBNK", "PGSUS", "ASTOR", "DSTKF",
]

STRUCTURE_TIMEFRAMES = [
    {"label": "1 Dakika", "yf_interval": "1m", "yf_period": "7d", "tv_interval": "1", "lookback": 100, "fresh": 6},
    {"label": "5 Dakika", "yf_interval": "5m", "yf_period": "60d", "tv_interval": "5", "lookback": 100, "fresh": 6},
    {"label": "15 Dakika", "yf_interval": "15m", "yf_period": "60d", "tv_interval": "15", "lookback": 100, "fresh": 6},
]

DOUBLE_TIMEFRAMES = [
    {"label": "1 Saat", "yf_interval": "60m", "yf_period": "730d", "tv_interval": "60", "lookback": 120, "fresh": 15},
    {"label": "4 Saat", "yf_interval": None, "yf_period": None, "tv_interval": "240", "lookback": 120, "fresh": 15},
    {"label": "Günlük", "yf_interval": "1d", "yf_period": "2y", "tv_interval": "D", "lookback": 120, "fresh": 15},
    {"label": "Haftalık", "yf_interval": "1wk", "yf_period": "5y", "tv_interval": "W", "lookback": 120, "fresh": 15},
]

BG, FG = "#0d1117", "#e6edf3"
GREEN, RED, GRID, ACCENT, AMBER = "#3fb950", "#f85149", "#21262d", "#58a6ff", "#d29922"


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


def fetch_ohlc_batch(symbols, interval, period):
    if not symbols:
        return {}
    symbols = list(symbols)
    yf_tickers = [s + ".IS" for s in symbols]
    raw = yf.download(tickers=yf_tickers, period=period, interval=interval,
                       group_by="ticker", threads=True, progress=False, auto_adjust=False)
    out = {}
    if len(symbols) == 1:
        df = raw.dropna(how="all")
        if len(df) >= 20:
            out[symbols[0]] = df
        return out
    for s, yf_t in zip(symbols, yf_tickers):
        try:
            df = raw[yf_t].dropna(how="all")
            if len(df) >= 20:
                out[s] = df
        except Exception:
            continue
    return out


def resample_4h(df_60m):
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    try:
        df4h = df_60m.resample("4h").agg(agg).dropna(how="any")
    except Exception:
        return None
    return df4h if len(df4h) >= 20 else None


def find_extrema(series, order=5):
    values = series.values
    max_idx = argrelextrema(values, np.greater_equal, order=order)[0]
    min_idx = argrelextrema(values, np.less_equal, order=order)[0]

    def dedupe(idx_list):
        out = []
        for i in idx_list:
            if out and i - out[-1] <= order:
                if values[i] >= values[out[-1]]:
                    out[-1] = i
            else:
                out.append(i)
        return out
    return dedupe(list(max_idx)), dedupe(list(min_idx))


def detect_double_bottom(df, order=5, tolerance=0.025, min_gap=8, lookback=120, recent_window=15):
    lookback = min(lookback, len(df))
    sub = df.iloc[-lookback:].reset_index(drop=True)
    highs, lows = sub["High"], sub["Low"]
    _, min_idx = find_extrema(lows, order=order)
    if len(min_idx) < 2:
        return None
    p2_idx = min_idx[-1]
    if len(sub) - 1 - p2_idx > recent_window:
        return None
    p2_val = lows.iloc[p2_idx]
    for p1_idx in reversed(min_idx[:-1]):
        if p2_idx - p1_idx < min_gap:
            continue
        p1_val = lows.iloc[p1_idx]
        if abs(p1_val - p2_val) / p1_val > tolerance:
            continue
        between = highs.iloc[p1_idx:p2_idx + 1]
        if between.empty:
            continue
        peak_idx = between.idxmax()
        peak_val = highs.iloc[peak_idx]
        height = (peak_val - min(p1_val, p2_val)) / min(p1_val, p2_val)
        if height < 0.03:
            continue
        return {"p1_idx": p1_idx, "p1_val": p1_val, "p2_idx": p2_idx, "p2_val": p2_val,
                "peak_idx": peak_idx, "peak_val": peak_val}
    return None


def alternate_pivots(points):
    if not points:
        return []
    points = sorted(points, key=lambda p: p[0])
    result = [points[0]]
    for p in points[1:]:
        last = result[-1]
        if p[2] == last[2]:
            if p[2] == "H" and p[1] > last[1]:
                result[-1] = p
            elif p[2] == "L" and p[1] < last[1]:
                result[-1] = p
        else:
            result.append(p)
    return result


def get_pivots(df, order=STRUCTURE_ORDER):
    n = len(df)
    max_idx, _ = find_extrema(df["High"], order=order)
    _, min_idx2 = find_extrema(df["Low"], order=order)
    max_idx = [i for i in max_idx if order <= i < n - order]
    min_idx2 = [i for i in min_idx2 if order <= i < n - order]
    points = [(i, df["High"].iloc[i], "H") for i in max_idx] + \
             [(i, df["Low"].iloc[i], "L") for i in min_idx2]
    return alternate_pivots(points)


def detect_bullish_structure_break(df, order=STRUCTURE_ORDER, lookback=100, lookback_check=6):
    lookback = min(lookback, len(df))
    sub = df.iloc[-lookback:].reset_index(drop=True)
    n = len(sub)
    pivots = get_pivots(sub, order=order)
    settle_cutoff = n - 2 * order
    settled = [p for p in pivots if p[0] < settle_cutoff]

    highs_seq = [p for p in settled if p[2] == "H"]
    lows_seq = [p for p in settled if p[2] == "L"]
    if len(highs_seq) < 2 or len(lows_seq) < 2:
        return None

    last_high, prev_high = highs_seq[-1], highs_seq[-2]
    last_low, prev_low = lows_seq[-1], lows_seq[-2]

    higher_high = last_high[1] > prev_high[1]
    higher_low = last_low[1] > prev_low[1]
    bullish_structure = higher_high and higher_low

    close = sub["Close"]
    break_idx = None
    for i in range(last_high[0] + 1, n):
        if close.iloc[i] > last_high[1]:
            break_idx = i
            break
    if break_idx is None:
        return None
    if (n - 1) - break_idx > lookback_check:
        return None

    signal_type = "Boğa BOS" if bullish_structure else "Boğa CHoCH"
    return {
        "type": signal_type,
        "broken_level": last_high[1],
        "broken_idx": last_high[0],
        "break_idx": break_idx,
    }


def render_double_bottom_chart(symbol, df, pattern_info, tf_label, lookback=120):
    lookback = min(lookback, len(df))
    sub = df.iloc[-lookback:].copy()
    mc = mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                                facecolor=BG, edgecolor=GRID, figcolor=BG,
                                gridcolor=GRID, gridstyle="--",
                                rc={"font.size": 9, "text.color": FG,
                                    "axes.labelcolor": FG, "xtick.color": FG, "ytick.color": FG})
    fig, axes = mpf.plot(sub, type="candle", style=style, volume=True, returnfig=True,
                          figsize=(9, 5.5), tight_layout=True,
                          title=f"\n{symbol} — Çift Dip ({tf_label})")
    ax = axes[0]
    p1_x, p2_x = pattern_info["p1_idx"], pattern_info["p2_idx"]
    p1_val, p2_val = pattern_info["p1_val"], pattern_info["p2_val"]
    neck_val = pattern_info["peak_val"]

    ax.scatter([p1_x, p2_x], [p1_val, p2_val], color=ACCENT, s=90, zorder=6, marker="^")
    ax.annotate("Dip 1", (p1_x, p1_val), textcoords="offset points", xytext=(0, -16),
                color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
    ax.annotate("Dip 2", (p2_x, p2_val), textcoords="offset points", xytext=(0, -16),
                color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
    ax.axhline(neck_val, color=AMBER, linestyle="--", linewidth=1.3, alpha=0.9, zorder=4)
    ax.annotate("Boyun Çizgisi", (2, neck_val), textcoords="offset points", xytext=(0, 6),
                color=AMBER, fontsize=9, zorder=6, annotation_clip=False)

    y0, y1 = ax.get_ylim()
    pad = (y1 - y0) * 0.08
    ax.set_ylim(y0 - pad, y1 + pad)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_structure_chart(symbol, df, result, tf_label, lookback=100):
    lookback = min(lookback, len(df))
    sub = df.iloc[-lookback:].copy()

    mc = mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                                facecolor=BG, edgecolor=GRID, figcolor=BG,
                                gridcolor=GRID, gridstyle="--",
                                rc={"font.size": 9, "text.color": FG,
                                    "axes.labelcolor": FG, "xtick.color": FG, "ytick.color": FG})
    fig, axes = mpf.plot(sub, type="candle", style=style, volume=True, returnfig=True,
                          figsize=(9, 5.5), tight_layout=True,
                          title=f"\n{symbol} — {result['type']} ({tf_label})")
    ax = axes[0]

    broken_level = result["broken_level"]
    high_x = result["broken_idx"]
    break_x = result["break_idx"]

    ax.plot([high_x, break_x], [broken_level, broken_level],
            color=AMBER, linestyle="--", linewidth=1.3, zorder=4)
    ax.scatter([high_x], [broken_level], color=ACCENT, s=80, zorder=6, marker="v")
    ax.annotate("Kırılan Tepe", (high_x, broken_level), textcoords="offset points",
                xytext=(-4, 10), color=ACCENT, fontsize=8, ha="left", annotation_clip=False)

    break_price = sub["Close"].iloc[break_x]
    signal_color = GREEN if result["type"] == "Boğa BOS" else ACCENT
    ax.scatter([break_x], [break_price], color=signal_color, s=110, zorder=7, marker="^")
    ax.annotate(result["type"], (break_x, break_price), textcoords="offset points",
                xytext=(0, 14), color=signal_color, fontsize=10, fontweight="bold",
                ha="center", zorder=7, annotation_clip=False)

    y0, y1 = ax.get_ylim()
    pad = (y1 - y0) * 0.1
    ax.set_ylim(y0 - pad, y1 + pad)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_summary_table(hits, title):
    cols_order = ["Çift Dip", "Boğa CHoCH", "Boğa BOS"]
    col_colors = {"Çift Dip": GREEN, "Boğa CHoCH": ACCENT, "Boğa BOS": AMBER}
    grouped = {c: [] for c in cols_order}
    for h in hits:
        if h["pattern"] in grouped:
            grouped[h["pattern"]].append(h)
    for c in grouped:
        grouped[c].sort(key=lambda h: -abs(h["change_pct"]))

    max_rows = max(1, max(len(v) for v in grouped.values()))
    n_cols = len(cols_order)
    row_h, header_h = 0.34, 0.55
    fig_h = header_h + max_rows * row_h + 0.5
    fig_w = 2.7 * n_cols + 0.6

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, n_cols)
    ax.set_ylim(0, max_rows + header_h)
    ax.invert_yaxis()
    ax.axis("off")
    ax.text(n_cols / 2, -0.35, title, ha="center", va="bottom", color=FG, fontsize=13, fontweight="bold")

    for ci, col in enumerate(cols_order):
        ax.text(ci + 0.5, header_h - 0.15, col, ha="center", va="center",
                 color=col_colors[col], fontsize=11, fontweight="bold")
        ax.plot([ci, ci + 1], [header_h, header_h], color=GRID, linewidth=1)
        rows = grouped[col]
        for ri, h in enumerate(rows):
            y = header_h + ri * row_h + row_h / 2
            chg = h["change_pct"]
            chg_color = GREEN if chg >= 0 else RED
            label = f"{h['symbol']} ({h['tf']})"
            ax.text(ci + 0.08, y, label, ha="left", va="center", color=FG, fontsize=9)
            ax.text(ci + 0.92, y, f"{chg:+.2f}%", ha="right", va="center", color=chg_color, fontsize=9)
        if not rows:
            ax.text(ci + 0.5, header_h + row_h / 2, "—", ha="center", va="center", color="#484f58", fontsize=10)

    for ci in range(n_cols + 1):
        ax.axvline(ci, color=GRID, linewidth=0.8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def send_telegram_photo(photo_bytes, caption):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
        files={"photo": ("chart.png", photo_bytes, "image/png")},
        timeout=30,
    )
    if not resp.ok:
        print(f"Telegram gönderim hatası: {resp.status_code} {resp.text}", file=sys.stderr)
        resp.raise_for_status()


def chart_link(symbol, tv_interval):
    return f"https://www.tradingview.com/chart/?symbol=BIST:{symbol}&interval={tv_interval}"


def change_pct_of(df):
    return float((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100) if len(df) >= 2 else 0.0


def main():
    state = load_state()
    already_sent = set(state.get("sent", []))
    all_hits = []
    newly_sent_keys = []

    for tf in STRUCTURE_TIMEFRAMES:
        try:
            ohlc = fetch_ohlc_batch(BIST30_SYMBOLS, tf["yf_interval"], tf["yf_period"])
        except Exception as e:
            print(f"{tf['label']} OHLC alınamadı: {e}", file=sys.stderr)
            continue

        for symbol, df in ohlc.items():
            result = detect_bullish_structure_break(df, lookback=tf["lookback"], lookback_check=tf["fresh"])
            if result is None:
                continue
            pattern_name = result["type"]
            change_pct = change_pct_of(df)
            all_hits.append({"symbol": symbol, "pattern": pattern_name, "change_pct": change_pct, "tf": tf["label"]})

            key = f"{symbol}|{pattern_name}|{tf['label']}"
            if key in already_sent:
                continue
            newly_sent_keys.append(key)

            try:
                png = render_structure_chart(symbol, df, result, tf["label"], lookback=tf["lookback"])
                caption = (f"*{symbol}* — {pattern_name} ({tf['label']})\n{change_pct:+.2f}%\n"
                           f"[Grafiği Aç]({chart_link(symbol, tf['tv_interval'])})")
                send_telegram_photo(png, caption)
                print(f"{symbol} — {pattern_name} ({tf['label']}): gönderildi.")
            except Exception as e:
                print(f"{symbol} — {pattern_name} ({tf['label']}) gönderilemedi: {e}", file=sys.stderr)

    ohlc_60m_cache = None
    for tf in DOUBLE_TIMEFRAMES:
        if tf["label"] == "4 Saat":
            if ohlc_60m_cache is None:
                print("4 Saat atlandı (1 Saat verisi mevcut değil).", file=sys.stderr)
                continue
            ohlc = {}
            for s, df60 in ohlc_60m_cache.items():
                df4h = resample_4h(df60)
                if df4h is not None:
                    ohlc[s] = df4h
        else:
            try:
                ohlc = fetch_ohlc_batch(BIST30_SYMBOLS, tf["yf_interval"], tf["yf_period"])
            except Exception as e:
                print(f"{tf['label']} OHLC alınamadı: {e}", file=sys.stderr)
                continue
            if tf["label"] == "1 Saat":
                ohlc_60m_cache = ohlc

        for symbol, df in ohlc.items():
            result = detect_double_bottom(df, lookback=tf["lookback"], recent_window=tf["fresh"])
            if result is None:
                continue
            change_pct = change_pct_of(df)
            all_hits.append({"symbol": symbol, "pattern": "Çift Dip", "change_pct": change_pct, "tf": tf["label"]})

            key = f"{symbol}|Çift Dip|{tf['label']}"
            if key in already_sent:
                continue
            newly_sent_keys.append(key)

            try:
                png = render_double_bottom_chart(symbol, df, result, tf["label"], lookback=tf["lookback"])
                caption = (f"*{symbol}* — Çift Dip ({tf['label']})\n{change_pct:+.2f}%\n"
                           f"[Grafiği Aç]({chart_link(symbol, tf['tv_interval'])})")
                send_telegram_photo(png, caption)
                print(f"{symbol} — Çift Dip ({tf['label']}): gönderildi.")
            except Exception as e:
                print(f"{symbol} — Çift Dip ({tf['label']}) gönderilemedi: {e}", file=sys.stderr)

    if all_hits:
        try:
            table_png = render_summary_table(all_hits, f"BIST30 Boğa Formasyon Özeti — {today_str()}")
            send_telegram_photo(table_png, f"*BIST30 Boğa Formasyon Özeti* — {today_str()}")
        except Exception as e:
            print(f"Özet çizelge gönderilemedi: {e}", file=sys.stderr)
    else:
        print("Bu çalıştırmada hiçbir zaman diliminde sinyal tespit edilmedi.")

    state["sent"] = list(already_sent.union(newly_sent_keys))
    state["date"] = today_str()
    save_state(state)


if __name__ == "__main__":
    main()
