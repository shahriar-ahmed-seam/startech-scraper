import os
import json
import sqlite3
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000
DB_FILE = "startech.db"

class DashboardHandler(SimpleHTTPRequestHandler):
    def get_db_connection(self):
        """Returns a read-only SQLite connection to avoid locking the scraper."""
        db_uri = f"file:{DB_FILE}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        if path.startswith("/api/"):
            self.handle_api(path, query_params)
        else:
            if path == "/" or path == "":
                self.path = "/index.html"
            super().do_GET()

    def handle_api(self, path, params):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        response_data = {}

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            if path == "/api/stats":
                response_data = self._handle_stats(cursor)

            elif path == "/api/products":
                response_data = self._handle_products(cursor, params)

            elif path == "/api/categories":
                response_data = self._handle_categories(cursor)

            elif path.startswith("/api/product/"):
                product_id = int(path.split("/")[-1])
                response_data = self._handle_product_detail(cursor, product_id)
                if response_data is None:
                    self.send_error(404, "Product not found")
                    conn.close()
                    return

            else:
                self.send_error(404, "API Endpoint not found")
                conn.close()
                return

            conn.close()
        except Exception as e:
            response_data = {"error": str(e)}

        self.wfile.write(json.dumps(response_data).encode("utf-8"))

    def _handle_stats(self, cursor):
        """Returns dashboard statistics with hierarchical category breakdown."""
        # Total count
        cursor.execute("SELECT COUNT(*) FROM products")
        total = cursor.fetchone()[0]

        # Legacy category breakdown (flat)
        cursor.execute("SELECT category, COUNT(*) FROM products GROUP BY category")
        categories = {row["category"]: row[1] for row in cursor.fetchall() if row["category"]}

        # Main category breakdown (hierarchical level 0)
        cursor.execute("""
            SELECT main_category, COUNT(*) FROM products
            WHERE main_category IS NOT NULL AND main_category != ''
            GROUP BY main_category ORDER BY main_category ASC
        """)
        main_categories = {row["main_category"]: row[1] for row in cursor.fetchall()}

        # Sub-category breakdown (level 1)
        cursor.execute("""
            SELECT sub_category, COUNT(*) FROM products
            WHERE sub_category IS NOT NULL AND sub_category != ''
            GROUP BY sub_category ORDER BY sub_category ASC
        """)
        sub_categories = {row["sub_category"]: row[1] for row in cursor.fetchall()}

        # Sub-sub-category breakdown (level 2)
        cursor.execute("""
            SELECT sub_sub_category, COUNT(*) FROM products
            WHERE sub_sub_category IS NOT NULL AND sub_sub_category != ''
            GROUP BY sub_sub_category ORDER BY sub_sub_category ASC
        """)
        sub_sub_categories = {row["sub_sub_category"]: row[1] for row in cursor.fetchall()}

        # Unique brands
        cursor.execute("""
            SELECT brand, COUNT(*) FROM products
            WHERE brand IS NOT NULL AND brand != ''
            GROUP BY brand ORDER BY brand ASC
        """)
        brands = {row["brand"]: row[1] for row in cursor.fetchall()}

        # Unique statuses
        cursor.execute("""
            SELECT status, COUNT(*) FROM products
            WHERE status IS NOT NULL AND status != ''
            GROUP BY status ORDER BY status ASC
        """)
        statuses = {row["status"]: row[1] for row in cursor.fetchall()}

        # Price ranges
        cursor.execute("SELECT MIN(price), MAX(price) FROM products WHERE price > 0")
        min_p, max_p = cursor.fetchone()

        # Category tree stats
        cat_tree_total = 0
        try:
            cursor.execute("SELECT COUNT(*) FROM categories")
            cat_tree_total = cursor.fetchone()[0]
        except:
            pass

        return {
            "total_products": total,
            "categories": categories,
            "main_categories": main_categories,
            "sub_categories": sub_categories,
            "sub_sub_categories": sub_sub_categories,
            "brands": brands,
            "statuses": statuses,
            "min_price": min_p or 0,
            "max_price": max_p or 0,
            "category_tree_total": cat_tree_total
        }

    def _handle_products(self, cursor, params):
        """Returns paginated product list with hierarchical filtering."""
        search_query = params.get("search", [""])[0].strip()
        # Support both flat and hierarchical filters
        category_filter = params.get("category", [""])[0].strip()
        main_cat_filter = params.get("main_category", [""])[0].strip()
        sub_cat_filter = params.get("sub_category", [""])[0].strip()
        sub_sub_cat_filter = params.get("sub_sub_category", [""])[0].strip()
        brand_filter = params.get("brand", [""])[0].strip()
        status_filter = params.get("status", [""])[0].strip()
        min_price = params.get("min_price", [""])[0].strip()
        max_price = params.get("max_price", [""])[0].strip()
        sort_filter = params.get("sort", ["recent"])[0].strip()
        page = int(params.get("page", ["1"])[0])
        limit = int(params.get("limit", ["24"])[0])
        offset = (page - 1) * limit

        query = "SELECT * FROM products WHERE 1=1"
        args = []

        if search_query:
            query += " AND (name LIKE ? OR brand LIKE ? OR product_code LIKE ?)"
            search_pat = f"%{search_query}%"
            args.extend([search_pat, search_pat, search_pat])

        # Hierarchical category filters
        if main_cat_filter:
            query += " AND main_category = ?"
            args.append(main_cat_filter)

        if sub_cat_filter:
            query += " AND sub_category = ?"
            args.append(sub_cat_filter)

        if sub_sub_cat_filter:
            query += " AND sub_sub_category = ?"
            args.append(sub_sub_cat_filter)

        # Legacy flat category filter (backwards compatible)
        if category_filter and not main_cat_filter and not sub_cat_filter:
            query += " AND (category = ? OR main_category = ? OR sub_category = ?)"
            args.extend([category_filter, category_filter, category_filter])

        if brand_filter:
            query += " AND brand = ?"
            args.append(brand_filter)

        if status_filter:
            query += " AND status = ?"
            args.append(status_filter)

        if min_price:
            try:
                query += " AND price >= ?"
                args.append(int(min_price))
            except ValueError:
                pass

        if max_price:
            try:
                query += " AND price <= ?"
                args.append(int(max_price))
            except ValueError:
                pass

        # Get total filtered count
        count_query = query.replace("SELECT *", "SELECT COUNT(*)")
        cursor.execute(count_query, args)
        total_filtered = cursor.fetchone()[0]

        sort_map = {
            "recent": "scraped_at DESC",
            "price-low": "price ASC",
            "price-high": "price DESC",
            "name-asc": "name ASC",
            "name-desc": "name DESC"
        }
        order_by = sort_map.get(sort_filter, "scraped_at DESC")

        query += f" ORDER BY {order_by} LIMIT ? OFFSET ?"
        args.extend([limit, offset])

        cursor.execute(query, args)
        products = []
        for row in cursor.fetchall():
            products.append({
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "category": row["category"],
                "main_category": row["main_category"] if "main_category" in row.keys() else None,
                "sub_category": row["sub_category"] if "sub_category" in row.keys() else None,
                "sub_sub_category": row["sub_sub_category"] if "sub_sub_category" in row.keys() else None,
                "price": row["price"],
                "old_price": row["old_price"],
                "status": row["status"],
                "product_code": row["product_code"],
                "brand": row["brand"],
                "image_path": row["image_path"]
            })

        return {
            "products": products,
            "total": total_filtered,
            "page": page,
            "limit": limit,
            "has_more": (offset + len(products)) < total_filtered
        }

    def _handle_categories(self, cursor):
        """Returns the full category tree as a nested JSON structure."""
        try:
            cursor.execute("SELECT id, name, url, parent_id, level FROM categories ORDER BY level, name")
        except:
            return {"tree": [], "total": 0}

        all_cats = cursor.fetchall()
        cat_map = {}
        for row in all_cats:
            cat_map[row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "url": row["url"],
                "level": row["level"],
                "children": []
            }

        tree = []
        for row in all_cats:
            cat = cat_map[row["id"]]
            parent_id = row["parent_id"]
            if parent_id and parent_id in cat_map:
                cat_map[parent_id]["children"].append(cat)
            else:
                tree.append(cat)

        # Add product counts to each category
        for cat_id, cat in cat_map.items():
            cursor.execute("SELECT COUNT(*) FROM products WHERE category_id = ?", (cat_id,))
            count_row = cursor.fetchone()
            cat["product_count"] = count_row[0] if count_row else 0

        return {"tree": tree, "total": len(all_cats)}

    def _handle_product_detail(self, cursor, product_id):
        """Returns full product details including specs and category hierarchy."""
        cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        prod_row = cursor.fetchone()

        if not prod_row:
            return None

        product = {
            "id": prod_row["id"],
            "name": prod_row["name"],
            "url": prod_row["url"],
            "category": prod_row["category"],
            "main_category": prod_row["main_category"] if "main_category" in prod_row.keys() else None,
            "sub_category": prod_row["sub_category"] if "sub_category" in prod_row.keys() else None,
            "sub_sub_category": prod_row["sub_sub_category"] if "sub_sub_category" in prod_row.keys() else None,
            "price": prod_row["price"],
            "old_price": prod_row["old_price"],
            "status": prod_row["status"],
            "product_code": prod_row["product_code"],
            "brand": prod_row["brand"],
            "image_path": prod_row["image_path"],
            "scraped_at": prod_row["scraped_at"],
            "specs": {}
        }

        # Build breadcrumb path
        breadcrumb_parts = []
        if product["main_category"]:
            breadcrumb_parts.append(product["main_category"])
        if product["sub_category"]:
            breadcrumb_parts.append(product["sub_category"])
        if product["sub_sub_category"]:
            breadcrumb_parts.append(product["sub_sub_category"])
        product["breadcrumb_path"] = " > ".join(breadcrumb_parts) if breadcrumb_parts else product["category"] or ""

        # Fetch specifications
        cursor.execute("""
            SELECT spec_group, spec_name, spec_value
            FROM product_specs
            WHERE product_id = ?
            ORDER BY id ASC
        """, (product_id,))

        for spec_row in cursor.fetchall():
            group = spec_row["spec_group"] or "General"
            if group not in product["specs"]:
                product["specs"][group] = []
            product["specs"][group].append({
                "name": spec_row["spec_name"],
                "value": spec_row["spec_value"]
            })

        return product


def main():
    file_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(file_dir)

    # Try PORT first, fall back to PORT+1 if occupied
    for port in [PORT, PORT + 1, PORT + 2]:
        try:
            print(f"[*] Starting local server on http://localhost:{port}")
            print(f"[*] Database file path: {os.path.abspath(DB_FILE)}")
            server = HTTPServer(("0.0.0.0", port), DashboardHandler)
            server.serve_forever()
        except OSError as e:
            if "Address already in use" in str(e) or "10048" in str(e):
                print(f"[!] Port {port} is occupied, trying {port + 1}...")
                continue
            raise
        except KeyboardInterrupt:
            print("\n[!] Shutting down server.")
            server.server_close()
            break


if __name__ == "__main__":
    main()
