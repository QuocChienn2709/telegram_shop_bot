# payos_client.py
import hashlib
import hmac
import json
import logging
import time
import requests
from config import Config

logger = logging.getLogger(__name__)

PAYOS_BASE_URL = "https://api-merchant.payos.vn/v2"


def create_payment_link(order_code, amount, description, buyer_name=None, buyer_email=None):
    """
    description PHẢI là mã đơn hàng để dễ tra soát, ví dụ: "DH1234567890"
    """
    headers = {
        "x-client-id": Config.PAYOS_CLIENT_ID,
        "x-api-key": Config.PAYOS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "orderCode": order_code,
        "amount": amount,
        "description": description[:25],
        "cancelUrl": Config.PAYOS_CANCEL_URL,
        "returnUrl": Config.PAYOS_RETURN_URL,
        "buyerName": buyer_name or "",
        "buyerEmail": buyer_email or "",
        "expiredAt": int(time.time()) + 3600 * 24,
    }
    signature_data = {
        "amount": payload["amount"],
        "cancelUrl": payload["cancelUrl"],
        "description": payload["description"],
        "orderCode": payload["orderCode"],
        "returnUrl": payload["returnUrl"]
    }
    sign_str = "&".join(f"{k}={signature_data[k]}" for k in sorted(signature_data.keys()))
    payload["signature"] = hmac.new(
        Config.PAYOS_CHECKSUM_KEY.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    try:
        resp = requests.post(f"{PAYOS_BASE_URL}/payment-requests", headers=headers, json=payload, timeout=10)
        data = resp.json()
        if data.get("code") == "00":
            return data["data"]["checkoutUrl"], order_code
        return None, data.get("desc", "Lỗi PayOS")
    except Exception as e:
        logger.error(f"create_payment_link error: {e}")
        return None, str(e)


def verify_payment_webhook(webhook_body, signature_header=None):
    try:
        data = webhook_body.get("data", {})
        signature = (webhook_body.get("signature") or signature_header or "").strip()
        if not data or not signature:
            return False
        key_bytes = Config.PAYOS_CHECKSUM_KEY.encode("utf-8")
        variants = [
            json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False),
            json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=True),
            json.dumps(data, separators=(",", ":"), sort_keys=False, ensure_ascii=False),
            json.dumps(data, separators=(",", ":"), sort_keys=False, ensure_ascii=True),
        ]
        for s in variants:
            expected = hmac.new(key_bytes, s.encode("utf-8"), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                return True
        return False
    except Exception as e:
        logger.error(f"verify_payment_webhook error: {e}")
        return False


def get_payment_status(order_code):
    headers = {
        "x-client-id": Config.PAYOS_CLIENT_ID,
        "x-api-key": Config.PAYOS_API_KEY
    }
    try:
        resp = requests.get(f"{PAYOS_BASE_URL}/payment-requests/{order_code}", headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"get_payment_status error: {e}")
        return None
