"""
Data access layer: products, categories, orders, analytics, and site configuration.
"""
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from services.database import get_db

_analytics_lock = threading.Lock()
ANALYTICS_MAX_ENTRIES = 10000

DEFAULT_SITE_CONTENT = {
    "hero_badge": "Fast & reliable digital services",
    "hero_title": "PHOENIXMARKET",
    "hero_subtitle": (
        "A modern marketplace for digital services like Canva Pro subscriptions, "
        "Instagram followers, TikTok views, and ChatGPT accounts. "
        "Affordable pricing, fast delivery, and trusted support."
    ),
    "hero_cta_label": "Browse services",
    "checkout_helper_text": (
        "To complete this purchase you will be redirected to our official marketplace listing on itemsatis."
    ),
    "checkout_button_label": "Continue to secure purchase",
    "footer_contact_text": "contact@phoenixmarket.example",
    "footer_discord_text": "YourDiscordTag#0000",
}

SITE_CONTENT_KEYS = list(DEFAULT_SITE_CONTENT.keys())


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "item"


def _parse_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _product_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d.get("created_at")) or datetime.now(timezone.utc)
    d["updated_at"] = _parse_dt(d.get("updated_at")) or datetime.now(timezone.utc)
    img = d.get("image") or ""
    if img.startswith("/static/"):
        d["image"] = img
    elif img:
        d["image_path"] = img
    return d


def _category_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d.get("created_at")) or datetime.now(timezone.utc)
    return d


def list_all_products() -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products ORDER BY id ASC"
    ).fetchall()
    return [_product_from_row(r) for r in rows]


def list_public_products() -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM products WHERE status = 'active' ORDER BY id ASC"
    ).fetchall()
    return [_product_from_row(r) for r in rows]


def get_product(product_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _product_from_row(row) if row else None


def get_product_by_slug(slug: str):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE slug = ?", (slug,)).fetchone()
    return _product_from_row(row) if row else None


def list_existing_slugs(exclude_id: int | None = None) -> set:
    db = get_db()
    if exclude_id is None:
        rows = db.execute("SELECT slug FROM products").fetchall()
    else:
        rows = db.execute(
            "SELECT slug FROM products WHERE id != ?", (exclude_id,)
        ).fetchall()
    return {r["slug"] for r in rows if r["slug"]}


def create_product(data: dict) -> int:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    img = data.get("image_path") or data.get("image") or ""
    cur = db.execute(
        """
        INSERT INTO products (
            name, slug, description, base_price, base_currency, listing_language,
            category, image, stock, status, owner, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"],
            data["slug"],
            data.get("description", ""),
            float(data["base_price"]),
            data.get("base_currency", "USD"),
            data.get("listing_language", "en"),
            data.get("category", ""),
            img,
            int(data.get("stock", 0)),
            data.get("status", "active"),
            data.get("owner", "admin"),
            now,
            now,
        ),
    )
    db.commit()
    return cur.lastrowid


def update_product(product_id: int, data: dict) -> None:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    fields = []
    values = []
    mapping = {
        "name": "name",
        "slug": "slug",
        "description": "description",
        "base_price": "base_price",
        "base_currency": "base_currency",
        "listing_language": "listing_language",
        "category": "category",
        "stock": "stock",
        "status": "status",
    }
    for key, col in mapping.items():
        if key in data:
            fields.append(f"{col} = ?")
            values.append(data[key])
    if "image_path" in data and data["image_path"]:
        fields.append("image = ?")
        values.append(data["image_path"])
    elif "image" in data:
        fields.append("image = ?")
        values.append(data["image"] or "")
    fields.append("updated_at = ?")
    values.append(now)
    values.append(product_id)
    db.execute(
        f"UPDATE products SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    db.commit()


def delete_product(product_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.commit()


def list_categories() -> list:
    db = get_db()
    rows = db.execute("SELECT * FROM categories ORDER BY name ASC").fetchall()
    return [_category_from_row(r) for r in rows]


def get_category(category_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return _category_from_row(row) if row else None


def get_category_by_slug(slug: str):
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    return _category_from_row(row) if row else None


def category_slugs_excluding(exclude_id: int | None = None) -> set:
    db = get_db()
    if exclude_id is None:
        rows = db.execute("SELECT slug FROM categories").fetchall()
    else:
        rows = db.execute(
            "SELECT slug FROM categories WHERE id != ?", (exclude_id,)
        ).fetchall()
    return {r["slug"] for r in rows if r["slug"]}


def create_category(name: str, slug: str, parent_id=None) -> int:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        """
        INSERT INTO categories (name, slug, parent_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, slug, parent_id, now),
    )
    db.commit()
    return cur.lastrowid


def update_category(category_id: int, name: str, slug: str, parent_id) -> None:
    db = get_db()
    db.execute(
        "UPDATE categories SET name = ?, slug = ?, parent_id = ? WHERE id = ?",
        (name, slug, parent_id, category_id),
    )
    db.commit()


def delete_category(category_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    db.commit()


def _order_items_for(order_id: int) -> list:
    db = get_db()
    rows = db.execute(
        """
        SELECT product_id, product_name, quantity, unit_price_usd
        FROM order_items WHERE order_id = ?
        """,
        (order_id,),
    ).fetchall()
    return [
        {
            "product_id": r["product_id"],
            "product_name": r["product_name"],
            "quantity": r["quantity"],
            "price": float(r["unit_price_usd"]),
        }
        for r in rows
    ]


def _order_from_row(row, include_items: bool) -> dict:
    oid = row["id"]
    d = {
        "id": oid,
        "tracking_token": row["tracking_token"],
        "user_email": row["user_email"],
        "full_name": row["full_name"],
        "notes": row["notes"] or "",
        "total_price": float(row["total_price"]),
        "total_currency": row["total_currency"] or "USD",
        "fulfillment_status": row["fulfillment_status"] or "pending",
        "tracking_number": row["tracking_number"],
        "carrier_note": row["carrier_note"],
        "created_at": _parse_dt(row["created_at"]),
        "updated_at": _parse_dt(row["updated_at"]),
    }
    if include_items:
        # Use line_items — dict.items would shadow Jinja attribute access
        d["line_items"] = _order_items_for(oid)
    return d


def create_order(
    *,
    tracking_token: str,
    user_email: str,
    full_name: str,
    notes: str,
    total_price: float,
    total_currency: str,
    line_items: list,
) -> int:
    """
    line_items: list of dicts with product_id, product_name, quantity, unit_price_usd
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        """
        INSERT INTO orders (
            tracking_token, user_email, full_name, notes,
            total_price, total_currency, fulfillment_status,
            tracking_number, carrier_note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)
        """,
        (
            tracking_token,
            user_email.strip().lower(),
            full_name.strip(),
            (notes or "").strip(),
            float(total_price),
            total_currency or "USD",
            now,
            now,
        ),
    )
    order_id = cur.lastrowid
    for li in line_items:
        db.execute(
            """
            INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price_usd)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_id,
                int(li["product_id"]),
                li["product_name"],
                int(li["quantity"]),
                float(li["unit_price_usd"]),
            ),
        )
    db.commit()
    return order_id


def get_order_by_id(order_id: int, include_items: bool = True):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _order_from_row(row, include_items) if row else None


def get_order_by_tracking_token(token: str, include_items: bool = True):
    db = get_db()
    row = db.execute(
        "SELECT * FROM orders WHERE tracking_token = ?", (token,)
    ).fetchone()
    return _order_from_row(row, include_items) if row else None


def list_orders_for_email(email: str) -> list:
    db = get_db()
    em = (email or "").strip().lower()
    rows = db.execute(
        """
        SELECT * FROM orders WHERE lower(user_email) = ?
        ORDER BY id DESC
        """,
        (em,),
    ).fetchall()
    return [_order_from_row(r, True) for r in rows]


def list_orders_admin(limit: int = 200) -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_order_from_row(r, False) for r in rows]


def count_orders() -> int:
    db = get_db()
    r = db.execute("SELECT COUNT(*) AS c FROM orders").fetchone()
    return int(r["c"])


def count_orders_for_email(email: str) -> int:
    db = get_db()
    r = db.execute(
        "SELECT COUNT(*) AS c FROM orders WHERE lower(user_email) = ?",
        ((email or "").strip().lower(),),
    ).fetchone()
    return int(r["c"])


def update_order_fulfillment(
    order_id: int,
    *,
    fulfillment_status: str,
    tracking_number: str | None,
    carrier_note: str | None,
) -> None:
    db = get_db()
    row = db.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        return
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        UPDATE orders SET
            fulfillment_status = ?,
            tracking_number = ?,
            carrier_note = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (fulfillment_status, tracking_number, carrier_note, now, order_id),
    )
    db.commit()


def record_visit(path: str, ip: str, user_agent: str) -> None:
    with _analytics_lock:
        db = get_db()
        db.execute(
            "INSERT INTO visits (ts, path, ip, user_agent) VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                path or "/",
                ip or "unknown",
                (user_agent or "")[:500],
            ),
        )
        total = db.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
        if total > ANALYTICS_MAX_ENTRIES:
            trim = total - ANALYTICS_MAX_ENTRIES
            db.execute(
                "DELETE FROM visits WHERE id IN (SELECT id FROM visits ORDER BY id ASC LIMIT ?)",
                (trim,),
            )
        db.commit()


def get_analytics_for_admin() -> dict:
    db = get_db()
    visits = db.execute(
        "SELECT ts, path, ip, user_agent FROM visits ORDER BY id ASC"
    ).fetchall()
    visits = [dict(v) for v in visits]
    total_visits = len(visits)
    unique_ips = set(v.get("ip") or "unknown" for v in visits)
    total_visitors = len(unique_ips)
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_prefix = today_start.strftime("%Y-%m-%d")
    today_visits = sum(
        1 for v in visits if v.get("ts") and v["ts"].startswith(today_prefix)
    )
    recent = list(reversed(visits[-100:]))

    def ua_summary(ua):
        if not ua or ua == "unknown":
            return "—"
        ua = ua[:80]
        if "Chrome" in ua and "Edg" not in ua:
            return "Chrome"
        if "Firefox" in ua:
            return "Firefox"
        if "Safari" in ua and "Chrome" not in ua:
            return "Safari"
        if "Edg" in ua:
            return "Edge"
        return ua.split("/")[0] if "/" in ua else ua[:20]

    recent_with_summary = [
        {
            "ts": v.get("ts", ""),
            "path": v.get("path", "/"),
            "ip": v.get("ip", "—"),
            "user_agent_summary": ua_summary(v.get("user_agent")),
        }
        for v in recent
    ]
    path_counts = {}
    for v in visits:
        p = (v.get("path") or "/").strip() or "/"
        path_counts[p] = path_counts.get(p, 0) + 1
    top_pages = sorted(path_counts.items(), key=lambda x: -x[1])[:15]
    return {
        "total_visits": total_visits,
        "total_visitors": total_visitors,
        "today_visits": today_visits,
        "recent_visits": recent_with_summary,
        "top_pages": top_pages,
    }


def get_config(key: str, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return row["value"]


def set_config(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO app_config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    db.commit()


def load_site_content() -> dict:
    out = dict(DEFAULT_SITE_CONTENT)
    db = get_db()
    for key in SITE_CONTENT_KEYS:
        row = db.execute(
            "SELECT value FROM app_config WHERE key = ?", (f"site_content.{key}",)
        ).fetchone()
        if row and row["value"] is not None:
            out[key] = row["value"]
    return out


def save_site_content(updates: dict) -> None:
    for key, val in updates.items():
        if key in SITE_CONTENT_KEYS:
            set_config(f"site_content.{key}", val)


def load_site_settings(default_itemsatis: str, default_email: str, default_discord: str) -> dict:
    return {
        "itemsatis_redirect_url": get_config(
            "site_settings.itemsatis_redirect_url", default_itemsatis
        ),
        "support_email": get_config(
            "site_settings.support_email", default_email
        ),
        "support_discord": get_config(
            "site_settings.support_discord", default_discord
        ),
    }


def save_site_settings(itemsatis_url: str, support_email: str, support_discord: str) -> None:
    if itemsatis_url:
        set_config("site_settings.itemsatis_redirect_url", itemsatis_url)
    set_config("site_settings.support_email", support_email)
    set_config("site_settings.support_discord", support_discord)


def migrate_listing_images_png_to_jpg() -> None:
    """Point product posters back to canonical .jpg assets (idempotent)."""
    db = get_db()
    pairs = (
        ("/static/images/canva.png", "/static/images/canva.jpg"),
        ("/static/images/instagram.png", "/static/images/instagram.jpg"),
        ("/static/images/tiktok.png", "/static/images/tiktok.jpg"),
        ("/static/images/chatgpt.png", "/static/images/chatgpt.jpg"),
    )
    for old, new in pairs:
        db.execute("UPDATE products SET image = ? WHERE image = ?", (new, old))
    db.commit()


def seed_if_empty(default_itemsatis: str) -> None:
    db = get_db()
    n = db.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    if n > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    seed_products = [
        {
            "name": "Canva Pro Subscription",
            "slug": "canva-pro-subscription",
            "base_price": 9.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Premium Canva Pro access with templates and collaboration tools.",
            "category": "Design",
            "image": "/static/images/canva.jpg",
            "stock": 100,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "Instagram Followers",
            "slug": "instagram-followers",
            "base_price": 14.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Boost your Instagram presence with targeted follower packages.",
            "category": "Social Media",
            "image": "/static/images/instagram.jpg",
            "stock": 200,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "TikTok Views",
            "slug": "tiktok-views",
            "base_price": 12.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Increase your TikTok views for better reach and engagement.",
            "category": "Social Media",
            "image": "/static/images/tiktok.jpg",
            "stock": 150,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "ChatGPT Accounts",
            "slug": "chatgpt-accounts",
            "base_price": 19.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Reliable ChatGPT access for research, coding and content.",
            "category": "AI",
            "image": "/static/images/chatgpt.jpg",
            "stock": 80,
            "status": "active",
            "owner": "admin",
        },
    ]
    for p in seed_products:
        db.execute(
            """
            INSERT INTO products (
                name, slug, description, base_price, base_currency, listing_language,
                category, image, stock, status, owner, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["name"],
                p["slug"],
                p["description"],
                p["base_price"],
                p["base_currency"],
                p["listing_language"],
                p["category"],
                p["image"],
                p["stock"],
                p["status"],
                p["owner"],
                now,
                now,
            ),
        )
    seen = set()
    for p in seed_products:
        name = p["category"]
        if not name or name in seen:
            continue
        seen.add(name)
        base_slug = slugify(name)
        slug = base_slug
        existing = category_slugs_excluding()
        counter = 2
        while slug in existing:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing.add(slug)
        db.execute(
            """
            INSERT INTO categories (name, slug, parent_id, created_at)
            VALUES (?, ?, NULL, ?)
            """,
            (name, slug, now),
        )
    for key, val in DEFAULT_SITE_CONTENT.items():
        db.execute(
            "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
            (f"site_content.{key}", val),
        )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.itemsatis_redirect_url", default_itemsatis),
    )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.support_email", "contact@phoenixmarket.example"),
    )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.support_discord", "YourDiscordTag#0000"),
    )
    db.commit()


def maybe_migrate_analytics_json(data_dir: Path) -> None:
    """One-time import from legacy analytics.json into SQLite."""
    legacy = data_dir / "analytics.json"
    if not legacy.exists():
        return
    db = get_db()
    existing = db.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
    if existing > 0:
        return
    try:
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, list) or not data:
        return
    for v in data[-ANALYTICS_MAX_ENTRIES:]:
        if not isinstance(v, dict):
            continue
        db.execute(
            "INSERT INTO visits (ts, path, ip, user_agent) VALUES (?, ?, ?, ?)",
            (
                v.get("ts") or datetime.now(timezone.utc).isoformat(),
                (v.get("path") or "/")[:500],
                (v.get("ip") or "unknown")[:200],
                (v.get("user_agent") or "")[:500],
            ),
        )
    db.commit()
