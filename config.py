# config.py
import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    PAYOS_CLIENT_ID = os.getenv("PAYOS_CLIENT_ID")
    PAYOS_API_KEY = os.getenv("PAYOS_API_KEY")
    PAYOS_CHECKSUM_KEY = os.getenv("PAYOS_CHECKSUM_KEY")
    PAYOS_CANCEL_URL = os.getenv("PAYOS_CANCEL_URL", "https://t.me/your_bot")
    PAYOS_RETURN_URL = os.getenv("PAYOS_RETURN_URL", "https://t.me/your_bot")
    WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or "").rstrip("/")
    MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://quocchienn:chien207@cluster0.0swxhya.mongodb.net/?appName=Cluster0")
    MONGODB_DB = os.getenv("MONGODB_DB", "telegram_shop")
    ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
