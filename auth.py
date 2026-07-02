import random
import time
import hashlib
import os
import logging
from whatsapp import GreenAPIWhatsApp
from database import get_db, get_user_by_phone, update_last_login

logger = logging.getLogger(__name__)

OTP_EXPIRY = 300       # 5 minutes
MAX_ATTEMPTS = 5

# In-memory store: {normalized_phone: {code, expires_at, attempts}}
_otp_store = {}


def _normalize(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("972") and len(digits) == 12:
        digits = "0" + digits[3:]
    return digits


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with PBKDF2. Returns (hash_str, salt)."""
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 260000)
    return dk.hex(), salt


def _verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored 'salt$hash' string."""
    try:
        salt, expected = stored_hash.split("$", 1)
        actual, _ = _hash_password(password, salt)
        return actual == expected
    except Exception:
        return False


def hash_for_storage(password: str) -> str:
    """Return 'salt$hash' string suitable for DB storage."""
    h, salt = _hash_password(password)
    return f"{salt}${h}"


def is_allowed(phone: str) -> bool:
    """Check if phone exists in users table and is active."""
    conn = get_db()
    try:
        user = get_user_by_phone(conn, phone)
        return user is not None
    finally:
        conn.close()


def get_user(phone: str):
    """Return user row for phone, or None."""
    conn = get_db()
    try:
        row = get_user_by_phone(conn, phone)
        return dict(row) if row else None
    finally:
        conn.close()


def verify_password_login(phone: str, password: str) -> bool:
    """
    Attempt password login. Returns True if phone+password match.
    Also records last_login on success.
    """
    user = get_user(phone)
    if not user:
        return False
    stored = user.get("password_hash") or ""
    if not stored:
        return False
    if not _verify_password(password, stored):
        return False
    conn = get_db()
    try:
        update_last_login(conn, phone)
    finally:
        conn.close()
    return True


def set_password(phone: str, new_password: str) -> bool:
    """Set or update a user's password."""
    conn = get_db()
    try:
        norm = _normalize(phone)
        stored = hash_for_storage(new_password)
        conn.execute("UPDATE users SET password_hash=? WHERE phone=?", (stored, norm))
        conn.commit()
        return conn.total_changes > 0
    except Exception as e:
        logger.error(f"set_password failed: {e}")
        return False
    finally:
        conn.close()


def has_password(phone: str) -> bool:
    """Return True if user has a password set."""
    user = get_user(phone)
    return bool(user and user.get("password_hash"))


def send_otp(phone: str) -> bool:
    norm = _normalize(phone)
    if not norm:
        return False

    code = str(random.randint(100000, 999999))
    _otp_store[norm] = {
        "code": code,
        "expires_at": time.time() + OTP_EXPIRY,
        "attempts": 0,
    }

    client = GreenAPIWhatsApp()
    if not client.instance_id or not client.api_token:
        logger.warning(f"WhatsApp not configured — OTP for {norm}: {code}")
        return True

    message = f"קוד האימות שלך למערכת ניטור ובקרה:\n\n*{code}*\n\nהקוד תקף ל-5 דקות."
    sent = client.send_text(message, phone)
    if not sent:
        logger.error(f"Failed to send OTP to {norm}")
    return sent


def verify_otp(phone: str, code: str) -> bool:
    norm = _normalize(phone)
    entry = _otp_store.get(norm)
    if not entry:
        return False
    if time.time() > entry["expires_at"]:
        del _otp_store[norm]
        return False
    entry["attempts"] += 1
    if entry["attempts"] > MAX_ATTEMPTS:
        del _otp_store[norm]
        return False
    if entry["code"] != code.strip():
        return False
    del _otp_store[norm]
    conn = get_db()
    try:
        update_last_login(conn, phone)
    finally:
        conn.close()
    return True
