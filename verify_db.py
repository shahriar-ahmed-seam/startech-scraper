"""Database verification and statistics script for Star Tech scraper."""
import sqlite3
import os

DB_FILE = "startech.db"

def main():
    if not os.path.exists(DB_FILE):
        print(f"[!] Database file '{DB_FILE}' not found!")
        return

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    print("=" * 60)
    print("  STAR TECH DATABASE VERIFICATION")
    print("=" * 60)

    # Total products
    cursor.execute("SELECT COUNT(*) FROM products")
    total = cursor.fetchone()[0]
    print(f"\nTotal products in database: {total}")

    # Products with hierarchical categories
    cursor.execute("SELECT COUNT(*) FROM products WHERE main_category IS NOT NULL AND main_category != ''")
    with_main = cursor.fetchone()[0]
    print(f"Products with main_category: {with_main}")

    cursor.execute("SELECT COUNT(*) FROM products WHERE sub_category IS NOT NULL AND sub_category != ''")
    with_sub = cursor.fetchone()[0]
    print(f"Products with sub_category: {with_sub}")

    cursor.execute("SELECT COUNT(*) FROM products WHERE sub_sub_category IS NOT NULL AND sub_sub_category != ''")
    with_subsub = cursor.fetchone()[0]
    print(f"Products with sub_sub_category: {with_subsub}")

    # Category tree
    try:
        cursor.execute("SELECT COUNT(*) FROM categories")
        cat_total = cursor.fetchone()[0]
        print(f"\nCategory tree nodes: {cat_total}")

        cursor.execute("SELECT COUNT(*) FROM categories WHERE level = 0")
        l0 = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM categories WHERE level = 1")
        l1 = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM categories WHERE level = 2")
        l2 = cursor.fetchone()[0]
        print(f"  Level 0 (Main): {l0}")
        print(f"  Level 1 (Sub): {l1}")
        print(f"  Level 2 (Sub-Sub): {l2}")
    except:
        print("\nCategory tree: Not built yet")

    # Main categories breakdown
    print("\n--- Main Category Breakdown ---")
    cursor.execute("""
        SELECT main_category, COUNT(*) as cnt FROM products
        WHERE main_category IS NOT NULL AND main_category != ''
        GROUP BY main_category ORDER BY cnt DESC
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]:30s} {row[1]:>6d} products")

    # Top sub-categories
    print("\n--- Top 20 Sub Categories ---")
    cursor.execute("""
        SELECT sub_category, COUNT(*) as cnt FROM products
        WHERE sub_category IS NOT NULL AND sub_category != ''
        GROUP BY sub_category ORDER BY cnt DESC LIMIT 20
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]:30s} {row[1]:>6d} products")

    # Brand breakdown
    print("\n--- Brand Breakdown (Top 15) ---")
    cursor.execute("""
        SELECT brand, COUNT(*) as cnt FROM products
        WHERE brand IS NOT NULL AND brand != ''
        GROUP BY brand ORDER BY cnt DESC LIMIT 15
    """)
    for row in cursor.fetchall():
        print(f"  {row[0]:30s} {row[1]:>6d} products")

    # Status breakdown
    print("\n--- Status Breakdown ---")
    cursor.execute("SELECT status, COUNT(*) FROM products GROUP BY status")
    for row in cursor.fetchall():
        print(f"  {str(row[0]):30s} {row[1]:>6d} products")

    # Price statistics
    cursor.execute("SELECT MIN(price), MAX(price), AVG(price) FROM products WHERE price > 0")
    min_p, max_p, avg_p = cursor.fetchone()
    print(f"\n--- Price Statistics ---")
    print(f"  Min price: {min_p or 0:>12,} BDT")
    print(f"  Max price: {max_p or 0:>12,} BDT")
    print(f"  Avg price: {int(avg_p or 0):>12,} BDT")

    # Scrape progress
    try:
        cursor.execute("SELECT COUNT(*) FROM scrape_progress WHERE completed = 1")
        completed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM scrape_progress WHERE completed = 0")
        in_progress = cursor.fetchone()[0]
        print(f"\n--- Scrape Progress ---")
        print(f"  Completed categories: {completed}")
        print(f"  In-progress categories: {in_progress}")
    except:
        pass

    # Sample products
    print("\n--- Sample Products (Last 5) ---")
    cursor.execute("""
        SELECT name, main_category, sub_category, sub_sub_category, price, status
        FROM products ORDER BY id DESC LIMIT 5
    """)
    for row in cursor.fetchall():
        path = " > ".join(filter(None, [row[1], row[2], row[3]]))
        price_str = f"{row[4]:,} BDT" if row[4] else "N/A"
        print(f"  [{path}] {row[0][:50]} | {price_str} | {row[5]}")

    conn.close()
    print(f"\n{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
