import os
import sys
import re
import time
import urllib.parse
import sqlite3
import argparse
import requests
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

def init_db(db_path="startech.db"):
    """Initializes the SQLite database and creates the necessary tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Create products table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        category TEXT,
        price INTEGER,
        old_price INTEGER,
        status TEXT,
        product_code TEXT,
        brand TEXT,
        image_path TEXT,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Create product_specs table
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
    
    conn.commit()
    return conn

def download_image(url, product_id):
    """Downloads an image from a URL and saves it to the pics folder, returning the local path."""
    if not url:
        return None
    
    if not os.path.exists(PICS_DIR):
        os.makedirs(PICS_DIR)
        
    try:
        # Handle relative URLs
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE_URL + url
            
        # Extract file extension, default to .jpg
        parsed_url = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"
            
        filename = f"product_{product_id}{ext}"
        filepath = os.path.join(PICS_DIR, filename)
        
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

def parse_price(price_str):
    """Extracts integer value from pricing string containing currency symbol and commas."""
    if not price_str:
        return None
    # Remove commas, currency symbol, whitespace, etc.
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else None

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

def get_categories(soup):
    """Extracts major category URLs from the homepage navigation menu."""
    categories = []
    # Startech menu is usually structured with nav items
    # We look for main links in navigation or categories
    # Common selector for menu links: .nav > li > a or general links pointing to categories
    # Let's inspect typical category elements or crawl specific ones.
    # To be extremely robust, we parse the header category structure.
    nav = soup.find("nav", id="menu")
    if not nav:
        # Fallback to main navigation
        nav = soup.find("ul", class_="navbar-nav")
    
    if nav:
        for a in nav.find_all("a", href=True):
            href = a['href']
            # Startech categories usually follow patterns like startech.com.bd/component/processor or startech.com.bd/laptop
            # Avoid contact, account, etc.
            if any(term in href for term in ["/account/", "/checkout/", "/cart", "/information/", "/route=common", "/compare"]):
                continue
            if href != BASE_URL and href != BASE_URL + "/" and not href.startswith("javascript:"):
                # Normalize URL
                if not href.startswith("http"):
                    href = urllib.parse.urljoin(BASE_URL, href)
                name = a.get_text(strip=True)
                if href not in [c['url'] for c in categories] and name:
                    categories.append({"name": name, "url": href})
                    
    # If menu parsing yielded nothing, use a set of default main categories
    if not categories:
        print("[!] Navigation menu parser failed. Falling back to default list of main categories.")
        default_categories = [
            ("Components", f"{BASE_URL}/component"),
            ("Processor", f"{BASE_URL}/component/processor"),
            ("Graphics Card", f"{BASE_URL}/component/graphics-card"),
            ("Motherboard", f"{BASE_URL}/component/motherboard"),
            ("RAM", f"{BASE_URL}/component/ram"),
            ("SSD", f"{BASE_URL}/component/ssd"),
            ("Monitor", f"{BASE_URL}/monitor"),
            ("Laptop", f"{BASE_URL}/laptop"),
            ("Keyboard", f"{BASE_URL}/accessories/keyboard"),
            ("Mouse", f"{BASE_URL}/accessories/mouse"),
        ]
        for name, url in default_categories:
            categories.append({"name": name, "url": url})
            
    return categories

def scrape_product_details(url, category_name=None):
    """Scrapes detailed information from a product page."""
    print(f"[*] Scraping product details from: {url}")
    soup = get_soup(url)
    if not soup:
        return None
        
    product = {
        "url": url,
        "category": category_name,
        "name": "",
        "price": None,
        "old_price": None,
        "status": "",
        "product_code": "",
        "brand": "",
        "image_url": None,
        "specs": []
    }
    
    # 1. Product Name
    title_elem = soup.find("h1", itemprop="name")
    if not title_elem:
        title_elem = soup.find("h1")
    if title_elem:
        product["name"] = title_elem.get_text(strip=True)
        
    # 2. Key/Core info (Status, Code, Brand)
    # These are usually in table.product-info-table
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
                    
    # Fallback/Additional search for status, code, brand if table not found or incomplete
    if not product["product_code"]:
        code_elem = soup.find(text=re.compile(r"Product Code:", re.IGNORECASE))
        if code_elem:
            product["product_code"] = code_elem.find_next().get_text(strip=True)
            
    if not product["brand"]:
        brand_elem = soup.find("meta", itemprop="brand")
        if brand_elem:
            product["brand"] = brand_elem.get("content", "").strip()
        else:
            brand_link = soup.find("a", href=re.compile(r"/brand/"))
            if brand_link:
                product["brand"] = brand_link.get_text(strip=True)
                
    if not product["status"]:
        status_elem = soup.find("td", class_="product-status")
        if status_elem:
            product["status"] = status_elem.get_text(strip=True)
            
    # 3. Pricing
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
        # Fallback pricing lookups
        price_meta = soup.find("meta", itemprop="price")
        if price_meta:
            try:
                product["price"] = int(float(price_meta.get("content", "0")))
            except:
                pass
                
    # 4. Image URL
    img_holder = soup.find("div", class_="product-img-holder")
    if img_holder:
        img_elem = img_holder.find("img", class_="main-img") or img_holder.find("img")
        if img_elem:
            product["image_url"] = img_elem.get("src") or img_elem.get("data-src")
            
    # 5. Full specifications table (data-table flex-table)
    specs_tables = soup.find_all("table", class_="data-table")
    for table in specs_tables:
        current_group = "General"
        for row in table.find_all("tr"):
            # Check for group header
            heading_cell = row.find("td", class_="heading-row")
            if heading_cell:
                current_group = heading_cell.get_text(strip=True)
                continue
                
            cells = row.find_all("td")
            if len(cells) >= 2:
                name_cell = cells[0].get_text(strip=True)
                value_cell = cells[1].get_text(strip=True)
                # Skip helper header rows or empty lines
                if name_cell and value_cell:
                    product["specs"].append({
                        "group": current_group,
                        "name": name_cell,
                        "value": value_cell
                    })
                    
    return product

def save_to_db(conn, product):
    """Saves the scraped product information and specifications into the database."""
    cursor = conn.cursor()
    
    try:
        # Check if product already exists to avoid conflict
        cursor.execute("SELECT id, image_path FROM products WHERE url = ?", (product["url"],))
        existing = cursor.fetchone()
        
        if existing:
            product_id = existing[0]
            # Update product basic info
            cursor.execute("""
            UPDATE products 
            SET name = ?, category = ?, price = ?, old_price = ?, status = ?, product_code = ?, brand = ?
            WHERE id = ?
            """, (
                product["name"],
                product["category"],
                product["price"],
                product["old_price"],
                product["status"],
                product["product_code"],
                product["brand"],
                product_id
            ))
            print(f"[+] Updated existing product ID {product_id} in database.")
        else:
            # Insert product basic info
            cursor.execute("""
            INSERT INTO products (name, url, category, price, old_price, status, product_code, brand)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                product["name"],
                product["url"],
                product["category"],
                product["price"],
                product["old_price"],
                product["status"],
                product["product_code"],
                product["brand"]
            ))
            product_id = cursor.lastrowid
            print(f"[+] Inserted new product ID {product_id} to database.")
            
        # Download and link image if we have URL and don't already have a valid local image
        if product["image_url"]:
            image_path = download_image(product["image_url"], product_id)
            if image_path:
                cursor.execute("UPDATE products SET image_path = ? WHERE id = ?", (image_path, product_id))
                
        # Clear existing specifications for this product to prevent duplicates, then re-insert
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

def main():
    parser = argparse.ArgumentParser(description="Star Tech Bangladesh Product Scraper")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of products to scrape (set to 0 for unlimited)")
    parser.add_argument("--category", type=str, default=None, help="Scrape only from this category URL or match name")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay in seconds between requests to avoid bans")
    parser.add_argument("--db", type=str, default="startech.db", help="SQLite database file path")
    args = parser.parse_args()
    
    print("[*] Starting Star Tech Bangladesh Web Scraper...")
    print(f"[*] Target DB: {args.db}")
    print(f"[*] Product Limit: {args.limit if args.limit > 0 else 'Unlimited'}")
    print(f"[*] Delay between requests: {args.delay} seconds")
    
    # Initialize DB
    conn = init_db(args.db)
    
    # Get home page to fetch categories
    print(f"[*] Fetching homepage to discover categories: {BASE_URL}")
    homepage_soup = get_soup(BASE_URL)
    if not homepage_soup:
        print("[-] Critical Error: Failed to fetch Star Tech homepage. Exiting.")
        sys.exit(1)
        
    categories = get_categories(homepage_soup)
    print(f"[+] Discovered {len(categories)} categories.")
    
    # Filter category if specified
    if args.category:
        filtered = []
        for cat in categories:
            if args.category.lower() in cat["name"].lower() or args.category.lower() in cat["url"].lower():
                filtered.append(cat)
        if not filtered:
            # Treat as direct URL
            if args.category.startswith("http"):
                filtered = [{"name": "User Specified", "url": args.category}]
            else:
                print(f"[-] No categories matching '{args.category}' were found.")
                sys.exit(1)
        categories = filtered
        print(f"[*] Scoping scraping to matched categories: {[c['name'] for c in categories]}")
        
    scraped_count = 0
    
    # Iterate through categories and paginate to find products
    for cat in categories:
        if args.limit > 0 and scraped_count >= args.limit:
            break
            
        print(f"\n[*] Processing category: {cat['name']} ({cat['url']})")
        page = 1
        
        while args.limit <= 0 or scraped_count < args.limit:
            # Build paginated URL
            paginated_url = cat["url"]
            if "?" in paginated_url:
                paginated_url += f"&page={page}"
            else:
                paginated_url += f"?page={page}"
                
            print(f"[*] Fetching product list page {page}: {paginated_url}")
            list_soup = get_soup(paginated_url)
            if not list_soup:
                print(f"[-] Failed to fetch list page {page}. Moving to next category.")
                break
                
            # Find all product containers
            product_elems = list_soup.find_all("div", class_="p-item")
            if not product_elems:
                print("[*] No more products found in this category. Moving on.")
                break
                
            print(f"[+] Found {len(product_elems)} product links on this page.")
            
            product_urls = []
            for elem in product_elems:
                # Find link inside .p-item-name a or general href inside item
                name_wrapper = elem.find("h4", class_="p-item-name")
                if name_wrapper:
                    link_elem = name_wrapper.find("a", href=True)
                    if link_elem:
                        product_urls.append(link_elem["href"])
                        
            if not product_urls:
                # Fallback to general search for href
                for elem in product_elems:
                    a_elem = elem.find("a", href=True)
                    if a_elem:
                        product_urls.append(a_elem["href"])
                        
            # Unique URLs on page
            product_urls = list(set(product_urls))
            
            # Scrape individual products
            for p_url in product_urls:
                if args.limit > 0 and scraped_count >= args.limit:
                    break
                    
                # Small courtesy delay
                time.sleep(args.delay)
                
                try:
                    product_data = scrape_product_details(p_url, cat["name"])
                    if product_data and product_data["name"]:
                        p_id = save_to_db(conn, product_data)
                        if p_id:
                            scraped_count += 1
                            # Get count of unique products stored in the database
                            db_cursor = conn.cursor()
                            db_cursor.execute("SELECT COUNT(*) FROM products")
                            unique_count = db_cursor.fetchone()[0]
                            limit_str = f"/{args.limit}" if args.limit > 0 else "/Unlimited"
                            print(f"[+] Progress: {scraped_count}{limit_str} processed ({unique_count} unique products in database).")
                except Exception as e:
                    print(f"[-] Exception scraping product {p_url}: {e}")
                    
            page += 1
            
    conn.close()
    print(f"\n[+] Scraping session completed. Scraped {scraped_count} products successfully.")

if __name__ == "__main__":
    main()
