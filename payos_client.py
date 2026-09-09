# payos_client.py
import hashlib
import hmac
import json
import time
import requests
from config import Config

PAYOS_BASE_URL = "https://api-merchant.payos.vn/v2"

def generate_signature(data, checksum_key):
    """Tạo chữ ký HMAC-SHA256 theo chuẩn PayOS"""
    sorted_data = sorted(data.items())
    sign_str = "&".join([f"{k}={v}" for k, v in sorted_data])
    return hmac.new(
        checksum_key.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def create_payment_link(order_code, amount, description, buyer_name=None, buyer_email=None):
    """
    Tạo link thanh toán PayOS.
    Trả về: (payment_url, order_code) hoặc (None, error_msg)
    """
    headers = {
        "x-client-id": Config.PAYOS_CLIENT_ID,
        "x-api-key": Config.PAYOS_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "orderCode": order_code,  # int, duy nhất
        "amount": amount,         # VND
        "description": description[:25],  # tối đa 25 ký tự
        "cancelUrl": Config.PAYOS_CANCEL_URL,
        "returnUrl": Config.PAYOS_RETURN_URL,
        "buyerName": buyer_name or "",
        "buyerEmail": buyer_email or "",
        "expiredAt": int(time.time()) + 3600 * 24,  # 24h
    }

    # Tạo chữ ký
    signature_data = {
        "amount": payload["amount"],
        "cancelUrl": payload["cancelUrl"],
        "description": payload["description"],
        "orderCode": payload["orderCode"],
        "returnUrl": payload["returnUrl"]
    }
    payload["signature"] = generate_signature(signature_data, Config.PAYOS_CHECKSUM_KEY)

    try:
        resp = requests.post(
            f"{PAYOS_BASE_URL}/payment-requests",
            headers=headers,
            json=payload,
            timeout=10
        )
        data = resp.json()
        if data.get("code") == "00":
            return data["data"]["checkoutUrl"], order_code
        else:
            return None, data.get("desc", "Lỗi PayOS không xác định")
    except Exception as e:
        return None, str(e)

def verify_payment_webhook(webhook_data, signature_header):
    """
    Xác thực webhook từ PayOS.
    webhook_data: dict nhận từ POST
    signature_header: giá trị header 'x-payos-signature'
    Trả về: bool
    """
    # Loại bỏ trường signature trong data (nếu có) trước khi tạo chữ ký
    data_copy = webhook_data.copy()
    if "signature" in data_copy:
        del data_copy["signature"]
    if "webhookUrl" in data_copy:
        del data_copy["webhookUrl"]  # PayOS không yêu cầu webhookUrl trong chữ ký

    sorted_data = sorted(data_copy.items())
    sign_str = "&".join([f"{k}={v}" for k, v in sorted_data])
    expected = hmac.new(
        Config.PAYOS_CHECKSUM_KEY.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
