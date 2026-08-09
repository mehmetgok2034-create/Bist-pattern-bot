#!/usr/bin/env python3
"""
BIST30 Günlük Grafik Formasyonu Tarayıcısı (Çift Dip / Çift Tepe)
-> Telegram'a grafik görseli + özet çizelge gönderir.

Bu, mum formasyonu (Hammer/Engulfing vb.) botundan AYRI bir sistemdir.
Günde bir kez (piyasa kapanışı sonrası) çalışacak şekilde tasarlanmıştır.
Sadece BIST30 hisselerini tarar.

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
LOOKBACK_BARS = 120
TRT = timezone(timedelta(hours=3))

BIST30_SYMBOLS = [
    "AEFES", "AKBNK", "ASELS", "BIMAS", "EKGYO", "ENKAI", "EREGL", "FROTO",
    "GARAN", "GUBRF", "ISCTR", "KCHOL", "KOZAL", "KRDMD", "MGROS", "PETKM",
    "SAHOL", "SASA", "SISE", "TAVHL", "TCELL", "THYAO", "TOASO", "TTKOM",
    "TUPRS", "VAKBN", "YKBNK", "PGSUS", "ASTOR", "DSTKF",
]

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


def fetch_daily_ohlc_batch(symbols):
    yf_tickers = [s + ".IS" for s in symbols]
    raw = yf.download(
        tickers=yf_tickers, period="1y", interval="1d",
        group_by="ticker", threads=True, progress=False, auto_adjust=False,
    )
    out = {}
    if len(symbols) == 1:
        s = symbols[0]
        df = raw.dropna(how="all")
        if len(df) >= 60:
            out[s] = df
        return out
    for s, yf_t in zip(symbols, yf_tickers):
        try:
            df = raw[yf_t].dropna(how="all")
            if len(df) >= 60:
                out[s] = df
        except Exception:
            continue
    return out


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


def detect_double_top(df, order=5, tolerance=0.025, min_gap=8, lookback=LOOKBACK_BARS, recent_window=15):
    sub = df.iloc[-lookback:].reset_index(drop=True)
    highs, lows = sub["High"], sub["Low"]
    max_idx, _ = find_extrema(highs, order=order)
    if len(max_idx) < 2:
        return None
    p2_idx = max_idx[-1]
    if len(sub) - 1 - p2_idx > recent_window:
        return None
    p2_val = highs.iloc[p2_idx]
    for p1_idx in reversed(max_idx[:-1]):
        if p2_idx - p1_idx < min_gap:
            continue
        p1_val = highs.iloc[p1_idx]
        if abs(p1_val - p2_val) / p1_val > tolerance:
            continue
        between = lows.iloc[p1_idx:p2_idx + 1]
        if between.empty:
            continue
        trough_idx = between.idxmin()
        trough_val = lows.iloc[trough_idx]
        depth = (max(p1_val, p2_val) - trough_val) / max(p1_val, p2_val)
        if depth < 0.03:
            continue
        return {"p1_idx": p1_idx, "p1_val": p1_val, "p2_idx": p2_idx, "p2_val": p2_val,
                "trough_idx": trough_idx, "trough_val": trough_val, "offset": len(df) - lookback}
    return None


def detect_double_bottom(df, order=5, tolerance=0.025, min_gap=8, lookback=LOOKBACK_BARS, recent_window=15):
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
                "peak_idx": peak_idx, "peak_val": peak_val, "offset": len(df) - lookback}
    return None


def render_pattern_chart(symbol, df, pattern_info, pattern_name, lookback=LOOKBACK_BARS):
    sub = df.iloc[-lookback:].copy()
    mc = mpf.make_marketcolors(up=GREEN, down=RED, edge="inherit", wick="inherit", volume="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=mc,
                                facecolor=BG, edgecolor=GRID, figcolor=BG,
                                gridcolor=GRID, gridstyle="--",
                                rc={"font.size": 9, "text.color": FG,
                                    "axes.labelcolor": FG, "xtick.color": FG, "ytick.color": FG})
    fig, axes = mpf.plot(sub, type="candle", style=style, volume=True, returnfig=True,
                          figsize=(9, 5.5), tight_layout=True, title=f"\n{symbol} — {pattern_name}")
    ax = axes[0]
    p1_x, p2_x = pattern_info["p1_idx"], pattern_info["p2_idx"]

    if pattern_name == "Çift Tepe":
        p1_val, p2_val = pattern_info["p1_val"], pattern_info["p2_val"]
        neck_val = pattern_info["trough_val"]
        ax.scatter([p1_x, p2_x], [p1_val, p2_val], color=ACCENT, s=90, zorder=6, marker="v")
        ax.annotate("Tepe 1", (p1_x, p1_val), textcoords="offset points", xytext=(0, 12),
                    color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
        ax.annotate("Tepe 2", (p2_x, p2_val), textcoords="offset points", xytext=(0, 12),
                    color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
        ax.axhline(neck_val, color="#d29922", linestyle="--", linewidth=1.3, alpha=0.9, zorder=4)
        ax.annotate("Boyun Çizgisi", (2, neck_val), textcoords="offset points", xytext=(0, 6),
                    color="#d29922", fontsize=9, zorder=6, annotation_clip=False)
    else:
        p1_val, p2_val = pattern_info["p1_val"], pattern_info["p2_val"]
        neck_val = pattern_info["peak_val"]
        ax.scatter([p1_x, p2_x], [p1_val, p2_val], color=ACCENT, s=90, zorder=6, marker="^")
        ax.annotate("Dip 1", (p1_x, p1_val), textcoords="offset points", xytext=(0, -16),
                    color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
        ax.annotate("Dip 2", (p2_x, p2_val), textcoords="offset points", xytext=(0, -16),
                    color=ACCENT, fontsize=9, ha="center", zorder=6, annotation_clip=False)
        ax.axhline(neck_val, color="#d29922", linestyle="--", linewidth=1.3, alpha=0.9, zorder=4)
        ax.annotate("Boyun Çizgisi", (2, neck_val), textcoords="offset points", xytext=(0, 6),
                    color="#d29922", fontsize=9, zorder=6, annotation_clip=False)

    y0, y1 = ax.get_ylim()
    pad = (y1 - y0) * 0.08
    ax.set_ylim(y0 - pad, y1 + pad)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_summary_table(hits, title):
    cols_order = ["Çift Dip", "Çift Tepe"]
    col_colors = {"Çift Dip": GREEN, "Çift Tepe": RED}
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
    fig_w = 2.4 * n_cols + 0.6

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
            ax.text(ci + 0.12, y, h["symbol"], ha="left", va="center", color=FG, fontsize=10)
            ax.text(ci + 0.88, y, f"{chg:+.2f}%", ha="right", va="center", color=chg_color, fontsize=10)
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


def chart_link(symbol):
    return f"https://www.tradingview.com/chart/?symbol=BIST:{symbol}&interval=D"


def main():
    state = load_state()
    already_sent = set(state.get("sent", []))

    try:
        ohlc = fetch_daily_ohlc_batch(BIST30_SYMBOLS)
    except Exception as e:
        print(f"OHLC verisi alınamadı: {e}", file=sys.stderr)
        sys.exit(1)

    all_hits = []
    newly_sent_keys = []

    for symbol, df in ohlc.items():
        change_pct = float((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100) if len(df) >= 2 else 0.0

        for pattern_name, result in [
            ("Çift Tepe", detect_double_top(df)),
            ("Çift Dip", detect_double_bottom(df)),
        ]:
            if result is None:
                continue

            all_hits.append({"symbol": symbol, "pattern": pattern_name, "change_pct": change_pct})

            key = f"{symbol}|{pattern_name}"
            if key in already_sent:
                continue
            newly_sent_keys.append(key)

            try:
                png = render_pattern_chart(symbol, df, result, pattern_name)
                caption = (f"*{symbol}* — {pattern_name}\n{change_pct:+.2f}%\n"
                           f"[Grafiği Aç]({chart_link(symbol)})")
                send_telegram_photo(png, caption)
                print(f"{symbol} — {pattern_name}: gönderildi.")
            except Exception as e:
                print(f"{symbol} — {pattern_name} gönderilemedi: {e}", file=sys.stderr)

    if all_hits:
        try:
            table_png = render_summary_table(all_hits, f"BIST30 Günlük Formasyon Özeti — {today_str()}")
            send_telegram_photo(table_png, f"*BIST30 Günlük Formasyon Özeti* — {today_str()}")
        except Exception as e:
            print(f"Özet çizelge gönderilemedi: {e}", file=sys.stderr)
    else:
        print("Bugün hiçbir hissede Çift Dip/Çift Tepe tespit edilmedi.")

    state["sent"] = list(already_sent.union(newly_sent_keys))
    state["date"] = today_str()
    save_state(state)


if __name__ == "__main__":
    main()
