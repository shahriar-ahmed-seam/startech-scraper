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
        # Using URI mode with mode=ro ensures it never acquires a write lock or interferes with the scraper.
        db_uri = f"file:{DB_FILE}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self):
        # Parse the request URL
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # Route API requests
        if path.startswith("/api/"):
            self.handle_api(path, query_params)
        else:
            # Fall back to default handler for static files (index.html, images, etc.)
            # If path is root, serve index.html
            if path == "/" or path == "":
                self.path = "/index.html"
            super().do_GET()

    def handle_api(self, path, params):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        # Enable CORS
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        response_data = {}

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            if path == "/api/stats":
                # Get total count
                cursor.execute("SELECT COUNT(*) FROM products")
                total = cursor.fetchone()[0]

                # Get breakdown by category
                cursor.execute("SELECT category, COUNT(*) FROM products GROUP BY category")
                categories = {row["category"]: row[1] for row in cursor.fetchall() if row["category"]}

                # Get unique brands
                cursor.execute("SELECT brand, COUNT(*) FROM products WHERE brand IS NOT NULL AND brand != '' GROUP BY brand ORDER BY brand ASC")
                brands = {row["brand"]: row[1] for row in cursor.fetchall()}

                # Get unique statuses
                cursor.execute("SELECT status, COUNT(*) FROM products WHERE status IS NOT NULL AND status != '' GROUP BY status ORDER BY status ASC")
                statuses = {row["status"]: row[1] for row in cursor.fetchall()}

                # Get price ranges
                cursor.execute("SELECT MIN(price), MAX(price) FROM products WHERE price > 0")
                min_p, max_p = cursor.fetchone()

                response_data = {
                    "total_products": total,
                    "categories": categories,
                    "brands": brands,
                    "statuses": statuses,
                    "min_price": min_p or 0,
                    "max_price": max_p or 0
                }

            elif path == "/api/products":
                # Extract filters
                search_query = params.get("search", [""])[0].strip()
                category_filter = params.get("category", [""])[0].strip()
                brand_filter = params.get("brand", [""])[0].strip()
                status_filter = params.get("status", [""])[0].strip()
                min_price = params.get("min_price", [""])[0].strip()
                max_price = params.get("max_price", [""])[0].strip()
                sort_filter = params.get("sort", ["recent"])[0].strip()
                page = int(params.get("page", ["1"])[0])
                limit = int(params.get("limit", ["24"])[0])
                offset = (page - 1) * limit

                # Build SQL query dynamically
                query = "SELECT * FROM products WHERE 1=1"
                args = []

                if search_query:
                    query += " AND (name LIKE ? OR brand LIKE ? OR product_code LIKE ?)"
                    search_pat = f"%{search_query}%"
                    args.extend([search_pat, search_pat, search_pat])

                if category_filter:
                    query += " AND category = ?"
                    args.append(category_filter)

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

                # Map sort keys to ORDER BY SQL fragments safely
                sort_map = {
                    "recent": "scraped_at DESC",
                    "price-low": "price ASC",
                    "price-high": "price DESC",
                    "name-asc": "name ASC",
                    "name-desc": "name DESC"
                }
                order_by = sort_map.get(sort_filter, "scraped_at DESC")

                # Get paginated data
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
                        "price": row["price"],
                        "old_price": row["old_price"],
                        "status": row["status"],
                        "product_code": row["product_code"],
                        "brand": row["brand"],
                        "image_path": row["image_path"]
                    })

                response_data = {
                    "products": products,
                    "total": total_filtered,
                    "page": page,
                    "limit": limit,
                    "has_more": (offset + len(products)) < total_filtered
                }

            elif path.startswith("/api/product/"):
                # Get product ID
                product_id_str = path.split("/")[-1]
                product_id = int(product_id_str)

                # Fetch basic product details
                cursor.execute("SELECT * FROM products WHERE id = ?", (product_id,))
                prod_row = cursor.fetchone()

                if prod_row:
                    product = {
                        "id": prod_row["id"],
                        "name": prod_row["name"],
                        "url": prod_row["url"],
                        "category": prod_row["category"],
                        "price": prod_row["price"],
                        "old_price": prod_row["old_price"],
                        "status": prod_row["status"],
                        "product_code": prod_row["product_code"],
                        "brand": prod_row["brand"],
                        "image_path": prod_row["image_path"],
                        "scraped_at": prod_row["scraped_at"],
                        "specs": {}
                    }

                    # Fetch product specifications
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

                    response_data = product
                else:
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

def main():
    # Make sure we run in the directory of this file
    file_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(file_dir)

    print(f"[*] Starting local server on http://localhost:{PORT}")
    print(f"[*] Database file path: {os.path.abspath(DB_FILE)}")
    
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Shutting down server.")
        server.server_close()

if __name__ == "__main__":
    main()
