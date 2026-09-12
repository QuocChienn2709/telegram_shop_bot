# database.py
import json, logging
from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from config import Config

logger = logging.getLogger(__name__)
_client = None
_db = None

def _get_client():
    global _client
    if _client is None:
        _client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=10000)
        _client.admin.command("ping")
        logger.info("MongoDB connected")
    return _client

def _get_db():
    global _db
    if _db is None:
        _db = _get_client()[Config.MONGODB_DB]
    return _db

def get_db():
    return _get_db()

def init_db():
    db = _get_db()
    try:
        db.products.create_index([("id", ASCENDING)], unique=True)
        db.orders.create_index([("order_code", ASCENDING)], unique=True)
        db.orders.create_index([("user_id", ASCENDING)])
        db.users.create_index([("user_id", ASCENDING)], unique=True)
        db.settings.create_index([("key", ASCENDING)], unique=True)
        db.texts.create_index([("key", ASCENDING)], unique=True)
    except Exception as e:
        logger.error(f"init_db: {e}")

def _next_id(name):
    db = _get_db()
    return db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True)["seq"]

# PRODUCTS
def add_product(name, description, price, stock, keys_list, emoji_id=None):
    db = _get_db()
    nid = _next_id("products")
    db.products.insert_one({
        "_id": nid, "id": nid, "name": name, "description": description or "",
        "price": int(price), "stock": int(stock), "keys": list(keys_list or []),
        "sold": 0, "emoji_id": emoji_id, "created_at": datetime.utcnow(),
    })
    return nid

def delete_product(pid):
    return _get_db().products.delete_one({"id": int(pid)}).deleted_count > 0

def delete_all_products():
    return _get_db().products.delete_many({}).deleted_count

def get_product(pid):
    doc = _get_db().products.find_one({"id": int(pid)})
    return _normalize_product(doc) if doc else None

def list_products(limit=5, offset=0):
    cur = _get_db().products.find(
        {"stock": {"$gt": 0}},
        {"id": 1, "name": 1, "price": 1, "stock": 1, "sold": 1, "emoji_id": 1}
    ).sort("id", ASCENDING).skip(int(offset)).limit(int(limit))
    return [_normalize_product(d) for d in cur]

def count_products():
    return _get_db().products.count_documents({"stock": {"$gt": 0}})

def list_all_products(limit=None, offset=0):
    cur = _get_db().products.find({}).sort("id", ASCENDING).skip(int(offset))
    if limit: cur = cur.limit(int(limit))
    return [_normalize_product(d) for d in cur]

def count_all_products():
    return _get_db().products.count_documents({})

def get_available_key(pid):
    doc = _get_db().products.find_one_and_update(
        {"id": int(pid), "keys": {"$exists": True, "$ne": []}},
        {"$pop": {"keys": -1}, "$inc": {"stock": -1, "sold": 1}},
        return_document=False
    )
    if not doc: return None
    keys = doc.get("keys") or []
    return keys[0] if keys else None

def _normalize_product(doc):
    if not doc: return None
    d = dict(doc)
    d["keys"] = json.dumps(d.get("keys") or [])
    if "_id" in d and "id" not in d: d["id"] = d["_id"]
    return d

# ORDERS
def create_order(order_id, user_id, product_id, quantity, amount, payment_method="payos"):
    db = _get_db()
    try:
        db.orders.insert_one({
            "_id": int(order_id), "order_code": int(order_id),
            "user_id": int(user_id), "product_id": int(product_id),
            "quantity": int(quantity), "amount": int(amount),
            "status": "pending", "payment_method": payment_method,
            "key_assigned": None, "created_at": datetime.utcnow(), "paid_at": None,
        })
    except DuplicateKeyError:
        pass
    db.users.update_one(
        {"user_id": int(user_id)},
        {"$setOnInsert": {"user_id": int(user_id), "registered_at": datetime.utcnow(), "lang": "vi", "lang_set": False}},
        upsert=True
    )

def get_order(order_id):
    doc = _get_db().orders.find_one({"order_code": int(order_id)})
    return _normalize_order(doc) if doc else None

def update_order_status(order_id, status, key_assigned=None):
    upd = {"$set": {"status": status}}
    if status == "paid" and key_assigned is not None:
        upd["$set"]["key_assigned"] = key_assigned
        upd["$set"]["paid_at"] = datetime.utcnow()
    _get_db().orders.update_one({"order_code": int(order_id)}, upd)

def get_pending_orders_by_user(user_id):
    cur = _get_db().orders.find({"user_id": int(user_id), "status": "pending"}).sort("created_at", DESCENDING)
    return [_normalize_order(d) for d in cur]

def _normalize_order(doc):
    if not doc: return None
    d = dict(doc)
    d["id"] = d.get("order_code") or d.get("_id")
    return d

# USERS
def register_user(user_id, username=None, first_name=None, last_name=None):
    _get_db().users.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {"username": username, "first_name": first_name, "last_name": last_name},
            "$setOnInsert": {"user_id": int(user_id), "registered_at": datetime.utcnow(), "lang": "vi", "lang_set": False},
        },
        upsert=True
    )

def get_user_lang(user_id):
    doc = _get_db().users.find_one({"user_id": int(user_id)}, {"lang": 1})
    return (doc.get("lang") if doc else None) or "vi"

def set_user_lang(user_id, lang):
    _get_db().users.update_one(
        {"user_id": int(user_id)},
        {"$set": {"lang": lang, "lang_set": True}, "$setOnInsert": {"user_id": int(user_id), "registered_at": datetime.utcnow()}},
        upsert=True
    )

def is_lang_set(user_id):
    doc = _get_db().users.find_one({"user_id": int(user_id)}, {"lang_set": 1})
    return bool(doc and doc.get("lang_set"))

def get_all_user_ids():
    return [d["user_id"] for d in _get_db().users.find({}, {"user_id": 1, "_id": 0})]

def count_users():
    return _get_db().users.count_documents({})

# SETTINGS
def get_setting(key):
    doc = _get_db().settings.find_one({"key": key})
    return doc.get("emoji_id") if doc else None

def set_setting(key, emoji_id):
    _get_db().settings.update_one({"key": key}, {"$set": {"emoji_id": emoji_id, "updated_at": datetime.utcnow()}}, upsert=True)

def delete_setting(key):
    _get_db().settings.delete_one({"key": key})

def get_all_settings():
    return list(_get_db().settings.find({}, {"key": 1, "emoji_id": 1, "_id": 0}))

# TEXTS - có emoji support
def get_text(key, default=""):
    """Trả về text đã có emoji placeholder nếu có."""
    doc = _get_db().texts.find_one({"key": key})
    if not doc: return default
    val = doc.get("value")
    if val is None: return default
    eid = doc.get("emoji_id")
    if eid:
        return f'<tg-emoji emoji-id="{eid}">🎁</tg-emoji> {val}'
    return val

def get_text_raw(key):
    """Trả về raw value + emoji_id riêng (dùng cho admin view)."""
    doc = _get_db().texts.find_one({"key": key})
    if not doc: return None, None
    return doc.get("value"), doc.get("emoji_id")

def set_text(key, value, emoji_id=None, keep_emoji=True):
    """
    Set text.
    - keep_emoji=True: nếu emoji_id=None thì giữ emoji cũ
    - keep_emoji=False: xóa emoji cũ
    """
    db = _get_db()
    upd = {"value": value, "updated_at": datetime.utcnow()}
    if emoji_id is not None:
        upd["emoji_id"] = emoji_id
    elif not keep_emoji:
        # Xóa emoji cũ
        db.texts.update_one({"key": key}, {"$unset": {"emoji_id": ""}, "$set": upd}, upsert=True)
        return
    db.texts.update_one({"key": key}, {"$set": upd}, upsert=True)

def delete_text(key):
    _get_db().texts.delete_one({"key": key})

def get_all_texts():
    return list(_get_db().texts.find({}, {"key": 1, "value": 1, "emoji_id": 1, "_id": 0}))

# BINANCE
def get_binance_address(): return get_text("binance_address", "")
def set_binance_address(a): set_text("binance_address", a, keep_emoji=False)
def get_binance_network(): return get_text("binance_network", "TRC20")
def set_binance_network(n): set_text("binance_network", n, keep_emoji=False)
def get_usdt_rate():
    try: return int(get_text("usdt_rate", "25000"))
    except ValueError: return 25000
def set_usdt_rate(r): set_text("usdt_rate", str(int(r)), keep_emoji=False)
