import os
import sqlite3

def verify_data(db_path="startech.db"):
    if not os.path.exists(db_path):
        print(f"[-] Database file '{db_path}' not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("==================================================")
    print("           STAR TECH SCRAPER VERIFICATION         ")
    print("==================================================")

    # 1. Total count
    cursor.execute("SELECT COUNT(*) FROM products")
    total_products = cursor.fetchone()[0]
    print(f"[+] Total Products Scraped: {total_products}")

    # 2. Count by Category
    print("\n[+] Breakdown by Category:")
    cursor.execute("SELECT category, COUNT(*) FROM products GROUP BY category")
    for cat, count in cursor.fetchall():
        print(f"    - {cat or 'None'}: {count}")

    # 3. Specs count
    cursor.execute("SELECT COUNT(*) FROM product_specs")
    total_specs = cursor.fetchone()[0]
    print(f"\n[+] Total Specifications Stored: {total_specs}")

    # 4. Details of Scraped Products
    print("\n[+] Sample Products:")
    cursor.execute("SELECT id, name, price, brand, product_code, image_path FROM products LIMIT 5")
    rows = cursor.fetchall()
    
    for row in rows:
        p_id, name, price, brand, code, img_path = row
        img_exists = "Yes" if img_path and os.path.exists(img_path) else "No"
        print(f"\n    ID: {p_id}")
        print(f"    Name: {name}")
        print(f"    Brand: {brand} | Code: {code}")
        print(f"    Price: {price} BDT")
        print(f"    Image Path: {img_path} (Exists on disk: {img_exists})")

        # Get some specs
        cursor.execute("SELECT spec_group, spec_name, spec_value FROM product_specs WHERE product_id = ? LIMIT 3", (p_id,))
        specs = cursor.fetchall()
        if specs:
            print("    Sample Specifications:")
            for group, name, val in specs:
                print(f"      [{group}] {name}: {val}")

    # 5. Check if pics dir has files
    pics_dir = "pics"
    if os.path.exists(pics_dir):
        files = os.listdir(pics_dir)
        print(f"\n[+] Total images in 'pics' folder: {len(files)}")
    else:
        print("\n[-] 'pics' folder does not exist.")

    conn.close()

if __name__ == "__main__":
    verify_data()
