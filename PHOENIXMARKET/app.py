import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from services.currency import convert, format_price
from services.database import init_app as register_db, init_schema
from services.listing import (
    get_display_price,
    format_product_price,
    get_listing_language_badge,
    get_base_price,
    get_base_currency,
)
import services.store as store
from services.store import slugify
from services.translations import t as translate

load_dotenv()

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Base configuration – environment driven for production readiness
# -----------------------------------------------------------------------------
app_root = Path(__file__).resolve().parent

# On Render, require SECRET_KEY and ITEMSATIS_REDIRECT_URL; allow dev defaults locally
if os.getenv("RENDER"):
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable is required on Render. "
            "Set it in your Render service Environment tab."
        )
    itemsatis_url = os.getenv("ITEMSATIS_REDIRECT_URL")
    if not itemsatis_url:
        raise RuntimeError(
            "ITEMSATIS_REDIRECT_URL environment variable is required on Render. "
            "Set it in your Render service Environment tab."
        )
else:
    secret_key = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    itemsatis_url = os.getenv("ITEMSATIS_REDIRECT_URL", "https://example.com/itemsatis")

app.config["SECRET_KEY"] = secret_key
app.config["ITEMSATIS_REDIRECT_URL"] = itemsatis_url
ITEMSATIS_REDIRECT_URL = itemsatis_url

UPLOAD_FOLDER = app_root / "static" / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Admin credentials: required in production (Render or FLASK_ENV=production); dev defaults otherwise.
_IS_PRODUCTION = bool(os.getenv("RENDER") or os.getenv("FLASK_ENV") == "production")
if _IS_PRODUCTION:
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise RuntimeError(
            "ADMIN_USERNAME and ADMIN_PASSWORD environment variables are required in production. "
            "Set them in Render (or use FLASK_ENV=production locally only with real secrets)."
        )
else:
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "phoenixmarket")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# -----------------------------------------------------------------------------
# Data directory & SQLite
# -----------------------------------------------------------------------------
DATA_DIR = app_root / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", str(DATA_DIR / "phoenixmarket.db"))

register_db(app)
with app.app_context():
    init_schema()
    store.seed_if_empty(itemsatis_url)
    store.migrate_listing_images_png_to_jpg()
    store.maybe_migrate_analytics_json(DATA_DIR)
    _persisted = store.load_site_settings(
        itemsatis_url,
        "contact@phoenixmarket.example",
        "YourDiscordTag#0000",
    )
    if _persisted.get("itemsatis_redirect_url"):
        app.config["ITEMSATIS_REDIRECT_URL"] = _persisted["itemsatis_redirect_url"]

if os.getenv("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

ORDER_STATUS_CHOICES = ("pending", "processing", "completed", "cancelled")


def get_product(product_id):
    return store.get_product(product_id)


def get_product_by_slug(slug: str):
    return store.get_product_by_slug(slug)


def get_category_by_slug(slug: str):
    return store.get_category_by_slug(slug)


def get_cart():
    return session.setdefault("cart", {})


def build_cart_items(cart_data, display_currency: str = None):
    """
    Return structured cart line items and subtotal.
    Uses display_currency for user-facing prices.
    Order storage uses USD-converted values for consistency.
    """
    from services.currency import DEFAULT_CURRENCY
    cur = display_currency or DEFAULT_CURRENCY
    items = []
    subtotal_usd = 0.0
    subtotal_display = 0.0
    for pid_str, qty in cart_data.items():
        try:
            product_id = int(pid_str)
        except (TypeError, ValueError):
            continue
        product = get_product(product_id)
        if not product:
            continue
        base_price = get_base_price(product)
        base_cur = get_base_currency(product)
        line_usd = convert(base_price * qty, base_cur, "USD")
        unit_display = get_display_price(product, cur)
        line_display = convert(base_price * qty, base_cur, cur)
        subtotal_usd += line_usd
        subtotal_display += line_display
        items.append({
            "product": product,
            "qty": qty,
            "line_total_usd": line_usd,
            "display_unit_price": unit_display,
            "display_line_total": line_display,
        })
    return items, subtotal_usd, subtotal_display


def allowed_image_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


def process_listing_image(file_storage):
    """Validate and process an uploaded image, returning a static-relative path."""
    if not file_storage or file_storage.filename == "":
        return None
    filename = secure_filename(file_storage.filename)
    if not allowed_image_file(filename):
        return None

    # Build safe destination path
    dest_path = UPLOAD_FOLDER / filename

    # Open and resize/crop to 1100x750 using a cover strategy
    with Image.open(file_storage.stream) as img:
        target_w, target_h = 1100, 750
        src_w, src_h = img.size
        src_ratio = src_w / src_h
        target_ratio = target_w / target_h

        if src_ratio > target_ratio:
            # Source is wider: fit height, then crop width
            new_h = target_h
            new_w = int(new_h * src_ratio)
        else:
            # Source is taller/narrower: fit width, then crop height
            new_w = target_w
            new_h = int(new_w / src_ratio)

        img = img.convert("RGB")
        img = img.resize((new_w, new_h), Image.LANCZOS)

        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        right = left + target_w
        bottom = top + target_h
        img = img.crop((left, top, right, bottom))

        img.save(dest_path, quality=90)

    # Return path relative to static folder
    return f"uploads/{filename}"


# Session keys for locale and currency
LOCALE_KEY = "locale"
CURRENCY_KEY = "currency"
SUPPORTED_LOCALES = ("en", "tr")
SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")


def get_current_locale():
    """Get user's locale from session, default en."""
    loc = session.get(LOCALE_KEY) or "en"
    return loc if loc in SUPPORTED_LOCALES else "en"


def get_current_currency():
    """Get user's currency from session, default USD."""
    cur = session.get(CURRENCY_KEY) or "USD"
    return cur if cur in SUPPORTED_CURRENCIES else "USD"


@app.context_processor
def inject_globals():
    """Inject common marketplace-wide data into templates."""
    cart = session.get("cart") or {}
    cart_count = sum(cart.values())
    locale = get_current_locale()
    currency = get_current_currency()

    def t(key):
        return translate(key, locale)

    site_settings = store.load_site_settings(
        app.config.get("ITEMSATIS_REDIRECT_URL", ""),
        "contact@phoenixmarket.example",
        "YourDiscordTag#0000",
    )
    return {
        "cart_count": cart_count,
        "site_content": store.load_site_content(),
        "site_settings": site_settings,
        "locale": locale,
        "currency": currency,
        "t": t,
        "format_price": format_price,
        "get_display_price": lambda p: get_display_price(p, currency),
        "format_product_price": lambda p: format_product_price(p, currency),
        "get_listing_language_badge": get_listing_language_badge,
        "SUPPORTED_LOCALES": SUPPORTED_LOCALES,
        "SUPPORTED_CURRENCIES": SUPPORTED_CURRENCIES,
    }


@app.before_request
def record_visit():
    """Record each page view for admin analytics (skip static, API, health, sitemap)."""
    path = request.path
    if path.startswith("/static/") or path.startswith("/api/") or path in ("/health", "/sitemap.xml"):
        return
    ip = request.remote_addr or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or "unknown"
    ua = request.user_agent.string if request.user_agent else ""
    try:
        store.record_visit(path, ip, ua)
    except Exception:  # noqa: S110
        pass


def get_public_products():
    """Return products that are visible in the public marketplace."""
    return store.list_public_products()


def is_admin():
    user = session.get("user") or {}
    # Support both the older "admin" flag and the new "is_admin" flag
    if session.get("is_admin"):
        return True
    return bool(session.get("admin")) and user.get("role") == "admin"


def require_admin():
    if not is_admin():
        return redirect(url_for("admin_login"))


# -----------------------------------------------------------------------------
# Locale and currency switchers
# -----------------------------------------------------------------------------
@app.route("/set-locale/<locale>")
def set_locale(locale):
    """Set user locale and redirect back."""
    loc = (locale or "").lower()[:2]
    if loc in SUPPORTED_LOCALES:
        session[LOCALE_KEY] = loc
    return redirect(request.referrer or url_for("index"))


@app.route("/set-currency/<currency>")
def set_currency(currency):
    """Set user currency and redirect back."""
    cur = (currency or "").upper()
    if cur in SUPPORTED_CURRENCIES:
        session[CURRENCY_KEY] = cur
    return redirect(request.referrer or url_for("index"))


# -----------------------------------------------------------------------------
# Health check (for Render and load balancers)
# -----------------------------------------------------------------------------
@app.route("/sitemap.xml")
def sitemap():
    """Generate sitemap for SEO."""
    base_url = request.url_root.rstrip("/")
    pages = [("/", "daily", "1.0"), ("/products", "daily", "0.9"), ("/contact", "monthly", "0.5")]
    for c in store.list_categories():
        pages.append((f"/category/{c['slug']}", "weekly", "0.8"))
    for p in get_public_products():
        slug = p.get("slug") or str(p["id"])
        pages.append((f"/product/{slug}", "weekly", "0.8"))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for path, changefreq, priority in pages:
        xml.append(f"  <url><loc>{base_url}{path}</loc><changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>")
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


@app.route("/health")
def health():
    """Lightweight health check; skip analytics."""
    return jsonify({"status": "ok"}), 200


# -----------------------------------------------------------------------------
# Public routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    products = get_public_products()
    featured = products[:4]
    return render_template(
        "index.html",
        products=products,
        featured_products=featured,
        categories=store.list_categories(),
    )


@app.route("/products")
def products():
    return render_template("products.html", products=get_public_products())


@app.route("/product/<slug_or_id>")
def product_detail(slug_or_id):
    """SEO-friendly product detail page: accepts either slug or numeric ID."""
    product = None
    if slug_or_id.isdigit():
        product = get_product(int(slug_or_id))
    if not product:
        product = get_product_by_slug(slug_or_id)
    if not product:
        abort(404)
    # Only admins can see inactive/draft products directly
    if product.get("status") != "active" and not is_admin():
        abort(404)
    return render_template("product_detail.html", product=product)


@app.route("/search")
def search():
    """Marketplace search by title, description, and category."""
    q = (request.args.get("q") or "").strip()
    query = q.lower()
    products = get_public_products()
    if query:
        products = [
            p
            for p in products
            if query in p.get("name", "").lower()
            or query in p.get("description", "").lower()
            or query in p.get("category", "").lower()
        ]
    return render_template("search.html", products=products, query=q)


@app.route("/category/<category_slug>")
def category_page(category_slug):
    """Public category browsing page."""
    category = get_category_by_slug(category_slug)
    if not category:
        abort(404)
    name = category["name"]
    products = [
        p
        for p in get_public_products()
        if (p.get("category") or "").lower() == name.lower()
    ]
    return render_template("category.html", category=category, products=products)


# -----------------------------------------------------------------------------
# Authentication (mock, session-based)
# -----------------------------------------------------------------------------
@app.route("/signin", methods=["GET", "POST"])
def signin():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        # In a real app, validate against database
        if not email or not password:
            flash("Email and password are required.", "error")
        else:
            session["user"] = {"email": email}
            flash("Signed in successfully.", "success")
            return redirect(url_for("account"))
    return render_template("signin.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not email or not password or not confirm:
            flash("All fields are required.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            # Real app: create user in DB
            session["user"] = {"email": email}
            flash("Account created and signed in.", "success")
            return redirect(url_for("account"))
    return render_template("signup.html")


@app.route("/signout")
def signout():
    session.pop("user", None)
    session.pop("admin", None)
    session.pop("is_admin", None)
    flash("Signed out.", "success")
    return redirect(url_for("index"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin", None)
    u = session.get("user") or {}
    if u.get("role") == "admin":
        session.pop("user", None)
    flash("Admin logged out.", "success")
    return redirect(url_for("index"))


@app.route("/contact")
def contact():
    """Contact / support page."""
    return render_template("contact.html")


@app.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/account")
def account():
    user = session.get("user")
    if not user:
        return redirect(url_for("signin"))
    user_email = (user.get("email") or "").strip().lower()
    order_count = store.count_orders_for_email(user_email)
    return render_template("account.html", user=user, order_count=order_count)


@app.route("/account/orders")
def account_orders():
    """Order history for signed-in users."""
    user = session.get("user")
    if not user:
        return redirect(url_for("signin"))
    user_email = (user.get("email") or "").strip().lower()
    user_orders = store.list_orders_for_email(user_email)
    return render_template("orders.html", orders=user_orders)


# -----------------------------------------------------------------------------
# Cart and checkout
# -----------------------------------------------------------------------------
@app.route("/cart")
def cart():
    cart_data = get_cart()
    items, _subtotal_base, subtotal_display = build_cart_items(
        cart_data, get_current_currency()
    )
    return render_template(
        "cart.html", items=items, subtotal=subtotal_display
    )


@app.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = get_product(product_id)
    if not product:
        flash("Product not found.", "error")
        return redirect(url_for("products"))
    cart_data = get_cart()
    key = str(product_id)
    cart_data[key] = cart_data.get(key, 0) + 1
    session["cart"] = cart_data
    flash("Added to cart.", "success")
    return redirect(request.referrer or url_for("products"))


@app.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart(product_id):
    qty_raw = request.form.get("qty", "1")
    try:
        qty = max(0, int(qty_raw))
    except ValueError:
        qty = 1
    cart_data = get_cart()
    key = str(product_id)
    if qty == 0:
        cart_data.pop(key, None)
    else:
        cart_data[key] = qty
    session["cart"] = cart_data
    flash("Cart updated.", "success")
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_data = get_cart()
    if not cart_data:
        flash("Your cart is empty.", "error")
        return redirect(url_for("products"))

    items, subtotal_base, subtotal_display = build_cart_items(
        cart_data, get_current_currency()
    )

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()

        if not full_name or not email:
            flash("Full name and email are required to place an order.", "error")
            return render_template(
                "checkout.html", items=items, subtotal=subtotal_display
            )

        tracking_token = str(uuid.uuid4())
        line_items = []
        for item in items:
            p = item["product"]
            line_items.append(
                {
                    "product_id": p["id"],
                    "product_name": p["name"],
                    "quantity": item["qty"],
                    "unit_price_usd": convert(
                        get_base_price(p),
                        get_base_currency(p),
                        "USD",
                    ),
                }
            )
        order_id = store.create_order(
            tracking_token=tracking_token,
            user_email=email,
            full_name=full_name,
            notes=notes,
            total_price=subtotal_base,
            total_currency="USD",
            line_items=line_items,
        )
        session["cart"] = {}
        session["last_order_id"] = order_id
        session["last_tracking_token"] = tracking_token
        return redirect(url_for("order_confirmation", token=tracking_token))

    return render_template(
        "checkout.html", items=items, subtotal=subtotal_display
    )


@app.route("/checkout/complete/<token>")
def order_confirmation(token):
    """Post-checkout page: tracking link and redirect to external payment."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        abort(404)
    return render_template(
        "order_confirmation.html",
        order=order,
        itemsatis_url=app.config["ITEMSATIS_REDIRECT_URL"],
    )


@app.route("/track/<token>")
def track_order(token):
    """Public order status by secret tracking token (from confirmation email / page)."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        flash("We could not find an order for this link.", "error")
        return redirect(url_for("index"))
    return render_template("track_order.html", order=order)


# -----------------------------------------------------------------------------
# Admin
# -----------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        u_ok = secrets.compare_digest(
            (username or "").encode("utf-8"),
            (ADMIN_USERNAME or "").encode("utf-8"),
        )
        p_ok = secrets.compare_digest(
            (password or "").encode("utf-8"),
            (ADMIN_PASSWORD or "").encode("utf-8"),
        )
        if u_ok and p_ok:
            # Single-owner admin; mark admin flags in session
            session["is_admin"] = True
            session["admin"] = True
            session["user"] = {"email": ADMIN_USERNAME, "role": "admin"}
            flash("Admin login successful.", "success")
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin credentials.", "error")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    guard = require_admin()
    if guard:
        return guard
    all_products = store.list_all_products()
    total_products = len(all_products)
    active_products = len([p for p in all_products if p.get("status") == "active"])
    inactive_products = total_products - active_products
    total_orders = store.count_orders()
    analytics = store.get_analytics_for_admin()
    return render_template(
        "admin_dashboard.html",
        products=all_products,
        total_products=total_products,
        active_products=active_products,
        inactive_products=inactive_products,
        total_orders=total_orders,
        categories=store.list_categories(),
        analytics=analytics,
    )


@app.route("/admin/listings")
def admin_listings():
    guard = require_admin()
    if guard:
        return guard
    # Basic search and filtering for marketplace-style management
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    category = (request.args.get("category") or "").strip().lower()
    lang = (request.args.get("lang") or "").strip().lower()

    filtered = store.list_all_products()
    if q:
        filtered = [
            p
            for p in filtered
            if q in p.get("name", "").lower()
            or q in p.get("description", "").lower()
        ]
    if status:
        filtered = [
            p
            for p in filtered
            if p.get("status", "").lower() == status
        ]
    if category:
        filtered = [
            p
            for p in filtered
            if p.get("category", "").lower() == category
        ]
    if lang:
        filtered = [
            p
            for p in filtered
            if (p.get("listing_language") or "en").lower() == lang
        ]

    categories = sorted(
        {p.get("category", "") for p in store.list_all_products() if p.get("category")}
    )

    return render_template(
        "admin_listings.html",
        products=filtered,
        categories=categories,
        current_q=q,
        current_status=status,
        current_category=category,
        current_lang=lang,
    )


@app.route("/admin/categories", methods=["GET"])
def admin_categories():
    guard = require_admin()
    if guard:
        return guard
    return render_template(
        "admin_categories.html", categories=store.list_categories()
    )


@app.route("/admin/create-category", methods=["POST"])
def admin_create_category():
    guard = require_admin()
    if guard:
        return guard
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("admin_categories"))
    cats = store.list_categories()
    if any(c["name"].lower() == name.lower() for c in cats):
        flash("A category with this name already exists.", "error")
        return redirect(url_for("admin_categories"))
    base_slug = slugify(name)
    slug = base_slug
    existing_slugs = store.category_slugs_excluding()
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    store.create_category(name, slug, None)
    flash("Category created.", "success")
    return redirect(url_for("admin_categories"))


@app.route("/admin/delete-category/<int:category_id>", methods=["POST"])
def admin_delete_category(category_id):
    guard = require_admin()
    if guard:
        return guard
    category = store.get_category(category_id)
    if not category:
        flash("Category not found.", "error")
        return redirect(url_for("admin_categories"))
    store.delete_category(category_id)
    flash("Category deleted.", "info")
    return redirect(url_for("admin_categories"))


@app.route("/admin/edit-category/<int:category_id>", methods=["GET", "POST"])
def admin_edit_category(category_id):
    """Edit an existing category name and optional parent."""
    guard = require_admin()
    if guard:
        return guard
    category = store.get_category(category_id)
    if not category:
        flash("Category not found.", "error")
        return redirect(url_for("admin_categories"))

    all_cats = store.list_categories()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        parent_id_raw = (request.form.get("parent_id") or "").strip()
        if not name:
            flash("Category name is required.", "error")
            return redirect(url_for("admin_edit_category", category_id=category_id))
        if any(
            c["name"].lower() == name.lower() and c["id"] != category_id
            for c in all_cats
        ):
            flash("Another category with this name already exists.", "error")
            return redirect(url_for("admin_edit_category", category_id=category_id))

        parent_id = None
        if parent_id_raw:
            try:
                parent_id_val = int(parent_id_raw)
            except ValueError:
                parent_id_val = None
            if parent_id_val and any(c["id"] == parent_id_val for c in all_cats):
                parent_id = parent_id_val

        base_slug = slugify(name)
        slug = base_slug
        existing_slugs = store.category_slugs_excluding(category_id)
        counter = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1

        store.update_category(category_id, name, slug, parent_id)
        flash("Category updated.", "success")
        return redirect(url_for("admin_categories"))

    return render_template(
        "admin_category_edit.html",
        category=category,
        categories=all_cats,
    )


@app.route("/admin/content", methods=["GET", "POST"])
def admin_content():
    guard = require_admin()
    if guard:
        return guard
    if request.method == "POST":
        updates = {}
        for key in store.SITE_CONTENT_KEYS:
            if key in request.form:
                updates[key] = request.form.get(key, "").strip()
        if updates:
            store.save_site_content(updates)
        flash("Content updated.", "success")
        return redirect(url_for("admin_content"))
    return render_template("admin_content.html")


@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    guard = require_admin()
    if guard:
        return guard
    if request.method == "POST":
        itemsatis_url = (request.form.get("itemsatis_redirect_url") or "").strip()
        support_email = (request.form.get("support_email") or "").strip()
        support_discord = (request.form.get("support_discord") or "").strip()

        cur = store.load_site_settings(
            app.config["ITEMSATIS_REDIRECT_URL"],
            "contact@phoenixmarket.example",
            "YourDiscordTag#0000",
        )
        merged_email = support_email or cur.get("support_email", "")
        merged_discord = support_discord or cur.get("support_discord", "")
        merged_itemsatis = itemsatis_url or cur.get("itemsatis_redirect_url", "")

        store.save_site_settings(merged_itemsatis, merged_email, merged_discord)
        if merged_itemsatis:
            app.config["ITEMSATIS_REDIRECT_URL"] = merged_itemsatis

        flash("Settings updated.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html")


@app.route("/admin/listings/new", methods=["GET", "POST"])
def admin_new_listing():
    guard = require_admin()
    if guard:
        return guard

    cat_rows = store.list_categories()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price_raw = request.form.get("base_price", "").strip()
        base_currency = (
            request.form.get("base_currency", "USD").strip().upper() or "USD"
        )
        if base_currency not in ("TRY", "USD", "EUR"):
            base_currency = "USD"
        listing_lang = (
            request.form.get("listing_language", "en").strip().lower() or "en"
        )
        if listing_lang not in ("tr", "en"):
            listing_lang = "en"
        category_name = request.form.get("category", "").strip() or "General"
        image_file = request.files.get("image_file")
        stock_raw = request.form.get("stock", "").strip() or "0"
        status = request.form.get("status", "active").strip() or "active"

        if not name or not description or not price_raw:
            flash("Name, description and base price are required.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=None,
                categories=cat_rows,
            )

        try:
            base_price = float(price_raw)
        except ValueError:
            flash("Base price must be a number.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=None,
                categories=cat_rows,
            )

        try:
            stock = max(0, int(stock_raw))
        except ValueError:
            stock = 0

        if image_file and not allowed_image_file(image_file.filename):
            flash("Invalid image type. Please upload PNG, JPG, JPEG, or WEBP.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=None,
                categories=cat_rows,
            )

        image_path = process_listing_image(image_file)

        base_slug = slugify(name)
        slug = base_slug
        existing_slugs = store.list_existing_slugs()
        counter = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1

        store.create_product(
            {
                "name": name,
                "description": description,
                "base_price": base_price,
                "base_currency": base_currency,
                "listing_language": listing_lang,
                "category": category_name,
                "image_path": image_path or "",
                "stock": stock,
                "status": status,
                "slug": slug,
                "owner": (session.get("user") or {}).get("email", "admin"),
            }
        )
        flash("Listing created.", "success")
        return redirect(url_for("admin_listings"))

    return render_template(
        "admin_listing_form.html",
        mode="new",
        product=None,
        categories=cat_rows,
    )


@app.route("/admin/create-listing", methods=["GET", "POST"])
def admin_create_listing():
    """Alias route required by spec; delegates to admin_new_listing."""
    return admin_new_listing()


@app.route("/admin/listings/<int:product_id>/edit", methods=["GET", "POST"])
def admin_edit_listing(product_id):
    guard = require_admin()
    if guard:
        return guard

    product = get_product(product_id)
    if not product:
        flash("Listing not found.", "error")
        return redirect(url_for("admin_listings"))

    cat_rows = store.list_categories()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price_raw = request.form.get("base_price", "").strip()
        base_currency = (
            request.form.get("base_currency", "USD").strip().upper() or "USD"
        )
        if base_currency not in ("TRY", "USD", "EUR"):
            base_currency = "USD"
        listing_lang = (
            request.form.get("listing_language", "en").strip().lower() or "en"
        )
        if listing_lang not in ("tr", "en"):
            listing_lang = "en"
        category_name = request.form.get("category", "").strip() or product["category"]
        image_file = request.files.get("image_file")
        stock_raw = request.form.get("stock", "").strip()
        status = request.form.get("status", "active").strip() or product["status"]

        if not name or not description or not price_raw:
            flash("Name, description and base price are required.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product=product,
                categories=cat_rows,
            )

        try:
            base_price = float(price_raw)
        except ValueError:
            flash("Base price must be a number.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product=product,
                categories=cat_rows,
            )

        try:
            stock = max(0, int(stock_raw)) if stock_raw else product.get("stock", 0)
        except ValueError:
            stock = product.get("stock", 0)

        if image_file and not allowed_image_file(image_file.filename):
            flash("Invalid image type. Please upload PNG, JPG, JPEG, or WEBP.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product=product,
                categories=cat_rows,
            )

        name_changed = name != product.get("name")
        update_data = {
            "name": name,
            "description": description,
            "base_price": base_price,
            "base_currency": base_currency,
            "listing_language": listing_lang,
            "category": category_name,
            "stock": stock,
            "status": status,
        }
        new_image_path = process_listing_image(image_file)
        if new_image_path:
            update_data["image_path"] = new_image_path
        if name_changed:
            base_slug = slugify(name)
            slug = base_slug
            existing_slugs = store.list_existing_slugs(product_id)
            counter = 2
            while slug in existing_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            update_data["slug"] = slug
        store.update_product(product_id, update_data)

        flash("Listing updated.", "success")
        return redirect(url_for("admin_listings"))

    return render_template(
        "admin_listing_form.html",
        mode="edit",
        product=product,
        categories=cat_rows,
    )


@app.route("/admin/listings/edit/<int:product_id>", methods=["GET", "POST"])
def admin_edit_listing_alt(product_id):
    """Alternate route to match /admin/listings/edit/<id> pattern."""
    return admin_edit_listing(product_id)


@app.route("/admin/listings/<int:product_id>/delete", methods=["POST"])
def admin_delete_listing(product_id):
    guard = require_admin()
    if guard:
        return guard

    product = get_product(product_id)
    if not product:
        flash("Listing not found.", "error")
        return redirect(url_for("admin_listings"))

    store.delete_product(product_id)
    flash("Listing deleted.", "info")
    return redirect(url_for("admin_listings"))


@app.route("/admin/edit-listing/<int:product_id>", methods=["GET", "POST"])
def admin_edit_listing_spec(product_id):
    """Alias route required by spec; delegates to admin_edit_listing."""
    return admin_edit_listing(product_id)


@app.route("/admin/delete-listing/<int:product_id>", methods=["POST"])
def admin_delete_listing_spec(product_id):
    """Alias route required by spec; delegates to admin_delete_listing."""
    return admin_delete_listing(product_id)


@app.route("/admin/orders")
def admin_orders():
    guard = require_admin()
    if guard:
        return guard
    orders = store.list_orders_admin(500)
    return render_template("admin_orders.html", orders=orders)


@app.route("/admin/orders/<int:order_id>", methods=["GET", "POST"])
def admin_order_detail(order_id):
    guard = require_admin()
    if guard:
        return guard
    order = store.get_order_by_id(order_id, include_items=True)
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for("admin_orders"))
    if request.method == "POST":
        status = (request.form.get("fulfillment_status") or "").strip().lower()
        if status not in ORDER_STATUS_CHOICES:
            status = order["fulfillment_status"]
        tracking_number = (request.form.get("tracking_number") or "").strip() or None
        carrier_note = (request.form.get("carrier_note") or "").strip() or None
        store.update_order_fulfillment(
            order_id,
            fulfillment_status=status,
            tracking_number=tracking_number,
            carrier_note=carrier_note,
        )
        flash("Order updated.", "success")
        return redirect(url_for("admin_order_detail", order_id=order_id))
    return render_template(
        "admin_order_detail.html",
        order=order,
        status_options=ORDER_STATUS_CHOICES,
    )


@app.post("/api/chat")
def api_chat():
    """Server-side AI chat endpoint.

    - Reads OPENAI_API_KEY from environment (see .env).
    - Never exposes the key to the browser.
    - If no key is configured, returns a clear 503 error.
    """

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()

    if not user_message:
        return jsonify({"error": "Message is required."}), 400

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key == "YOUR_API_KEY_HERE":
        # Frontend will gracefully fall back to FAQ answers.
        return (
            jsonify(
                {
                    "error": "AI chat is not configured on the server. Please add OPENAI_API_KEY in .env."
                }
            ),
            503,
        )

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the PHOENIXMARKET customer support assistant. "
                        "Help users with sign-in, sign-up, cart, checkout, orders, account, and general marketplace questions. "
                        "Be concise, professional, and use clear steps. Do not discuss internal implementation details."
                    ),
                },
                {"role": "user", "content": user_message},
            ],
        )
        answer = completion.choices[0].message.content.strip()
        return jsonify({"answer": answer})
    except Exception as exc:  # noqa: BLE001
        # Avoid leaking internal error details to the client.
        print(f"AI chat error: {exc}")  # log server-side
        return (
            jsonify(
                {
                    "error": "There was a problem contacting the AI assistant. Please try again later."
                }
            ),
            502,
        )


@app.errorhandler(404)
def not_found(error):  # noqa: D401, ARG001
    """Handle 404 errors with a friendly marketplace page."""
    return render_template("404.html"), 404


if __name__ == "__main__":
    # Debug is disabled by default; enable explicitly with FLASK_DEBUG=1
    debug_flag = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_flag)
