"""
Currency conversion: live rates with cache, env fallback, and static fallback.
"""
import os
import threading
import time
from typing import Any

import requests

SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")
DEFAULT_CURRENCY = "USD"

# Rates relative to USD (1 USD = X TRY, 1 USD = X EUR)
_FALLBACK_TRY = float(os.getenv("TRY_PER_USD", "34.50"))
_FALLBACK_EUR = float(os.getenv("EUR_PER_USD", "0.92"))

_CACHE_TTL = int(os.getenv("EXCHANGE_RATE_CACHE_SECONDS", "3600"))
_CACHE_LOCK = threading.Lock()
_rates_cache: dict[str, Any] = {"rates": None, "fetched_at": 0.0, "source": "fallback"}

# Primary: public JSON (no API key). Override with EXCHANGE_RATE_API_URL if needed.
_DEFAULT_API = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
)


def _static_rates() -> dict:
    return {"TRY": _FALLBACK_TRY, "EUR": _FALLBACK_EUR, "USD": 1.0}


def _fetch_live_rates() -> tuple[dict | None, str]:
    """
    Return (rates dict or None, source label).
    Expects JSON with nested 'usd' object containing 'try' and 'eur' (lowercase keys).
    """
    url = (os.getenv("EXCHANGE_RATE_API_URL") or _DEFAULT_API).strip()
    timeout = float(os.getenv("EXCHANGE_RATE_REQUEST_TIMEOUT", "8"))
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError, TypeError):
        return None, "error"

    usd_block = data.get("usd") if isinstance(data, dict) else None
    if not isinstance(usd_block, dict):
        return None, "error"

    try:
        try_rate = float(usd_block.get("try") or usd_block.get("TRY"))
        eur_rate = float(usd_block.get("eur") or usd_block.get("EUR"))
    except (TypeError, ValueError):
        return None, "error"

    if try_rate <= 0 or eur_rate <= 0:
        return None, "error"

    return {"TRY": try_rate, "EUR": eur_rate, "USD": 1.0}, "live"


def get_rates() -> dict:
    """Return conversion rates: USD -> {TRY, EUR}; cached with TTL."""
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _rates_cache["rates"]
        age = now - _rates_cache["fetched_at"]
        if cached is not None and age < _CACHE_TTL:
            return dict(cached)

    live, _src = _fetch_live_rates()
    with _CACHE_LOCK:
        if live is not None:
            _rates_cache["rates"] = live
            _rates_cache["fetched_at"] = time.monotonic()
            _rates_cache["source"] = "live"
            return dict(live)
        # Keep serving stale cache briefly if live failed but we had data
        if _rates_cache.get("rates"):
            return dict(_rates_cache["rates"])
        static = _static_rates()
        _rates_cache["rates"] = static
        _rates_cache["fetched_at"] = time.monotonic()
        _rates_cache["source"] = "fallback"
        return dict(static)


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    """
    Convert amount from from_currency to to_currency.
    Uses USD as pivot.
    """
    if not amount or from_currency == to_currency:
        return float(amount or 0)
    from_cur = (from_currency or DEFAULT_CURRENCY).upper()
    to_cur = (to_currency or DEFAULT_CURRENCY).upper()
    rates = get_rates()
    if from_cur == "USD":
        usd = amount
    elif from_cur == "TRY":
        usd = amount / rates["TRY"]
    elif from_cur == "EUR":
        usd = amount / rates["EUR"]
    else:
        usd = amount
    if to_cur == "USD":
        return round(usd, 2)
    if to_cur == "TRY":
        return round(usd * rates["TRY"], 2)
    if to_cur == "EUR":
        return round(usd * rates["EUR"], 2)
    return round(usd, 2)


def format_price(amount, currency: str = None) -> str:
    """Format price for display with currency symbol."""
    amt = float(amount or 0)
    cur = (currency or DEFAULT_CURRENCY).upper()
    symbols = {"TRY": "₺", "USD": "$", "EUR": "€"}
    sym = symbols.get(cur, cur)
    return f"{amt:,.2f} {sym}".replace(",", " ")
