import os
import sys
import re
import time
import json
import urllib.parse
import sqlite3
import argparse
import requests
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup

# Base website URL
BASE_URL = "https://www.startech.com.bd"

# Headers to impersonate a real browser
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Referer": BASE_URL
}

# Directory for storing pictures
PICS_DIR = "pics"


# ═══════════════════════════════════════════════════════════════════
#  DATABASE INITIALIZATION & MIGRATION
# ═══════════════════════════════════════════════════════════════════

def init_db(db_path="startech.db"):
    """Initializes the SQLite database with hierarchical category support."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON;")

    # ── Categories tree table ──
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        parent_id INTEGER,
        level INTEGER DEFAULT 0,
        FOREIGN KEY(parent_id) REFERENCES categories(id)
    );
    """)

    # ── Products table (original + new hierarchical columns) ──
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        category TEXT,
        main_category TEXT,
        sub_category TEXT,
        sub_sub_category TEXT,
        category_id INTEGER,
        price INTEGER,
        old_price INTEGER,
        status TEXT,
        product_code TEXT,
        brand TEXT,
        image_path TEXT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(category_id) REFERENCES categories(id)
    );
    """)

    # ── Product specifications ──
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS product_specs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        spec_group TEXT,
        spec_name TEXT NOT NULL,
        spec_value TEXT NOT NULL,
        FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
    );
    """)

    # ── Scrape progress tracker ──
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scrape_progress (
        category_url TEXT PRIMARY KEY,
        last_page INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()

    # Run migrations for existing databases that lack the new columns
    _migrate_schema(conn)

    return conn


def _migrate_schema(conn):
    """Add new columns to products table if they don't exist (safe for existing DBs)."""
    cursor = conn.cursor()

    # Get existing column names
    cursor.execute("PRAGMA table_info(products)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    new_cols = {
        "main_category": "TEXT",
        "sub_category": "TEXT",
        "sub_sub_category": "TEXT",
        "category_id": "INTEGER",
    }

    for col_name, col_type in new_cols.items():
        if col_name not in existing_cols:
            print(f"[*] Migrating: adding column '{col_name}' to products table")
            cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")

    conn.commit()


# ═══════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def get_soup(url):
    """Sends a GET request and returns a BeautifulSoup object."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return BeautifulSoup(response.text, 'html.parser')
        else:
            print(f"[-] Error fetching {url} (Status: {response.status_code})")
    except Exception as e:
        print(f"[-] Request failed for {url}: {e}")
    return None


def parse_price(price_str):
    """Extracts integer value from pricing string containing currency symbol and commas."""
    if not price_str:
        return None
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else None


def download_image(url, product_id, skip_images=False):
    """Downloads an image from a URL and saves it to the pics folder, returning the local path."""
    if not url or skip_images:
        return None

    if not os.path.exists(PICS_DIR):
        os.makedirs(PICS_DIR)

    try:
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE_URL + url

        parsed_url = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"

        filename = f"product_{product_id}{ext}"
        filepath = os.path.join(PICS_DIR, filename)

        # Skip if image already downloaded
        if os.path.exists(filepath):
            return filepath

        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
            return filepath
        else:
            print(f"[-] Failed to download image from {url} (Status: {response.status_code})")
    except Exception as e:
        print(f"[-] Error downloading image {url}: {e}")

    return None


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1: CATEGORY TREE BUILDER
# ═══════════════════════════════════════════════════════════════════

def build_category_tree(conn):
    """Fetches the Star Tech homepage, parses the nav menu, and populates the categories table.
    Returns a dict mapping category_url -> {id, name, parent_id, level, path}."""

    print(f"[*] Fetching homepage to build category tree: {BASE_URL}")
    soup = get_soup(BASE_URL)
    if not soup:
        print("[-] Critical Error: Failed to fetch Star Tech homepage.")
        sys.exit(1)

    nav = soup.find("nav", class_="navbar") or soup.find("nav", id="main-nav")
    if not nav:
        print("[-] Critical Error: Could not find navigation menu.")
        sys.exit(1)

    navbar_ul = nav.find("ul", class_="navbar-nav")
    if not navbar_ul:
        print("[-] Critical Error: Could not find navbar-nav.")
        sys.exit(1)

    cursor = conn.cursor()
    url_to_cat = {}

    # Skip patterns — these are utility pages, not product categories
    SKIP_PATTERNS = ["/account/", "/checkout/", "/cart", "/tool/", "/information/",
                     "/compare", "route=common", "javascript:"]

    def should_skip(href):
        return any(pat in href for pat in SKIP_PATTERNS)

    def normalize_url(href):
        if not href.startswith("http"):
            href = urllib.parse.urljoin(BASE_URL, href)
        return href.rstrip("/")

    def parse_menu_level(ul_elem, parent_id, level, parent_path):
        """Recursively parse a <ul> or <div> menu level."""
        if not ul_elem:
            return

        for li in ul_elem.find_all("li", recursive=False):
            a = li.find("a", recursive=False)
            if not a or not a.get("href"):
                continue

            name = a.get_text(strip=True)
            href = normalize_url(a["href"])

            # Skip utility/action links and "Show All" links
            if should_skip(href):
                continue
            if "see-all" in (a.get("class") or []):
                continue
            if not name:
                continue

            # Insert or get category
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO categories (name, url, parent_id, level)
                    VALUES (?, ?, ?, ?)
                """, (name, href, parent_id, level))
                conn.commit()

                cursor.execute("SELECT id FROM categories WHERE url = ?", (href,))
                row = cursor.fetchone()
                if not row:
                    continue
                cat_id = row[0]
            except sqlite3.Error as e:
                print(f"[-] DB error inserting category '{name}': {e}")
                continue

            current_path = parent_path + [name]
            url_to_cat[href] = {
                "id": cat_id,
                "name": name,
                "parent_id": parent_id,
                "level": level,
                "path": current_path
            }

            # Check for sub-menu: <ul> or <div class="drop-down">
            sub_menu = li.find("ul", recursive=False) or li.find("div", class_="drop-down", recursive=False)

            if sub_menu:
                if sub_menu.name == "div":
                    # Multi-column menus: <div> wrapping multiple <ul>s
                    for inner_ul in sub_menu.find_all("ul", recursive=False):
                        for inner_li in inner_ul.find_all("li", recursive=False):
                            inner_a = inner_li.find("a", recursive=False)
                            if not inner_a or not inner_a.get("href"):
                                continue
                            inner_name = inner_a.get_text(strip=True)
                            inner_href = normalize_url(inner_a["href"])
                            if should_skip(inner_href) or not inner_name:
                                continue
                            try:
                                cursor.execute("""
                                    INSERT OR IGNORE INTO categories (name, url, parent_id, level)
                                    VALUES (?, ?, ?, ?)
                                """, (inner_name, inner_href, cat_id, level + 1))
                                conn.commit()
                                cursor.execute("SELECT id FROM categories WHERE url = ?", (inner_href,))
                                inner_row = cursor.fetchone()
                                if inner_row:
                                    inner_cat_id = inner_row[0]
                                    url_to_cat[inner_href] = {
                                        "id": inner_cat_id,
                                        "name": inner_name,
                                        "parent_id": cat_id,
                                        "level": level + 1,
                                        "path": current_path + [inner_name]
                                    }
                            except sqlite3.Error:
                                pass
                else:
                    # Normal nested <ul>
                    parse_menu_level(sub_menu, cat_id, level + 1, current_path)

    parse_menu_level(navbar_ul, None, 0, [])

    # Count results
    cursor.execute("SELECT COUNT(*) FROM categories")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM categories WHERE id NOT IN (SELECT DISTINCT parent_id FROM categories WHERE parent_id IS NOT NULL)")
    leaves = cursor.fetchone()[0]

    print(f"[+] Category tree built: {total} total nodes, {leaves} leaf categories")

    return url_to_cat


def get_leaf_categories(conn):
    """Returns all leaf categories (categories with no children) ordered for scraping."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, c.url, c.level, c.parent_id
        FROM categories c
        WHERE c.id NOT IN (
            SELECT DISTINCT parent_id FROM categories WHERE parent_id IS NOT NULL
        )
        ORDER BY c.level ASC, c.id ASC
    """)
    return cursor.fetchall()


def get_category_path(conn, category_id):
    """Walks up the parent chain to build the full category path as a list."""
    cursor = conn.cursor()
    path = []
    current_id = category_id
    while current_id is not None:
        cursor.execute("SELECT id, name, parent_id FROM categories WHERE id = ?", (current_id,))
        row = cursor.fetchone()
        if not row:
            break
        path.insert(0, row[1])  # prepend name
        current_id = row[2]  # parent_id
    return path


# ═══════════════════════════════════════════════════════════════════
#  PHASE 2: PRODUCT SCRAPING
# ═══════════════════════════════════════════════════════════════════

def extract_breadcrumb(soup):
    """Extract category hierarchy from the product page's breadcrumb.

    Star Tech breadcrumbs follow: Home > MainCat > SubCat > SubSubCat > ProductName
    We extract up to 3 category levels (ignoring Home and the product name itself).

    Returns dict with main_category, sub_category, sub_sub_category, breadcrumb_path.
    """
    result = {
        "main_category": None,
        "sub_category": None,
        "sub_sub_category": None,
        "breadcrumb_path": ""
    }

    breadcrumb_ul = soup.find("ul", class_="breadcrumb")
    if not breadcrumb_ul:
        return result

    items = breadcrumb_ul.find_all("li")
    # Collect breadcrumb entries (skipping Home icon and the product name at the end)
    crumbs = []
    for li in items:
        # Skip the home link (has no itemprop)
        if not li.get("itemprop") and not li.find(attrs={"itemprop": "name"}):
            continue
        span = li.find("span", itemprop="name")
        if span:
            crumbs.append(span.get_text(strip=True))

    # Last crumb is the product name itself — exclude it
    if len(crumbs) > 1:
        category_crumbs = crumbs[:-1]  # exclude product name
    else:
        category_crumbs = crumbs

    if len(category_crumbs) >= 1:
        result["main_category"] = category_crumbs[0]
    if len(category_crumbs) >= 2:
        result["sub_category"] = category_crumbs[1]
    if len(category_crumbs) >= 3:
        result["sub_sub_category"] = category_crumbs[2]

    result["breadcrumb_path"] = " > ".join(category_crumbs)

    return result


def scrape_product_details(url, fallback_category_path=None, skip_images_flag=False):
    """Scrapes detailed information from a product page, including breadcrumb hierarchy."""
    print(f"[*] Scraping product details from: {url}")
    soup = get_soup(url)
    if not soup:
        return None

    product = {
        "url": url,
        "category": None,           # legacy flat field
        "main_category": None,
        "sub_category": None,
        "sub_sub_category": None,
        "name": "",
        "price": None,
        "old_price": None,
        "status": "",
        "product_code": "",
        "brand": "",
        "image_url": None,
        "specs": []
    }

    # ── Breadcrumb extraction (primary source of category hierarchy) ──
    breadcrumb = extract_breadcrumb(soup)
    product["main_category"] = breadcrumb["main_category"]
    product["sub_category"] = breadcrumb["sub_category"]
    product["sub_sub_category"] = breadcrumb["sub_sub_category"]

    # Set legacy flat category field from breadcrumb (use deepest available)
    if breadcrumb["sub_category"]:
        product["category"] = breadcrumb["sub_category"]
    elif breadcrumb["main_category"]:
        product["category"] = breadcrumb["main_category"]

    # Fallback: use the category path from the nav menu if breadcrumb didn't work
    if not product["main_category"] and fallback_category_path:
        if len(fallback_category_path) >= 1:
            product["main_category"] = fallback_category_path[0]
        if len(fallback_category_path) >= 2:
            product["sub_category"] = fallback_category_path[1]
            product["category"] = fallback_category_path[1]
        if len(fallback_category_path) >= 3:
            product["sub_sub_category"] = fallback_category_path[2]

    # ── Product Name ──
    title_elem = soup.find("h1", itemprop="name") or soup.find("h1")
    if title_elem:
        product["name"] = title_elem.get_text(strip=True)

    # ── Key info (Status, Code, Brand) ──
    info_table = soup.find("table", class_="product-info-table")
    if info_table:
        for row in info_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)
                if "status" in label:
                    product["status"] = value
                elif "code" in label:
                    product["product_code"] = value
                elif "brand" in label:
                    product["brand"] = value

    # Fallback for product code
    if not product["product_code"]:
        code_elem = soup.find(text=re.compile(r"Product Code:", re.IGNORECASE))
        if code_elem:
            product["product_code"] = code_elem.find_next().get_text(strip=True)

    # Fallback for brand
    if not product["brand"]:
        brand_elem = soup.find("meta", itemprop="brand")
        if brand_elem:
            product["brand"] = brand_elem.get("content", "").strip()
        else:
            brand_link = soup.find("a", href=re.compile(r"/brand/"))
            if brand_link:
                product["brand"] = brand_link.get_text(strip=True)

    # Fallback for status
    if not product["status"]:
        status_elem = soup.find("td", class_="product-status")
        if status_elem:
            product["status"] = status_elem.get_text(strip=True)

    # ── Pricing ──
    price_elem = soup.find("td", class_="product-price")
    if price_elem:
        ins_elem = price_elem.find("ins")
        del_elem = price_elem.find("del")
        if ins_elem:
            product["price"] = parse_price(ins_elem.get_text(strip=True))
        else:
            product["price"] = parse_price(price_elem.get_text(strip=True))
        if del_elem:
            product["old_price"] = parse_price(del_elem.get_text(strip=True))
    else:
        price_meta = soup.find("meta", itemprop="price")
        if price_meta:
            try:
                product["price"] = int(float(price_meta.get("content", "0")))
            except (ValueError, TypeError):
                pass

    # ── Image URL ──
    img_holder = soup.find("div", class_="product-img-holder")
    if img_holder:
        img_elem = img_holder.find("img", class_="main-img") or img_holder.find("img")
        if img_elem:
            product["image_url"] = img_elem.get("src") or img_elem.get("data-src")

    # ── Full specifications table ──
    specs_tables = soup.find_all("table", class_="data-table")
    for table in specs_tables:
        current_group = "General"
        for row in table.find_all("tr"):
            heading_cell = row.find("td", class_="heading-row")
            if heading_cell:
                current_group = heading_cell.get_text(strip=True)
                continue
            cells = row.find_all("td")
            if len(cells) >= 2:
                name_cell = cells[0].get_text(strip=True)
                value_cell = cells[1].get_text(strip=True)
                if name_cell and value_cell:
                    product["specs"].append({
                        "group": current_group,
                        "name": name_cell,
                        "value": value_cell
                    })

    return product


def save_to_db(conn, product, category_id=None, skip_images=False):
    """Saves the scraped product information and specifications into the database."""
    cursor = conn.cursor()

    try:
        # Check if product already exists
        cursor.execute("SELECT id, image_path FROM products WHERE url = ?", (product["url"],))
        existing = cursor.fetchone()

        if existing:
            product_id = existing[0]
            # Update product with new hierarchical fields
            cursor.execute("""
            UPDATE products
            SET name = ?, category = ?, main_category = ?, sub_category = ?,
                sub_sub_category = ?, category_id = ?,
                price = ?, old_price = ?, status = ?, product_code = ?, brand = ?
            WHERE id = ?
            """, (
                product["name"],
                product["category"],
                product["main_category"],
                product["sub_category"],
                product["sub_sub_category"],
                category_id,
                product["price"],
                product["old_price"],
                product["status"],
                product["product_code"],
                product["brand"],
                product_id
            ))
            print(f"[+] Updated existing product ID {product_id} in database.")
        else:
            # Insert product with hierarchical category info
            cursor.execute("""
            INSERT INTO products (name, url, category, main_category, sub_category,
                                  sub_sub_category, category_id,
                                  price, old_price, status, product_code, brand)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product["name"],
                product["url"],
                product["category"],
                product["main_category"],
                product["sub_category"],
                product["sub_sub_category"],
                category_id,
                product["price"],
                product["old_price"],
                product["status"],
                product["product_code"],
                product["brand"]
            ))
            product_id = cursor.lastrowid
            print(f"[+] Inserted new product ID {product_id} to database.")

        # Download and link image
        if product["image_url"]:
            image_path = download_image(product["image_url"], product_id, skip_images)
            if image_path:
                cursor.execute("UPDATE products SET image_path = ? WHERE id = ?", (image_path, product_id))

        # Clear and re-insert specifications
        cursor.execute("DELETE FROM product_specs WHERE product_id = ?", (product_id,))
        for spec in product["specs"]:
            cursor.execute("""
            INSERT INTO product_specs (product_id, spec_group, spec_name, spec_value)
            VALUES (?, ?, ?, ?)
            """, (product_id, spec["group"], spec["name"], spec["value"]))

        conn.commit()
        return product_id
    except sqlite3.Error as e:
        print(f"[-] Database error while saving {product['name']}: {e}")
        conn.rollback()
    return None


# ═══════════════════════════════════════════════════════════════════
#  PHASE 3: MIGRATION OF EXISTING DATA
# ═══════════════════════════════════════════════════════════════════

def migrate_existing_products(conn, url_to_cat):
    """Backfill main_category/sub_category/sub_sub_category for existing products
    that have the old flat 'category' field but no hierarchical data."""

    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, url, category FROM products
        WHERE main_category IS NULL OR main_category = ''
    """)
    rows = cursor.fetchall()

    if not rows:
        print("[*] No products need migration.")
        return

    print(f"[*] Migrating {len(rows)} existing products to hierarchical categories...")
    migrated = 0

    for product_id, product_url, old_category in rows:
        # Strategy: use old flat category as main_category fallback
        main_cat = old_category
        sub_cat = None
        sub_sub_cat = None
        cat_id = None

        # Try to find the best matching category from the URL-to-category map
        # Check if any known category URL is a prefix of this product's URL
        best_match = None
        best_match_len = 0
        for cat_url, cat_info in url_to_cat.items():
            if product_url.startswith(cat_url) and len(cat_url) > best_match_len:
                best_match = cat_info
                best_match_len = len(cat_url)

        if best_match:
            path = best_match["path"]
            cat_id = best_match["id"]
            if len(path) >= 1:
                main_cat = path[0]
            if len(path) >= 2:
                sub_cat = path[1]
            if len(path) >= 3:
                sub_sub_cat = path[2]

        cursor.execute("""
            UPDATE products
            SET main_category = ?, sub_category = ?, sub_sub_category = ?, category_id = ?
            WHERE id = ?
        """, (main_cat, sub_cat, sub_sub_cat, cat_id, product_id))
        migrated += 1

    conn.commit()
    print(f"[+] Migrated {migrated} products to hierarchical categories.")


# ═══════════════════════════════════════════════════════════════════
#  MAIN SCRAPING LOOP
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Star Tech Bangladesh Product Scraper (v2 - Hierarchical)")
    parser.add_argument("--limit", type=int, default=10,
                        help="Maximum number of NEW products to scrape (set to 0 for unlimited)")
    parser.add_argument("--category", type=str, default=None,
                        help="Scrape only from this category URL or match name")
    parser.add_argument("--delay", type=float, default=1.5,
                        help="Delay in seconds between requests to avoid bans")
    parser.add_argument("--db", type=str, default="startech.db",
                        help="SQLite database file path")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Max execution time in seconds (0 for no limit)")
    parser.add_argument("--skip-images", action="store_true", default=False,
                        help="Skip downloading product images (faster cloud runs)")
    parser.add_argument("--migrate-only", action="store_true", default=False,
                        help="Only run migration on existing data, don't scrape new products")
    args = parser.parse_args()

    print("=" * 60)
    print("  STAR TECH BANGLADESH SCRAPER v2 (Hierarchical)")
    print("=" * 60)
    print(f"[*] Target DB: {args.db}")
    print(f"[*] Product Limit: {args.limit if args.limit > 0 else 'Unlimited'}")
    print(f"[*] Delay between requests: {args.delay} seconds")
    print(f"[*] Timeout: {args.timeout} seconds" if args.timeout > 0 else "[*] Timeout: None")
    print(f"[*] Skip images: {args.skip_images}")

    start_time = time.time()

    # Initialize DB with new schema
    conn = init_db(args.db)

    # Phase 1: Build category tree from homepage nav menu
    url_to_cat = build_category_tree(conn)

    # Phase 3 (run early): Migrate existing products
    migrate_existing_products(conn, url_to_cat)

    if args.migrate_only:
        print("[+] Migration-only mode. Exiting.")
        conn.close()
        return

    # Phase 2: Determine which categories to scrape
    leaf_cats = get_leaf_categories(conn)
    print(f"[+] Found {len(leaf_cats)} leaf categories to scrape.")

    # Filter by user-specified category if provided
    if args.category:
        filtered = []
        for cat in leaf_cats:
            cat_id, cat_name, cat_url, cat_level, cat_parent = cat
            if (args.category.lower() in cat_name.lower() or
                    args.category.lower() in cat_url.lower()):
                filtered.append(cat)

        # If no leaf matched directly, check if the user specified a parent category
        # and find all descendant leaf categories
        if not filtered:
            cursor = conn.cursor()
            # Find parent category by name or URL
            cursor.execute("""
                SELECT id, name, url FROM categories
                WHERE LOWER(name) = LOWER(?) OR url LIKE ?
            """, (args.category, f"%{args.category.lower()}%"))
            parent_rows = cursor.fetchall()

            if parent_rows:
                # Find all descendant leaves of these parent categories
                parent_ids = set(r[0] for r in parent_rows)
                # Recursively collect all descendant IDs
                all_descendant_ids = set()
                queue = list(parent_ids)
                while queue:
                    pid = queue.pop(0)
                    cursor.execute("SELECT id FROM categories WHERE parent_id = ?", (pid,))
                    for child in cursor.fetchall():
                        all_descendant_ids.add(child[0])
                        queue.append(child[0])

                # Now filter leaf_cats to only those whose id is in descendants
                leaf_ids = set(c[0] for c in leaf_cats)
                matching_leaf_ids = all_descendant_ids & leaf_ids
                # Also include the parent itself if it happens to be a leaf
                matching_leaf_ids |= parent_ids & leaf_ids

                filtered = [cat for cat in leaf_cats if cat[0] in matching_leaf_ids]
                if not filtered:
                    # Parent category might itself be scrapeable (no children in leaves)
                    for r in parent_rows:
                        filtered.append((r[0], r[1], r[2], 0, None))

        # Also include exact URL match
        if not filtered and args.category.startswith("http"):
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, url, level, parent_id FROM categories WHERE url = ?",
                           (args.category.rstrip("/"),))
            row = cursor.fetchone()
            if row:
                filtered = [row]
            else:
                filtered = [(None, "User Specified", args.category, 0, None)]

        if not filtered:
            print(f"[-] No categories matching '{args.category}' were found.")
            conn.close()
            sys.exit(1)

        leaf_cats = filtered
        print(f"[*] Scoping to {len(leaf_cats)} matching categories: {[c[1] for c in leaf_cats[:20]]}"
              + (f"... and {len(leaf_cats)-20} more" if len(leaf_cats) > 20 else ""))

    scraped_count = 0
    cursor = conn.cursor()

    for cat_id, cat_name, cat_url, cat_level, cat_parent_id in leaf_cats:
        if args.limit > 0 and scraped_count >= args.limit:
            break
        if args.timeout and (time.time() - start_time) > args.timeout:
            print(f"[!] Timeout of {args.timeout} seconds reached. Stopping.")
            break

        # Check if this category was already fully scraped
        cursor.execute("SELECT completed, last_page FROM scrape_progress WHERE category_url = ?",
                       (cat_url,))
        progress = cursor.fetchone()
        if progress and progress[0] == 1:
            print(f"[*] Skipping already completed category: {cat_name}")
            continue

        start_page = (progress[1] + 1) if progress else 1

        # Build the category path for fallback
        category_path = get_category_path(conn, cat_id) if cat_id else [cat_name]

        print(f"\n{'='*50}")
        print(f"[*] Scraping category: {' > '.join(category_path)} ({cat_url})")
        print(f"{'='*50}")

        page = start_page
        empty_pages = 0

        while args.limit <= 0 or scraped_count < args.limit:
            if args.timeout and (time.time() - start_time) > args.timeout:
                break

            # Build paginated URL
            paginated_url = cat_url
            if "?" in paginated_url:
                paginated_url += f"&page={page}"
            else:
                paginated_url += f"?page={page}"

            print(f"[*] Fetching product list page {page}: {paginated_url}")
            list_soup = get_soup(paginated_url)
            if not list_soup:
                print(f"[-] Failed to fetch page {page}. Moving to next category.")
                break

            # Find all product containers
            product_elems = list_soup.find_all("div", class_="p-item")
            if not product_elems:
                empty_pages += 1
                if empty_pages >= 2:
                    print("[*] No more products in this category. Marking as complete.")
                    cursor.execute("""
                        INSERT OR REPLACE INTO scrape_progress (category_url, last_page, completed, updated_at)
                        VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    """, (cat_url, page))
                    conn.commit()
                    break
                page += 1
                continue

            empty_pages = 0

            # Extract product URLs from the listing page
            product_urls = []
            for elem in product_elems:
                name_wrapper = elem.find("h4", class_="p-item-name")
                if name_wrapper:
                    link_elem = name_wrapper.find("a", href=True)
                    if link_elem:
                        product_urls.append(link_elem["href"])

            if not product_urls:
                for elem in product_elems:
                    a_elem = elem.find("a", href=True)
                    if a_elem:
                        product_urls.append(a_elem["href"])

            product_urls = list(set(product_urls))
            print(f"[+] Found {len(product_urls)} product links on page {page}.")

            for p_url in product_urls:
                if args.limit > 0 and scraped_count >= args.limit:
                    break
                if args.timeout and (time.time() - start_time) > args.timeout:
                    print(f"[!] Timeout reached. Stopping.")
                    break

                # Skip if already in DB
                cursor.execute("SELECT id FROM products WHERE url = ?", (p_url,))
                if cursor.fetchone():
                    print(f"[*] Skipping already scraped: {p_url}")
                    continue

                time.sleep(args.delay)

                try:
                    product_data = scrape_product_details(p_url, category_path, args.skip_images)
                    if product_data and product_data["name"]:
                        p_id = save_to_db(conn, product_data, cat_id, args.skip_images)
                        if p_id:
                            scraped_count += 1
                            cursor.execute("SELECT COUNT(*) FROM products")
                            total_in_db = cursor.fetchone()[0]
                            limit_str = f"/{args.limit}" if args.limit > 0 else "/Unlimited"
                            cat_display = " > ".join(category_path)
                            print(f"[+] Progress: {scraped_count}{limit_str} new | {total_in_db} total in DB | [{cat_display}]")
                except Exception as e:
                    print(f"[-] Exception scraping {p_url}: {e}")

            # Update progress
            cursor.execute("""
                INSERT OR REPLACE INTO scrape_progress (category_url, last_page, completed, updated_at)
                VALUES (?, ?, 0, CURRENT_TIMESTAMP)
            """, (cat_url, page))
            conn.commit()

            page += 1

    conn.close()
    elapsed = time.time() - start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    print(f"\n[+] Scraping session completed in {hours}h {minutes}m. Scraped {scraped_count} new products.")


if __name__ == "__main__":
    main()
