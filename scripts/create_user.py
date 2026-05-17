from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import get_db, hash_password, init_db, iso_utc, validate_email, validate_password_strength


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or reset a Memory Reminder user account.")
    parser.add_argument("--email", required=True, help="Login email.")
    parser.add_argument("--name", default="", help="Display name. Defaults to the email prefix.")
    parser.add_argument("--password", default="", help="Password. Omit to enter it securely.")
    parser.add_argument("--reset-password", action="store_true", help="Reset password if the user already exists.")
    return parser.parse_args()


def read_password(initial: str) -> str:
    if initial:
        return initial
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("两次输入的密码不一致")
    return password


def main() -> None:
    args = parse_args()
    email = args.email.strip().lower()
    name = args.name.strip() or email.split("@")[0]
    password = read_password(args.password)

    validate_email(email)
    validate_password_strength(password)
    init_db()

    with get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing and not args.reset_password:
            raise SystemExit("账号已存在。如需重置密码，请添加 --reset-password。")
        if existing:
            conn.execute(
                "UPDATE users SET name = ?, password_hash = ? WHERE email = ?",
                (name[:40], hash_password(password), email),
            )
            print(f"已重置账号密码：{email}")
            return
        try:
            conn.execute(
                "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (email, name[:40], hash_password(password), iso_utc()),
            )
        except sqlite3.IntegrityError as exc:
            raise SystemExit(f"账号创建失败：{exc}") from exc
    print(f"已创建账号：{email}")


if __name__ == "__main__":
    main()
