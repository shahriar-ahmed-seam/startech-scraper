import os
import re
import sys
import time
import urllib.parse
import sqlite3
import requests
# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

DB_FILE = "startech.db"
PICS_DIR = "pics"
BASE_URL = "https://www.startech.com.bd"
NUM_THREADS = 15  # Adjust this to speed up/slow down download
DELAY_BETWEEN_REQUESTS = 0.2  # Small delay to prevent IP blocking

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Referer": BASE_URL
}

# Thread lock for SQLite database writes
db_lock = threading.Lock()
# Thread-local storage for requests sessions and db connections
thread_local = threading.local()

def get_session():
    if not hasattr(thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)
        thread_local.session = session
    return thread_local.session

def get_db_conn():
    if not hasattr(thread_local, "conn"):
        thread_local.conn = sqlite3.connect(DB_FILE)
    return thread_local.conn

def fetch_image_url_from_page(session, product_url):
    """Fetches product HTML and extracts the main image URL."""
    try:
        response = session.get(product_url, timeout=15)
        if response.status_code != 200:
            return None
        
        soup = BeautifulSoup(response.text, "html.parser")
        img_holder = soup.find("div", class_="product-img-holder")
        if img_holder:
            img_elem = img_holder.find("img", class_="main-img") or img_holder.find("img")
            if img_elem:
                return img_elem.get("src") or img_elem.get("data-src")
    except Exception as e:
        pass
    return None

def download_image_file(session, url, product_id):
    """Downloads an image URL and saves it to pics/product_<id>.<ext>."""
    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = BASE_URL + url

    try:
        parsed_url = urllib.parse.urlparse(url)
        ext = os.path.splitext(parsed_url.path)[1]
        if not ext or len(ext) > 5:
            ext = ".jpg"

        filename = f"product_{product_id}{ext}"
        filepath = os.path.join(PICS_DIR, filename)

        # Download image file
        response = session.get(url, timeout=15)
        if response.status_code == 200:
            with open(filepath, "wb") as f:
                f.write(response.content)
            return filepath
    except Exception as e:
        print(f"\n[-] Error downloading image {url}: {e}")
    return None

def process_product(product):
    product_id, product_url = product
    session = get_session()
    
    # Introduce a small staggered delay per thread to avoid rate limits
    time.sleep(DELAY_BETWEEN_REQUESTS)
    
    # Step 1: Get product detail page HTML and parse image URL
    img_url = fetch_image_url_from_page(session, product_url)
    if not img_url:
        return product_id, False, "Could not find image URL on page"

    # Step 2: Download image to folder
    local_path = download_image_file(session, img_url, product_id)
    if not local_path:
        return product_id, False, "Failed to download image file"

    # Step 3: Update database with local path
    conn = get_db_conn()
    try:
        with db_lock:
            cursor = conn.cursor()
            cursor.execute("UPDATE products SET image_path = ? WHERE id = ?", (local_path, product_id))
            conn.commit()
        return product_id, True, local_path
    except Exception as e:
        return product_id, False, f"Database write error: {e}"

def main():
    if not os.path.exists(PICS_DIR):
        os.makedirs(PICS_DIR)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Find products missing images
    cursor.execute("SELECT id, url FROM products WHERE image_path IS NULL OR image_path = ''")
    products_to_download = cursor.fetchall()
    conn.close()

    total_count = len(products_to_download)
    if total_count == 0:
        print("[+] All products in the database already have images! Nothing to do.")
        return

    print("=" * 60)
    print(f"[*] Starting local image downloader")
    print(f"[*] Products missing images: {total_count}")
    print(f"[*] Concurrency limit: {NUM_THREADS} threads")
    print("=" * 60)

    success_count = 0
    failure_count = 0
    start_time = time.time()

    try:
        with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
            # Submit all products to the thread pool
            futures = {executor.submit(process_product, p): p for p in products_to_download}
            
            for future in as_completed(futures):
                product_id, success, detail = future.result()
                if success:
                    success_count += 1
                else:
                    failure_count += 1
                
                # Print progress bar inline
                completed = success_count + failure_count
                pct = (completed / total_count) * 100
                elapsed = time.time() - start_time
                speed = completed / elapsed if elapsed > 0 else 0
                eta = (total_count - completed) / speed if speed > 0 else 0
                
                sys.stdout.write(
                    f"\rProgress: {completed}/{total_count} ({pct:.1f}%) | "
                    f"Success: {success_count} | Failures: {failure_count} | "
                    f"Speed: {speed:.1f} items/sec | ETA: {int(eta // 60)}m {int(eta % 60)}s"
                )
                sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n[!] Downloader paused by user. Clean shutdown...")
    finally:
        # Clean up any open database connections in worker threads
        # Python cleans up thread local variables automatically on exit, but close if alive
        pass

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"[+] Download session completed in {int(elapsed // 60)}m {int(elapsed % 60)}s.")
    print(f"[+] Successfully downloaded: {success_count} images.")
    print(f"[+] Failed: {failure_count} products.")
    print("=" * 60)

if __name__ == "__main__":
    main()
