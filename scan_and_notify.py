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
    "TUPRS",
