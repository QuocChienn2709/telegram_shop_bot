# payos_client.py
import hashlib, hmac, json, logging, time, requests
from config import Config

logger = logging.getLogger(__name__)
PAYOS_BASE_URL = "https://api-merchant.payos.vn/v2"

def create_payment_link(order_code, amount, description, buyer_name=None, buyer_email=None):
    headers = {
        "x-client-id": Config.PAYOS_CLIENT_ID,
        "x-api-key": Config.PAYOS_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "orderCode": order_code, "amount": amount, "description": description[:25],
        "cancelUrl": Config.PAYOS_CANCEL_URL, "returnUrl": Config.PAYOS_RETURN_URL,
        "buyerName": buyer_name or "", "buyerEmail": buyer_email or "",
        "expiredAt": int(time.time()) + 3600 * 24,
    }
    sd = {"amount": payload["amount"], "cancelUrl": payload["cancelUrl"],
          "description": payload["description"], "orderCode": payload["orderCode"],
          "returnUrl": payload["returnUrl"]}
    sign_str = "&".join(f"{k}={sd[k]}" for k in sorted(sd.keys()))
    payload["signature"] = hmac.new(Config.PAYOS_CHECKSUM_KEY.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    try:
        r = requests.post(f"{PAYOS_BASE_URL}/payment-requests", headers=headers, json=payload, timeout=10)
        d = r.json()
        if d.get("code") == "00":
            return d["data"]["checkoutUrl"], order_code
        return None, d.get("desc", "Lỗi PayOS")
    except Exception as e:
        logger.error(f"create_payment_link: {e}")
        return None, str(e)

def verify_payment_webhook(body, sig_header=None):
    try:
        data = body.get("data", {})
        sig = (body.get("signature") or sig_header or "").strip()
        if not data or not sig: return False
        kb = Config.PAYOS_CHECKSUM_KEY.encode()
        for s in [
            json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False),
            json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=True),
            json.dumps(data, separators=(",", ":"), ensure_ascii=False),
            json.dumps(data, separators=(",", ":"), ensure_ascii=True),
        ]:
            if hmac.compare_digest(hmac.new(kb, s.encode(), hashlib.sha256).hexdigest(), sig):
                return True
        return False
    except Exception as e:
        logger.error(f"verify: {e}")
        return False

def get_payment_status(order_code):
    try:
        r = requests.get(f"{PAYOS_BASE_URL}/payment-requests/{order_code}",
                         headers={"x-client-id": Config.PAYOS_CLIENT_ID, "x-api-key": Config.PAYOS_API_KEY},
                         timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"get_payment_status: {e}")
        return None
