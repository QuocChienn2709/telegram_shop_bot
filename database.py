# database.py
import json
import logging
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
        db.products.create_index([("stock", ASCENDING)])
        db.orders.create_index([("order_code", ASCENDING)], unique=True)
        db.orders.create_index([("user_id", ASCENDING)])
        db.orders.create_index([("status", ASCENDING)])
        db.users.create_index([("user_id", ASCENDING)], unique=True)
        db.settings.create_index([("key", ASCENDING)], unique=True)
        logger.info("MongoDB indexes created")
    except Exception as e:
        logger.error(f"init_db index error: {e}")


def _next_id(name: str) -> int:
    db = _get_db()
    result = db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return result["seq"]


def add_product(name, description, price, stock, keys_list, emoji_id=None):
    db = _get_db()
    new_id = _next_id("products")
    doc = {
        "_id": new_id,
        "id": new_id,
        "name": name,
        "description": description or "",
        "price": int(price),
        "stock": int(stock),
        "keys": list(keys_list or []),
        "sold": 0,
        "emoji_id": emoji_id,
        "created_at": datetime.utcnow(),
    }
    db.products.insert_one(doc)
    return new_id


def delete_product(product_id):
    db = _get_db()
    result = db.products.delete_one({"id": int(product_id)})
    return result.deleted_count > 0


def delete_all_products():
    db = _get_db()
    result = db.products.delete_many({})
    return result.deleted_count


def get_product(product_id):
    db = _get_db()
    doc = db.products.find_one({"id": int(product_id)})
    return _normalize_product(doc) if doc else None


def list_products(limit=5, offset=0):
    db = _get_db()
    cursor = (
        db.products.find(
            {"stock": {"$gt": 0}},
            {"id": 1, "name": 1, "price": 1, "stock": 1, "sold": 1, "emoji_id": 1}
        )
        .sort("id", ASCENDING)
        .skip(int(offset))
        .limit(int(limit))
    )
    return [_normalize_product(d) for d in cursor]


def list_all_products():
    db = _get_db()
    cursor = db.products.find({}).sort("id", ASCENDING)
    return [_normalize_product(d) for d in cursor]


def get_available_key(product_id):
    db = _get_db()
    pid = int(product_id)
    doc = db.products.find_one_and_update(
        {"id": pid, "keys": {"$exists": True, "$ne": []}},
        {"$pop": {"keys": -1}, "$inc": {"stock": -1, "sold": 1}},
        return_document=False
    )
    if not doc:
        return None
    keys = doc.get("keys") or []
    return keys[0] if keys else None


def _normalize_product(doc):
    if not doc:
        return None
    d = dict(doc)
    d["keys"] = json.dumps(d.get("keys") or [])
    if "_id" in d and "id" not in d:
        d["id"] = d["_id"]
    return d


def create_order(order_id, user_id, product_id, quantity, amount):
    db = _get_db()
    doc = {
        "_id": int(order_id),
        "order_code": int(order_id),
        "user_id": int(user_id),
        "product_id": int(product_id),
        "quantity": int(quantity),
        "amount": int(amount),
        "status": "pending",
        "key_assigned": None,
        "created_at": datetime.utcnow(),
        "paid_at": None,
    }
    try:
        db.orders.insert_one(doc)
    except DuplicateKeyError:
        logger.warning(f"Order {order_id} đã tồn tại")
    db.users.update_one(
        {"user_id": int(user_id)},
        {"$setOnInsert": {"user_id": int(user_id), "registered_at": datetime.utcnow()}},
        upsert=True
    )


def get_order(order_id):
    db = _get_db()
    doc = db.orders.find_one({"order_code": int(order_id)})
    return _normalize_order(doc) if doc else None


def update_order_status(order_id, status, key_assigned=None):
    db = _get_db()
    update = {"$set": {"status": status}}
    if status == "paid" and key_assigned is not None:
        update["$set"]["key_assigned"] = key_assigned
        update["$set"]["paid_at"] = datetime.utcnow()
    db.orders.update_one({"order_code": int(order_id)}, update)


def get_pending_orders_by_user(user_id):
    db = _get_db()
    cursor = (
        db.orders.find({"user_id": int(user_id), "status": "pending"})
        .sort("created_at", DESCENDING)
    )
    return [_normalize_order(d) for d in cursor]


def _normalize_order(doc):
    if not doc:
        return None
    d = dict(doc)
    d["id"] = d.get("order_code") or d.get("_id")
    return d


def register_user(user_id, username=None, first_name=None, last_name=None):
    db = _get_db()
    db.users.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
            },
            "$setOnInsert": {"user_id": int(user_id), "registered_at": datetime.utcnow()},
        },
        upsert=True
    )


def get_setting(key):
    db = _get_db()
    doc = db.settings.find_one({"key": key})
    return doc.get("emoji_id") if doc else None


def set_setting(key, emoji_id):
    db = _get_db()
    db.settings.update_one(
        {"key": key},
        {"$set": {"emoji_id": emoji_id, "updated_at": datetime.utcnow()}},
        upsert=True
    )


def delete_setting(key):
    db = _get_db()
    db.settings.delete_one({"key": key})


def get_all_settings():
    db = _get_db()
    return list(db.settings.find({}, {"key": 1, "emoji_id": 1, "_id": 0}))
