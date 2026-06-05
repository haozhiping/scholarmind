"""Seed data utility: create or reset the default admin user.

Usage:
    python -m common.db.seed           # create if not exists (default password: admin123)
    python -m common.db.seed --reset   # reset password even if user exists
"""
import argparse
import asyncio
import sys
import os

# Allow running as `python -m common.db.seed` from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.auth.security import hash_password
from common.db.mysql_client import mysql

DEFAULT_USERNAME = "admin"
DEFAULT_EMAIL = "admin@scholarmind.local"
DEFAULT_PASSWORD = "admin123"


async def seed_admin(*, reset: bool = False):
    await mysql.connect()
    try:
        existing = await mysql.fetchone(
            "SELECT id, username FROM users WHERE username=%s", DEFAULT_USERNAME
        )
        if existing and not reset:
            print(f"[SKIP] Admin user already exists (id={existing['id']}, username={existing['username']})")
            return
        if existing and reset:
            pwd_hash = hash_password(DEFAULT_PASSWORD)
            await mysql.execute(
                "UPDATE users SET password_hash=%s WHERE username=%s",
                pwd_hash, DEFAULT_USERNAME,
            )
            print(f"[OK] Admin password reset to default: {DEFAULT_PASSWORD}")
            return

        pwd_hash = hash_password(DEFAULT_PASSWORD)
        user_id = await mysql.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, 'admin')",
            DEFAULT_USERNAME, DEFAULT_EMAIL, pwd_hash,
        )
        print(f"[OK] Default admin created (id={user_id}, username={DEFAULT_USERNAME}, password={DEFAULT_PASSWORD})")
    finally:
        await mysql.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Seed default admin user")
    parser.add_argument("--reset", action="store_true", help="Reset password even if user already exists")
    args = parser.parse_args()
    asyncio.run(seed_admin(reset=args.reset))


if __name__ == "__main__":
    main()
