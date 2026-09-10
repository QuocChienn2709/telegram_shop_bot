# database.py
import os
import sqlite3
import json
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "shop.db")

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                stock INTEGER DEFAULT 0,
                keys TEXT,
                sold INTEGER DEFAULT 0,
                emoji_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER DEFAULT 1,
                amount INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                key_assigned TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")

        # Migration: thêm cột emoji_id nếu DB cũ
        cols = [row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()]
        if "emoji_id" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN emoji_id TEXT")

def add_product(name, description, price, stock, keys_list, emoji_id=None):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO products (name, description, price, stock, keys, emoji_id) VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, price, stock, json.dumps(keys_list), emoji_id)
        )
        return cur.lastrowid

def update_product(product_id, name=None, description=None, price=None, emoji_id=None):
    fields, values = [], []
    if name is not None:
        fields.append("name = ?"); values.append(name)
    if description is not None:
        fields.append("description = ?"); values.append(description)
    if price is not None:
        fields.append("price = ?"); values.append(price)
    if emoji_id is not None:
        fields.append("emoji_id = ?"); values.append(emoji_id)
    if not fields:
        return False
    values.append(product_id)
    with get_db() as conn:
        conn.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", values)
    return True

def delete_product(product_id):
    with get_db() as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return cur.rowcount > 0

def delete_all_products():
    with get_db() as conn:
        cur = conn.execute("DELETE FROM products")
        return cur.rowcount

def get_product(product_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return dict(row) if row else None

def list_products(limit=5, offset=0):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, price, stock, sold, emoji_id FROM products WHERE stock > 0 ORDER BY id LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]

def list_all_products():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, description, price, stock, sold, emoji_id FROM products ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

def update_stock(product_id, new_stock):
    with get_db() as conn:
        conn.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))

def get_available_key(product_id):
    with get_db() as conn:
        row = conn.execute("SELECT keys FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return None
        keys = json.loads(row["keys"] or "[]")
        if not keys:
            return None
        key = keys.pop(0)
        conn.execute(
            "UPDATE products SET keys = ?, stock = stock - 1, sold = sold + 1 WHERE id = ?",
            (json.dumps(keys), product_id)
        )
        return key

def create_order(order_id, user_id, product_id, quantity, amount):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO orders (id, user_id, product_id, quantity, amount) VALUES (?, ?, ?, ?, ?)",
            (order_id, user_id, product_id, quantity, amount)
        )
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

def get_order(order_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None

def update_order_status(order_id, status, key_assigned=None):
    with get_db() as conn:
        if status == "paid" and key_assigned:
            conn.execute(
                "UPDATE orders SET status = ?, key_assigned = ?, paid_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, key_assigned, order_id)
            )
        else:
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))

def get_pending_orders_by_user(user_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
