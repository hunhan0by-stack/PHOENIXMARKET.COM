"""
Listing/price helpers.
Uses currency service for display prices.
"""
from services.currency import convert, format_price, DEFAULT_CURRENCY


def get_display_price(product: dict, target_currency: str = None) -> float:
    """
    Get the display price for a product in the target currency.
    Product may have base_price/base_currency or legacy price (treated as USD).
    """
    cur = (target_currency or DEFAULT_CURRENCY).upper()
    base_price = product.get("base_price")
    if base_price is None:
        base_price = product.get("price", 0)
    base_currency = (product.get("base_currency") or "USD").upper()
    return convert(float(base_price or 0), base_currency, cur)


def format_product_price(product: dict, target_currency: str = None) -> str:
    """Format a product's price for display in target currency."""
    amount = get_display_price(product, target_currency)
    cur = (target_currency or DEFAULT_CURRENCY).upper()
    return format_price(amount, cur)


def get_listing_language_badge(product: dict) -> str:
    """Return TR or EN badge for listing language."""
    lang = (product.get("listing_language") or "en").lower()[:2]
    return "TR" if lang == "tr" else "EN"


def get_base_price(product: dict) -> float:
    """Get the admin-defined base price (for order storage)."""
    base = product.get("base_price")
    if base is not None:
        return float(base)
    return float(product.get("price", 0))


def get_base_currency(product: dict) -> str:
    """Get the admin-defined base currency."""
    return (product.get("base_currency") or "USD").upper()
