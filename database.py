# database.py
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError
from config import Config

logger = logging.getLogger(__name__)

_client = None
_db = None
_lock = threading.Lock()

# ============================================================
# CACHE
# ============================================================
_settings_cache = {"data": {}, "ts": 0}
_texts_cache = {"data": {}, "ts": 0}
_products_cache = {"data": [], "ts": 0}
CACHE_TTL_SETTINGS = 60
CACHE_TTL_TEXTS = 60
CACHE_TTL_PRODUCTS = 15


def _get_client():
    global _client
    with _lock:
        if _client is None:
            _client = MongoClient(
                Config.MONGODB_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
                maxPoolSize=20,
                minPoolSize=2,
                retryWrites=True,
            )
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
        db.products.create_index([("stock", ASCENDING)])
        db.orders.create_index([("order_code", ASCENDING)], unique=True)
        db.orders.create_index([("user_id", ASCENDING), ("status", ASCENDING)])
        db.orders.create_index([("email_status", ASCENDING)])
        db.orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
        db.users.create_index([("user_id", ASCENDING)], unique=True)
        db.settings.create_index([("key", ASCENDING)], unique=True)
        db.texts.create_index([("key", ASCENDING)], unique=True)
    except Exception as e:
        logger.error(f"init_db: {e}")


def _next_id(name):
    db = _get_db()
    return db.counters.find_one_and_update(
        {"_id": name}, {"$inc": {"seq": 1}}, upsert=True, return_document=True
    )["seq"]


def _invalidate_products_cache():
    _products_cache["ts"] = 0


def _invalidate_settings_cache():
    _settings_cache["ts"] = 0


def _invalidate_texts_cache():
    _texts_cache["ts"] = 0


# ============================================================
# PRODUCTS
# ============================================================
def add_product(name, description, price, stock, keys_list, emoji_id=None, requires_email=False):
    db = _get_db()
    nid = _next_id("products")
    db.products.insert_one({
        "_id": nid,
        "id": nid,
        "name": name,
        "description": description or "",
        "price": int(price),
        "stock": int(stock),
        "keys": list(keys_list or []),
        "sold": 0,
        "emoji_id": emoji_id,
        "requires_email": bool(requires_email),
        "created_at": datetime.utcnow(),
    })
    _invalidate_products_cache()
    return nid


def set_product_requires_email(product_id, requires):
    _get_db().products.update_one(
        {"id": int(product_id)},
        {"$set": {"requires_email": bool(requires)}}
    )
    _invalidate_products_cache()


def decrement_stock(pid):
    _get_db().products.update_one(
        {"id": int(pid), "stock": {"$gt": 0}},
        {"$inc": {"stock": -1, "sold": 1}}
    )
    _invalidate_products_cache()


def delete_product(pid):
    r = _get_db().products.delete_one({"id": int(pid)}).deleted_count > 0
    _invalidate_products_cache()
    return r


def delete_all_products():
    r = _get_db().products.delete_many({}).deleted_count
    _invalidate_products_cache()
    return r


def get_product(pid):
    doc = _get_db().products.find_one({"id": int(pid)})
    return _normalize_product(doc) if doc else None


def list_products(limit=5, offset=0):
    now = time.time()
    if now - _products_cache["ts"] > CACHE_TTL_PRODUCTS:
        cur = _get_db().products.find(
            {"stock": {"$gt": 0}},
            {"id": 1, "name": 1, "price": 1, "stock": 1, "sold": 1,
             "emoji_id": 1, "requires_email": 1}
        ).sort("id", ASCENDING).limit(200)
        _products_cache["data"] = [_normalize_product(d) for d in cur]
        _products_cache["ts"] = now
    all_products = _products_cache["data"]
    return all_products[int(offset):int(offset) + int(limit)]


def count_products():
    now = time.time()
    if now - _products_cache["ts"] > CACHE_TTL_PRODUCTS:
        list_products(limit=1)
    return len(_products_cache["data"])


def list_all_products(limit=None, offset=0):
    cur = _get_db().products.find({}).sort("id", ASCENDING).skip(int(offset))
    if limit:
        cur = cur.limit(int(limit))
    return [_normalize_product(d) for d in cur]


def count_all_products():
    return _get_db().products.count_documents({})


def get_available_key(pid):
    doc = _get_db().products.find_one_and_update(
        {"id": int(pid), "keys": {"$exists": True, "$ne": []}},
        {"$pop": {"keys": -1}, "$inc": {"stock": -1, "sold": 1}},
        return_document=False
    )
    _invalidate_products_cache()
    if not doc:
        return None
    keys = doc.get("keys") or []
    return keys[0] if keys else None


def _normalize_product(doc):
    if not doc:
        return None
    d = dict(doc)
    d["keys"] = json.dumps(d.get("keys") or [])
    d.setdefault("requires_email", False)
    if "_id" in d and "id" not in d:
        d["id"] = d["_id"]
    return d


# ============================================================
# ORDERS
# ============================================================
def create_order(order_id, user_id, product_id, quantity, amount, payment_method="payos"):
    db = _get_db()
    try:
        db.orders.insert_one({
            "_id": int(order_id),
            "order_code": int(order_id),
            "user_id": int(user_id),
            "product_id": int(product_id),
            "quantity": int(quantity),
            "amount": int(amount),
            "status": "pending",
            "payment_method": payment_method,
            "key_assigned": None,
            "customer_email": None,
            "email_status": None,
            "created_at": datetime.utcnow(),
            "paid_at": None,
        })
    except DuplicateKeyError:
        pass
    db.users.update_one(
        {"user_id": int(user_id)},
        {"$setOnInsert": {
            "user_id": int(user_id),
            "registered_at": datetime.utcnow(),
            "lang": "vi",
            "lang_set": False,
        }},
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


def set_order_email(order_code, email):
    _get_db().orders.update_one(
        {"order_code": int(order_code)},
        {"$set": {"customer_email": email, "email_status": "awaiting_user_confirm"}}
    )


def set_order_email_status(order_code, status):
    _get_db().orders.update_one(
        {"order_code": int(order_code)},
        {"$set": {"email_status": status}}
    )


def get_awaiting_email_order(user_id):
    doc = _get_db().orders.find_one(
        {
            "user_id": int(user_id),
            "email_status": {"$in": ["awaiting", "awaiting_user_confirm"]},
            "status": "paid",
        },
        sort=[("created_at", DESCENDING)]
    )
    return _normalize_order(doc) if doc else None


def get_pending_orders_by_user(user_id):
    cur = _get_db().orders.find(
        {"user_id": int(user_id), "status": "pending"}
    ).sort("created_at", DESCENDING).limit(20)
    return [_normalize_order(d) for d in cur]


def get_recent_orders_by_user(user_id, hours=24):
    """Lấy cả đơn pending + đơn cancelled trong `hours` giờ gần đây."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    cur = _get_db().orders.find({
        "user_id": int(user_id),
        "$or": [
            {"status": "pending"},
            {"status": "cancelled", "created_at": {"$gte": cutoff}},
        ],
    }).sort("created_at", DESCENDING).limit(20)
    return [_normalize_order(d) for d in cur]


def restore_cancelled_order(order_code, key_assigned=None):
    """Khôi phục đơn đã hủy thành paid (khi phát hiện đã thanh toán)."""
    upd = {"$set": {
        "status": "paid",
        "paid_at": datetime.utcnow(),
    }}
    if key_assigned is not None:
        upd["$set"]["key_assigned"] = key_assigned
    _get_db().orders.update_one({"order_code": int(order_code)}, upd)


def get_orders_by_user_and_ids(user_id, order_ids):
    cur = _get_db().orders.find({
        "user_id": int(user_id),
        "order_code": {"$in": [int(i) for i in order_ids]}
    })
    return {o["order_code"]: _normalize_order(o) for o in cur}


def _normalize_order(doc):
    if not doc:
        return None
    d = dict(doc)
    d["id"] = d.get("order_code") or d.get("_id")
    d.setdefault("customer_email", None)
    d.setdefault("email_status", None)
    return d


# ============================================================
# USERS
# ============================================================
def register_user(user_id, username=None, first_name=None, last_name=None):
    _get_db().users.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
            },
            "$setOnInsert": {
                "user_id": int(user_id),
                "registered_at": datetime.utcnow(),
                "lang": "vi",
                "lang_set": False,
            },
        },
        upsert=True
    )


def get_user_lang(user_id):
    doc = _get_db().users.find_one({"user_id": int(user_id)}, {"lang": 1})
    return (doc.get("lang") if doc else None) or "vi"


def set_user_lang(user_id, lang):
    _get_db().users.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {"lang": lang, "lang_set": True},
            "$setOnInsert": {
                "user_id": int(user_id),
                "registered_at": datetime.utcnow(),
            },
        },
        upsert=True
    )


def is_lang_set(user_id):
    doc = _get_db().users.find_one({"user_id": int(user_id)}, {"lang_set": 1})
    return bool(doc and doc.get("lang_set"))


def get_all_user_ids():
    return [d["user_id"] for d in _get_db().users.find({}, {"user_id": 1, "_id": 0})]


def count_users():
    return _get_db().users.count_documents({})


# ============================================================
# SETTINGS (CACHE)
# ============================================================
def _load_settings_cache():
    cur = _get_db().settings.find({}, {"key": 1, "emoji_id": 1, "_id": 0})
    _settings_cache["data"] = {d["key"]: d.get("emoji_id") for d in cur}
    _settings_cache["ts"] = time.time()


def get_setting(key):
    now = time.time()
    if now - _settings_cache["ts"] > CACHE_TTL_SETTINGS:
        _load_settings_cache()
    return _settings_cache["data"].get(key)


def set_setting(key, emoji_id):
    _get_db().settings.update_one(
        {"key": key},
        {"$set": {"emoji_id": emoji_id, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    _invalidate_settings_cache()


def delete_setting(key):
    _get_db().settings.delete_one({"key": key})
    _invalidate_settings_cache()


def get_all_settings():
    if time.time() - _settings_cache["ts"] > CACHE_TTL_SETTINGS:
        _load_settings_cache()
    return [{"key": k, "emoji_id": v} for k, v in _settings_cache["data"].items()]


# ============================================================
# TEXTS (CACHE)
# ============================================================
def _load_texts_cache():
    cur = _get_db().texts.find({}, {"key": 1, "value": 1, "_id": 0})
    _texts_cache["data"] = {d["key"]: d.get("value") for d in cur}
    _texts_cache["ts"] = time.time()


def get_text(key, default=""):
    now = time.time()
    if now - _texts_cache["ts"] > CACHE_TTL_TEXTS:
        _load_texts_cache()
    v = _texts_cache["data"].get(key)
    return v if v is not None else default


def set_text(key, value):
    _get_db().texts.update_one(
        {"key": key},
        {"$set": {"value": value, "updated_at": datetime.utcnow()}},
        upsert=True
    )
    _invalidate_texts_cache()


def delete_text(key):
    _get_db().texts.delete_one({"key": key})
    _invalidate_texts_cache()


def get_all_texts():
    if time.time() - _texts_cache["ts"] > CACHE_TTL_TEXTS:
        _load_texts_cache()
    return [{"key": k, "value": v} for k, v in _texts_cache["data"].items()]


# ============================================================
# BINANCE
# ============================================================
def get_binance_address():
    return get_text("binance_address", "")


def set_binance_address(a):
    set_text("binance_address", a)


def get_binance_network():
    return get_text("binance_network", "TRC20")


def set_binance_network(n):
    set_text("binance_network", n)


def get_usdt_rate():
    try:
        return int(get_text("usdt_rate", "25000"))
    except ValueError:
        return 25000


def set_usdt_rate(r):
    set_text("usdt_rate", str(int(r)))
