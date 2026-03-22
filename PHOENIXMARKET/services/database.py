"""
SQLite database setup and Flask application context integration.
"""
import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    """Return a connection bound to the current application context."""
    if "db" not in g:
        path = current_app.config["DATABASE_PATH"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        g.db = conn
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES categories(id),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            base_price REAL NOT NULL,
            base_currency TEXT NOT NULL DEFAULT 'USD',
            listing_language TEXT NOT NULL DEFAULT 'en',
            category TEXT NOT NULL DEFAULT '',
            image TEXT,
            stock INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            owner TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
        CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_token TEXT NOT NULL UNIQUE,
            user_email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            total_price REAL NOT NULL,
            total_currency TEXT NOT NULL DEFAULT 'USD',
            fulfillment_status TEXT NOT NULL DEFAULT 'pending',
            tracking_number TEXT,
            carrier_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(user_email);
        CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(tracking_token);

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price_usd REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            path TEXT NOT NULL,
            ip TEXT NOT NULL,
            user_agent TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);

        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    db.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
