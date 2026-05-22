import sqlite3
import sys
import os

def merge_databases(source_db_path, target_db_path):
    """
    Merges source_db (the newly scraped database from the runner)
    into target_db (the base database pulled from GitHub main branch).
    """
    if not os.path.exists(source_db_path):
        print(f"Error: Source database '{source_db_path}' does not exist.")
        sys.exit(1)
    if not os.path.exists(target_db_path):
        print(f"Error: Target database '{target_db_path}' does not exist.")
        sys.exit(1)

    print(f"[*] Merging changes from '{source_db_path}' into '{target_db_path}'...")

    conn = sqlite3.connect(target_db_path)
    cursor = conn.cursor()

    try:
        # Attach the source database
        cursor.execute(f"ATTACH DATABASE ? AS source_db", (source_db_path,))

        # 1. Merge categories
        print("[*] Merging categories...")
        cursor.execute("""
            INSERT OR IGNORE INTO categories (id, name, url, parent_id, level)
            SELECT id, name, url, parent_id, level FROM source_db.categories
        """)
        categories_added = cursor.rowcount
        print(f"[+] Added {categories_added} new categories.")

        # 2. Merge products
        print("[*] Merging products...")
        cursor.execute("""
            INSERT OR IGNORE INTO products (
                id, name, url, category, price, old_price, status, 
                product_code, brand, image_path, scraped_at, 
                main_category, sub_category, sub_sub_category, category_id
            )
            SELECT 
                id, name, url, category, price, old_price, status, 
                product_code, brand, image_path, scraped_at, 
                main_category, sub_category, sub_sub_category, category_id
            FROM source_db.products
        """)
        products_added = cursor.rowcount
        print(f"[+] Added {products_added} new products.")

        # 3. Merge product specs
        print("[*] Merging product specs...")
        cursor.execute("""
            INSERT OR IGNORE INTO product_specs (id, product_id, spec_group, spec_name, spec_value)
            SELECT id, product_id, spec_group, spec_name, spec_value FROM source_db.product_specs
        """)
        specs_added = cursor.rowcount
        print(f"[+] Added {specs_added} new specs.")

        # 4. Overwrite scrape progress (runner has the latest crawl state)
        print("[*] Updating scrape progress...")
        cursor.execute("DELETE FROM scrape_progress")
        cursor.execute("""
            INSERT INTO scrape_progress (category_url, last_page, completed, updated_at)
            SELECT category_url, last_page, completed, updated_at FROM source_db.scrape_progress
        """)
        progress_rows = cursor.rowcount
        print(f"[+] Synced {progress_rows} scrape progress entries.")

        # Commit all changes to target_db
        conn.commit()
        print("[+] Database merge completed successfully!")

    except Exception as e:
        conn.rollback()
        print(f"[-] Error during merge: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python merge_db.py <source_db> <target_db>")
        sys.exit(1)
    merge_databases(sys.argv[1], sys.argv[2])
