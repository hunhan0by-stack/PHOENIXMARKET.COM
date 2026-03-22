import os
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
from werkzeug.utils import secure_filename

load_dotenv()

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Base configuration – environment driven for production readiness
# -----------------------------------------------------------------------------
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")
app_root = Path(__file__).resolve().parent

UPLOAD_FOLDER = app_root / "static" / "uploads"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

ITEMSATIS_REDIRECT_URL = os.getenv("ITEMSATIS_REDIRECT_URL", "[YOUR_ITEMSATIS_LINK]")
app.config["ITEMSATIS_REDIRECT_URL"] = ITEMSATIS_REDIRECT_URL

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "phoenixmarket")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


# -----------------------------------------------------------------------------
# In-memory data (acts as a simple data layer for now)
# -----------------------------------------------------------------------------
PRODUCTS = [
    {
        "id": 1,
        "name": "Canva Pro Subscription",
        "price": 9.99,
        "description": "Premium Canva Pro access with templates and collaboration tools.",
        "category": "Design",
        "image": "/static/images/canva.jpg",
        "stock": 100,
        "status": "active",
        "owner": "admin",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "id": 2,
        "name": "Instagram Followers",
        "price": 14.99,
        "description": "Boost your Instagram presence with targeted follower packages.",
        "category": "Social Media",
        "image": "/static/images/instagram.jpg",
        "stock": 200,
        "status": "active",
        "owner": "admin",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "id": 3,
        "name": "TikTok Views",
        "price": 12.99,
        "description": "Increase your TikTok views for better reach and engagement.",
        "category": "Social Media",
        "image": "/static/images/tiktok.jpg",
        "stock": 150,
        "status": "active",
        "owner": "admin",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
    {
        "id": 4,
        "name": "ChatGPT Accounts",
        "price": 19.99,
        "description": "Reliable ChatGPT access for research, coding and content.",
        "category": "AI",
        "image": "/static/images/chatgpt.jpg",
        "stock": 80,
        "status": "active",
        "owner": "admin",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    },
]

# Simple in-memory order storage for checkout-ready flow
ORDERS = []

# In-memory category storage; seeded from existing products
def slugify(value: str) -> str:
    """Generate a simple, SEO‑friendly slug."""
    import re

    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "item"


# Seed products with SEO‑friendly slugs
for p in PRODUCTS:
    if "slug" not in p or not p["slug"]:
        p["slug"] = slugify(p["name"])


CATEGORIES = []
for p in PRODUCTS:
    name = p.get("category")
    if not name:
        continue
    if not any(c["name"] == name for c in CATEGORIES):
        base_slug = slugify(name)
        slug = base_slug
        existing_slugs = {c["slug"] for c in CATEGORIES}
        counter = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        CATEGORIES.append(
            {
                "id": len(CATEGORIES) + 1,
                "name": name,
                "slug": slug,
                "parent_id": None,
                "created_at": datetime.now(timezone.utc),
            }
        )


# In-memory editable site content (lightweight CMS)
SITE_CONTENT = {
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


SITE_SETTINGS = {
    "itemsatis_redirect_url": app.config["ITEMSATIS_REDIRECT_URL"],
    "support_email": "contact@phoenixmarket.example",
    "support_discord": "YourDiscordTag#0000",
}


def get_product(product_id):
    return next((p for p in PRODUCTS if p["id"] == product_id), None)


def get_product_by_slug(slug: str):
    return next((p for p in PRODUCTS if p.get("slug") == slug), None)


def get_category_by_slug(slug: str):
    return next((c for c in CATEGORIES if c.get("slug") == slug), None)


def get_cart():
    return session.setdefault("cart", {})


def build_cart_items(cart_data):
    """Return structured cart line items and subtotal from a raw cart dict."""
    items = []
    subtotal = 0.0
    for pid_str, qty in cart_data.items():
        try:
            product_id = int(pid_str)
        except (TypeError, ValueError):
            continue
        product = get_product(product_id)
        if not product:
            continue
        line_total = product["price"] * qty
        subtotal += line_total
        items.append({"product": product, "qty": qty, "line_total": line_total})
    return items, subtotal


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


@app.context_processor
def inject_globals():
    """Inject common marketplace-wide data into templates."""
    cart = session.get("cart") or {}
    cart_count = sum(cart.values())
    return {
        "cart_count": cart_count,
        "site_content": SITE_CONTENT,
        "site_settings": SITE_SETTINGS,
    }


def get_public_products():
    """Return products that are visible in the public marketplace."""
    return [p for p in PRODUCTS if p.get("status") == "active"]


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
        categories=CATEGORIES,
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
    flash("Signed out.", "success")
    return redirect(url_for("index"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin", None)
    flash("Admin logged out.", "success")
    return redirect(url_for("index"))


@app.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@app.route("/account")
def account():
    user = session.get("user")
    if not user:
        return redirect(url_for("signin"))
    return render_template("account.html", user=user)


# -----------------------------------------------------------------------------
# Cart and checkout
# -----------------------------------------------------------------------------
@app.route("/cart")
def cart():
    cart_data = get_cart()
    items, subtotal = build_cart_items(cart_data)
    return render_template("cart.html", items=items, subtotal=subtotal)


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

    items, subtotal = build_cart_items(cart_data)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()

        if not full_name or not email:
            flash("Full name and email are required to place an order.", "error")
            return render_template("checkout.html", items=items, subtotal=subtotal)

        order_id = len(ORDERS) + 1
        now = datetime.now(timezone.utc)
        order_items = [
            {
                "product_id": item["product"]["id"],
                "quantity": item["qty"],
                "price": item["product"]["price"],
            }
            for item in items
        ]
        ORDERS.append(
            {
                "id": order_id,
                "user_email": email,
                "full_name": full_name,
                "total_price": subtotal,
                "items": order_items,
                "created_at": now,
            }
        )
        session["cart"] = {}
        session["last_order_id"] = order_id
        flash(
            "Order created successfully. You are being redirected to our official itemsatis listing to complete payment.",
            "success",
        )
        return redirect(app.config["ITEMSATIS_REDIRECT_URL"])

    return render_template("checkout.html", items=items, subtotal=subtotal)


# -----------------------------------------------------------------------------
# Admin (mock)
# -----------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
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
    total_products = len(PRODUCTS)
    active_products = len([p for p in PRODUCTS if p.get("status") == "active"])
    inactive_products = total_products - active_products
    total_orders = len(ORDERS)
    return render_template(
        "admin_dashboard.html",
        products=PRODUCTS,
        total_products=total_products,
        active_products=active_products,
        inactive_products=inactive_products,
        total_orders=total_orders,
        categories=CATEGORIES,
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

    filtered = PRODUCTS
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

    categories = sorted({p.get("category", "") for p in PRODUCTS if p.get("category")})

    return render_template(
        "admin_listings.html",
        products=filtered,
        categories=categories,
        current_q=q,
        current_status=status,
        current_category=category,
    )


@app.route("/admin/categories", methods=["GET"])
def admin_categories():
    guard = require_admin()
    if guard:
        return guard
    return render_template("admin_categories.html", categories=CATEGORIES)


@app.route("/admin/create-category", methods=["POST"])
def admin_create_category():
    guard = require_admin()
    if guard:
        return guard
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Category name is required.", "error")
        return redirect(url_for("admin_categories"))
    if any(c["name"].lower() == name.lower() for c in CATEGORIES):
        flash("A category with this name already exists.", "error")
        return redirect(url_for("admin_categories"))
    base_slug = slugify(name)
    slug = base_slug
    existing_slugs = {c["slug"] for c in CATEGORIES}
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    CATEGORIES.append(
        {
            "id": len(CATEGORIES) + 1,
            "name": name,
            "slug": slug,
            "parent_id": None,
            "created_at": datetime.now(timezone.utc),
        }
    )
    flash("Category created.", "success")
    return redirect(url_for("admin_categories"))


@app.route("/admin/delete-category/<int:category_id>", methods=["POST"])
def admin_delete_category(category_id):
    guard = require_admin()
    if guard:
        return guard
    category = next((c for c in CATEGORIES if c["id"] == category_id), None)
    if not category:
        flash("Category not found.", "error")
        return redirect(url_for("admin_categories"))
    CATEGORIES.remove(category)
    flash("Category deleted.", "info")
    return redirect(url_for("admin_categories"))


@app.route("/admin/edit-category/<int:category_id>", methods=["GET", "POST"])
def admin_edit_category(category_id):
    """Edit an existing category name and optional parent."""
    guard = require_admin()
    if guard:
        return guard
    category = next((c for c in CATEGORIES if c["id"] == category_id), None)
    if not category:
        flash("Category not found.", "error")
        return redirect(url_for("admin_categories"))

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        parent_id_raw = (request.form.get("parent_id") or "").strip()
        if not name:
            flash("Category name is required.", "error")
            return redirect(url_for("admin_edit_category", category_id=category_id))
        if any(
            c["name"].lower() == name.lower() and c["id"] != category_id
            for c in CATEGORIES
        ):
            flash("Another category with this name already exists.", "error")
            return redirect(url_for("admin_edit_category", category_id=category_id))

        parent_id = None
        if parent_id_raw:
            try:
                parent_id_val = int(parent_id_raw)
            except ValueError:
                parent_id_val = None
            if parent_id_val and any(c["id"] == parent_id_val for c in CATEGORIES):
                parent_id = parent_id_val

        category["name"] = name
        category["parent_id"] = parent_id

        base_slug = slugify(name)
        slug = base_slug
        existing_slugs = {c["slug"] for c in CATEGORIES if c["id"] != category_id}
        counter = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        category["slug"] = slug

        flash("Category updated.", "success")
        return redirect(url_for("admin_categories"))

    return render_template(
        "admin_category_edit.html",
        category=category,
        categories=CATEGORIES,
    )


@app.route("/admin/content", methods=["GET", "POST"])
def admin_content():
    guard = require_admin()
    if guard:
        return guard
    if request.method == "POST":
        for key in [
            "hero_badge",
            "hero_title",
            "hero_subtitle",
            "hero_cta_label",
            "checkout_helper_text",
            "checkout_button_label",
            "footer_contact_text",
            "footer_discord_text",
        ]:
            if key in request.form:
                SITE_CONTENT[key] = request.form.get(key, "").strip()
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

        if itemsatis_url:
            SITE_SETTINGS["itemsatis_redirect_url"] = itemsatis_url
            app.config["ITEMSATIS_REDIRECT_URL"] = itemsatis_url
        SITE_SETTINGS["support_email"] = support_email or SITE_SETTINGS["support_email"]
        SITE_SETTINGS["support_discord"] = support_discord or SITE_SETTINGS["support_discord"]

        flash("Settings updated.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html")


@app.route("/admin/listings/new", methods=["GET", "POST"])
def admin_new_listing():
    guard = require_admin()
    if guard:
        return guard

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price_raw = request.form.get("price", "").strip()
        category_name = request.form.get("category", "").strip() or "General"
        image_file = request.files.get("image_file")
        stock_raw = request.form.get("stock", "").strip() or "0"
        status = request.form.get("status", "active").strip() or "active"

        if not name or not description or not price_raw:
            flash("Name, description and price are required.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=None,
                categories=CATEGORIES,
            )

        try:
            price = float(price_raw)
        except ValueError:
            flash("Price must be a number.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=None,
                categories=CATEGORIES,
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
                categories=CATEGORIES,
            )

        image_path = process_listing_image(image_file)

        # Generate unique slug
        base_slug = slugify(name)
        slug = base_slug
        existing_slugs = {p.get("slug") for p in PRODUCTS}
        counter = 2
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1

        new_id = max((p["id"] for p in PRODUCTS), default=0) + 1
        now = datetime.now(timezone.utc)
        PRODUCTS.append(
            {
                "id": new_id,
                "name": name,
                "description": description,
                "price": price,
                "category": category_name,
                "image_path": image_path,
                "stock": stock,
                "status": status,
                 "slug": slug,
                "owner": (session.get("user") or {}).get("email", "admin"),
                "created_at": now,
                "updated_at": now,
            }
        )
        flash("Listing created.", "success")
        return redirect(url_for("admin_listings"))

    return render_template(
        "admin_listing_form.html",
        mode="new",
        product=None,
        categories=CATEGORIES,
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

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        price_raw = request.form.get("price", "").strip()
        category_name = request.form.get("category", "").strip() or product["category"]
        image_file = request.files.get("image_file")
        stock_raw = request.form.get("stock", "").strip()
        status = request.form.get("status", "active").strip() or product["status"]

        if not name or not description or not price_raw:
            flash("Name, description and price are required.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product=product,
                categories=CATEGORIES,
            )

        try:
            price = float(price_raw)
        except ValueError:
            flash("Price must be a number.", "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product=product,
                categories=CATEGORIES,
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
                categories=CATEGORIES,
            )

        # If the title changes, refresh slug while keeping it unique.
        name_changed = name != product.get("name")

        product["name"] = name
        product["description"] = description
        product["price"] = price
        product["category"] = category_name
        new_image_path = process_listing_image(image_file)
        if new_image_path:
            product["image_path"] = new_image_path
        product["stock"] = stock
        product["status"] = status
        if name_changed:
            base_slug = slugify(name)
            slug = base_slug
            existing_slugs = {p.get("slug") for p in PRODUCTS if p["id"] != product["id"]}
            counter = 2
            while slug in existing_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            product["slug"] = slug
        product["updated_at"] = datetime.now(timezone.utc)

        flash("Listing updated.", "success")
        return redirect(url_for("admin_listings"))

    return render_template(
        "admin_listing_form.html",
        mode="edit",
        product=product,
        categories=CATEGORIES,
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

    PRODUCTS.remove(product)
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
