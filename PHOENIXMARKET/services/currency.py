"""
Currency conversion service.
Centralized exchange rates and conversion logic.
Configurable via environment; replace with live API later if needed.
"""
import os

SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")
DEFAULT_CURRENCY = "USD"

# Rates relative to USD (1 USD = X TRY, 1 USD = X EUR)
# Override via env: TRY_PER_USD, EUR_PER_USD
_TRY_PER_USD = float(os.getenv("TRY_PER_USD", "34.50"))
_EUR_PER_USD = float(os.getenv("EUR_PER_USD", "0.92"))


def get_rates():
    """Return conversion rates: USD -> {TRY, EUR}."""
    return {"TRY": _TRY_PER_USD, "EUR": _EUR_PER_USD, "USD": 1.0}


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
    # Convert to USD first
    if from_cur == "USD":
        usd = amount
    elif from_cur == "TRY":
        usd = amount / rates["TRY"]
    elif from_cur == "EUR":
        usd = amount / rates["EUR"]
    else:
        usd = amount
    # Convert from USD to target
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
