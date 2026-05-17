from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", DATA_DIR / "memory.sqlite3"))
APP_TZ = ZoneInfo(os.environ.get("APP_TZ", "Asia/Shanghai"))
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8000"))
COOKIE_NAME = "memory_sid"
SESSION_DAYS = 30
PASSWORD_ITERATIONS = 220_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(dt: datetime | None = None) -> str:
    value = dt or utc_now()
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_client_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HttpError(400, "提醒时间格式不正确") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=APP_TZ)
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def default_next_review() -> str:
    local = datetime.now(APP_TZ) + timedelta(days=1)
    local = local.replace(hour=8, minute=0, second=0, microsecond=0)
    return iso_utc(local.astimezone(timezone.utc))


def next_morning_after(days: int) -> str:
    local = datetime.now(APP_TZ) + timedelta(days=days)
    local = local.replace(hour=8, minute=0, second=0, microsecond=0)
    return iso_utc(local.astimezone(timezone.utc))


def today_end_utc() -> str:
    local = datetime.now(APP_TZ)
    end = local.replace(hour=23, minute=59, second=59, microsecond=0)
    return iso_utc(end.astimezone(timezone.utc))


def normalize_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = str(value).replace("，", ",").replace("、", ",").split(",")
    tags: list[str] = []
    seen = set()
    for item in raw_items:
        tag = str(item).strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag[:24])
    return tags[:8]


def make_title(content: str) -> str:
    first_line = content.strip().splitlines()[0] if content.strip() else "未命名记忆"
    return first_line[:60]


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def validate_email(email: str) -> None:
    if not EMAIL_RE.match(email) or len(email) > 120:
        raise HttpError(400, "邮箱格式不正确")


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HttpError(400, "密码至少需要 8 位")
    if len(password) > 128:
        raise HttpError(400, "密码不能超过 128 位")
    if password.strip() != password:
        raise HttpError(400, "密码开头和结尾不能有空格")
    if password.isdigit() or password.isalpha():
        raise HttpError(400, "密码需要同时包含字母和数字或符号")


def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',
                importance INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'active',
                interval_days INTEGER NOT NULL DEFAULT 1,
                review_count INTEGER NOT NULL DEFAULT 0,
                next_review_at TEXT NOT NULL,
                last_reviewed_at TEXT,
                last_notified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cards_user_status_next
                ON cards(user_id, status, next_review_at);
            CREATE INDEX IF NOT EXISTS idx_sessions_user
                ON sessions(user_id, expires_at);
            """
        )


def row_to_user(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


def row_to_card(row: sqlite3.Row) -> dict:
    try:
        tags = json.loads(row["tags"])
    except json.JSONDecodeError:
        tags = []
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "tags": tags,
        "importance": row["importance"],
        "status": row["status"],
        "intervalDays": row["interval_days"],
        "reviewCount": row["review_count"],
        "nextReviewAt": row["next_review_at"],
        "lastReviewedAt": row["last_reviewed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    expires_at = iso_utc(utc_now() + timedelta(days=SESSION_DAYS))
    conn.execute(
        "INSERT INTO sessions (id, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (session_id, user_id, expires_at, iso_utc()),
    )
    return session_id


def cookie_header(session_id: str) -> str:
    max_age = SESSION_DAYS * 24 * 60 * 60
    return f"{COOKIE_NAME}={session_id}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"


def clear_cookie_header() -> str:
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def compute_next_interval(row: sqlite3.Row, result: str) -> tuple[int, str]:
    current = max(1, int(row["interval_days"] or 1))
    review_count = max(0, int(row["review_count"] or 0))
    ladder = [1, 3, 7, 14, 30, 60, 120, 180]

    if result == "remembered":
        next_days = ladder[min(review_count + 1, len(ladder) - 1)]
    elif result == "fuzzy":
        next_days = 2 if current >= 7 else 1
    elif result == "forgotten":
        next_days = 1
    else:
        raise HttpError(400, "复习结果不正确")

    return next_days, next_morning_after(next_days)


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


def send_email(to_email: str, subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_email = os.environ["SMTP_FROM"]
    use_ssl = os.environ.get("SMTP_SSL", "").lower() in {"1", "true", "yes"} or port == 465
    use_tls = os.environ.get("SMTP_TLS", "true").lower() not in {"0", "false", "no"}

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=20) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)


def process_due_email_reminders() -> None:
    if not smtp_configured():
        return
    now = iso_utc()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT cards.*, users.email, users.name
            FROM cards
            JOIN users ON users.id = cards.user_id
            WHERE cards.status = 'active'
              AND cards.next_review_at <= ?
              AND (cards.last_notified_at IS NULL OR cards.last_notified_at < cards.next_review_at)
            ORDER BY users.id, cards.next_review_at ASC
            LIMIT 200
            """,
            (now,),
        ).fetchall()

        grouped: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["user_id"], []).append(row)

        for user_id, user_rows in grouped.items():
            email = user_rows[0]["email"]
            lines = [f"{user_rows[0]['name']}，今天有 {len(user_rows)} 条内容需要复习：", ""]
            for row in user_rows[:12]:
                tags = ", ".join(json.loads(row["tags"] or "[]"))
                tag_part = f" [{tags}]" if tags else ""
                lines.append(f"- {row['title']}{tag_part}")
            if len(user_rows) > 12:
                lines.append(f"- 还有 {len(user_rows) - 12} 条，请打开记忆提醒工具查看。")
            lines.append("")
            lines.append("打开应用后，按“记住了 / 模糊 / 没记住”反馈，系统会安排下一次提醒。")
            send_email(email, "记忆提醒：今天该复习了", "\n".join(lines))
            conn.execute(
                """
                UPDATE cards
                SET last_notified_at = ?, updated_at = ?
                WHERE user_id = ? AND status = 'active' AND next_review_at <= ?
                """,
                (now, now, user_id, now),
            )


def reminder_worker() -> None:
    interval = max(30, int(os.environ.get("REMINDER_POLL_SECONDS", "60")))
    while True:
        try:
            process_due_email_reminders()
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


class MemoryHandler(BaseHTTPRequestHandler):
    server_version = "MemoryReminder/0.1"

    def log_message(self, format, *args):
        print("[%s] %s" % (self.log_date_time_string(), format % args))

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self.handle_api(method, parsed)
            else:
                self.serve_static(parsed.path)
        except HttpError as exc:
            self.send_json(exc.status, {"error": exc.message})
        except Exception:
            traceback.print_exc()
            self.send_json(500, {"error": "服务器内部错误"})

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HttpError(400, "JSON 格式不正确") from exc
        if not isinstance(value, dict):
            raise HttpError(400, "请求体必须是对象")
        return value

    def send_json(self, status: int, payload: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            target = STATIC_DIR / "index.html"
        elif request_path.startswith("/static/"):
            rel = unquote(request_path[len("/static/") :])
            target = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
                raise HttpError(404, "文件不存在")
        else:
            target = STATIC_DIR / "index.html"

        if not target.exists() or not target.is_file():
            raise HttpError(404, "文件不存在")

        mime_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def get_session_id(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        parsed = cookies.SimpleCookie(raw)
        morsel = parsed.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def current_user(self) -> dict | None:
        session_id = self.get_session_id()
        if not session_id:
            return None
        now = iso_utc()
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT users.id, users.email, users.name
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.id = ? AND sessions.expires_at > ?
                """,
                (session_id, now),
            ).fetchone()
        return row_to_user(row)

    def require_user(self) -> dict:
        user = self.current_user()
        if not user:
            raise HttpError(401, "请先登录")
        return user

    def handle_api(self, method: str, parsed) -> None:
        path = parsed.path
        if method == "GET" and path == "/api/health":
            self.send_json(200, {"ok": True, "time": iso_utc()})
            return
        if path.startswith("/api/auth/"):
            self.handle_auth(method, path)
            return
        if path == "/api/cards":
            if method == "GET":
                self.list_cards(parsed)
                return
            if method == "POST":
                self.create_card()
                return
        if path.startswith("/api/cards/"):
            self.handle_card_item(method, path)
            return
        if method == "GET" and path == "/api/tags":
            self.list_tags()
            return
        if method == "GET" and path == "/api/reminders/due":
            self.due_reminders()
            return
        raise HttpError(404, "接口不存在")

    def handle_auth(self, method: str, path: str) -> None:
        if method == "GET" and path == "/api/auth/me":
            self.send_json(200, {"user": self.current_user()})
            return

        if method == "POST" and path == "/api/auth/register":
            if os.environ.get("ALLOW_REGISTRATION", "true").lower() in {"0", "false", "no"}:
                raise HttpError(403, "当前服务器已关闭新用户注册")
            data = self.read_json()
            email = str(data.get("email", "")).strip().lower()
            name = str(data.get("name", "")).strip() or email.split("@")[0]
            password = str(data.get("password", ""))
            password_confirm = data.get("passwordConfirm")
            validate_email(email)
            validate_password_strength(password)
            if password_confirm is not None and password != str(password_confirm):
                raise HttpError(400, "两次输入的密码不一致")
            with get_db() as conn:
                try:
                    cursor = conn.execute(
                        "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
                        (email, name[:40], hash_password(password), iso_utc()),
                    )
                except sqlite3.IntegrityError as exc:
                    raise HttpError(409, "这个邮箱已经注册过") from exc
                session_id = create_session(conn, cursor.lastrowid)
            self.send_json(201, {"user": {"id": cursor.lastrowid, "email": email, "name": name[:40]}}, {"Set-Cookie": cookie_header(session_id)})
            return

        if method == "POST" and path == "/api/auth/login":
            data = self.read_json()
            email = str(data.get("email", "")).strip().lower()
            password = str(data.get("password", ""))
            validate_email(email)
            with get_db() as conn:
                row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
                if not row or not verify_password(password, row["password_hash"]):
                    raise HttpError(401, "邮箱或密码不正确")
                session_id = create_session(conn, row["id"])
            self.send_json(200, {"user": row_to_user(row)}, {"Set-Cookie": cookie_header(session_id)})
            return

        if method == "POST" and path == "/api/auth/logout":
            session_id = self.get_session_id()
            if session_id:
                with get_db() as conn:
                    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self.send_json(200, {"ok": True}, {"Set-Cookie": clear_cookie_header()})
            return

        raise HttpError(404, "认证接口不存在")

    def list_cards(self, parsed) -> None:
        user = self.require_user()
        params = parse_qs(parsed.query)
        view = params.get("view", ["today"])[0]
        q = params.get("q", [""])[0].strip()
        tag = params.get("tag", [""])[0].strip()

        clauses = ["user_id = ?"]
        args: list = [user["id"]]
        if view == "archived":
            clauses.append("status = 'archived'")
        else:
            clauses.append("status = 'active'")

        if view == "today":
            clauses.append("next_review_at <= ?")
            args.append(today_end_utc())
        elif view == "due":
            clauses.append("next_review_at <= ?")
            args.append(iso_utc())
        elif view == "upcoming":
            clauses.append("next_review_at > ?")
            args.append(today_end_utc())

        if q:
            clauses.append("(title LIKE ? OR content LIKE ?)")
            args.extend([f"%{q}%", f"%{q}%"])
        if tag:
            clauses.append("tags LIKE ?")
            args.append(f'%"{tag}"%')

        sql = f"""
            SELECT * FROM cards
            WHERE {' AND '.join(clauses)}
            ORDER BY
                CASE importance WHEN 3 THEN 0 WHEN 2 THEN 1 ELSE 2 END,
                next_review_at ASC,
                updated_at DESC
            LIMIT 300
        """
        with get_db() as conn:
            rows = conn.execute(sql, args).fetchall()
        self.send_json(200, {"cards": [row_to_card(row) for row in rows]})

    def create_card(self) -> None:
        user = self.require_user()
        data = self.read_json()
        content = str(data.get("content", "")).strip()
        if not content:
            raise HttpError(400, "内容不能为空")
        title = str(data.get("title", "")).strip() or make_title(content)
        tags = normalize_tags(data.get("tags"))
        try:
            importance = int(data.get("importance", 2))
        except ValueError as exc:
            raise HttpError(400, "重要程度不正确") from exc
        importance = min(3, max(1, importance))
        next_review = parse_client_datetime(data.get("nextReviewAt") or data.get("next_review_at"))
        now = iso_utc()
        with get_db() as conn:
            cursor = conn.execute(
                """
                INSERT INTO cards (
                    user_id, title, content, tags, importance, interval_days,
                    next_review_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (user["id"], title[:80], content, json.dumps(tags, ensure_ascii=False), importance, iso_utc(next_review) if next_review else default_next_review(), now, now),
            )
            row = conn.execute("SELECT * FROM cards WHERE id = ?", (cursor.lastrowid,)).fetchone()
        self.send_json(201, {"card": row_to_card(row)})

    def handle_card_item(self, method: str, path: str) -> None:
        user = self.require_user()
        parts = path.strip("/").split("/")
        if len(parts) < 3:
            raise HttpError(404, "卡片不存在")
        try:
            card_id = int(parts[2])
        except ValueError as exc:
            raise HttpError(404, "卡片不存在") from exc

        if len(parts) == 4 and parts[3] == "review" and method == "POST":
            self.review_card(user, card_id)
            return
        if len(parts) == 4 and parts[3] == "archive" and method == "POST":
            self.set_card_status(user, card_id, "archived")
            return
        if len(parts) == 4 and parts[3] == "restore" and method == "POST":
            self.set_card_status(user, card_id, "active")
            return
        if len(parts) == 3 and method == "PATCH":
            self.update_card(user, card_id)
            return
        if len(parts) == 3 and method == "DELETE":
            self.delete_card(user, card_id)
            return
        raise HttpError(404, "卡片接口不存在")

    def update_card(self, user: dict, card_id: int) -> None:
        data = self.read_json()
        fields = []
        args: list = []

        if "content" in data:
            content = str(data["content"]).strip()
            if not content:
                raise HttpError(400, "内容不能为空")
            fields.extend(["content = ?", "title = ?"])
            args.extend([content, str(data.get("title", "")).strip() or make_title(content)])
        elif "title" in data:
            title = str(data["title"]).strip()
            if title:
                fields.append("title = ?")
                args.append(title[:80])

        if "tags" in data:
            fields.append("tags = ?")
            args.append(json.dumps(normalize_tags(data["tags"]), ensure_ascii=False))
        if "importance" in data:
            fields.append("importance = ?")
            args.append(min(3, max(1, int(data["importance"]))))
        if "nextReviewAt" in data or "next_review_at" in data:
            parsed = parse_client_datetime(data.get("nextReviewAt") or data.get("next_review_at"))
            if not parsed:
                raise HttpError(400, "提醒时间不能为空")
            fields.append("next_review_at = ?")
            args.append(iso_utc(parsed))

        if not fields:
            raise HttpError(400, "没有可更新的字段")

        fields.append("updated_at = ?")
        args.append(iso_utc())
        args.extend([card_id, user["id"]])

        with get_db() as conn:
            cursor = conn.execute(
                f"UPDATE cards SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
                args,
            )
            if cursor.rowcount == 0:
                raise HttpError(404, "卡片不存在")
            row = conn.execute("SELECT * FROM cards WHERE id = ? AND user_id = ?", (card_id, user["id"])).fetchone()
        self.send_json(200, {"card": row_to_card(row)})

    def review_card(self, user: dict, card_id: int) -> None:
        data = self.read_json()
        result = str(data.get("result", "")).strip()
        now = iso_utc()
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM cards WHERE id = ? AND user_id = ? AND status = 'active'",
                (card_id, user["id"]),
            ).fetchone()
            if not row:
                raise HttpError(404, "卡片不存在")
            interval_days, next_review_at = compute_next_interval(row, result)
            conn.execute(
                """
                UPDATE cards
                SET interval_days = ?,
                    review_count = review_count + 1,
                    next_review_at = ?,
                    last_reviewed_at = ?,
                    last_notified_at = NULL,
                    updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (interval_days, next_review_at, now, now, card_id, user["id"]),
            )
            updated = conn.execute("SELECT * FROM cards WHERE id = ? AND user_id = ?", (card_id, user["id"])).fetchone()
        self.send_json(200, {"card": row_to_card(updated)})

    def set_card_status(self, user: dict, card_id: int, status: str) -> None:
        now = iso_utc()
        with get_db() as conn:
            cursor = conn.execute(
                "UPDATE cards SET status = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                (status, now, card_id, user["id"]),
            )
            if cursor.rowcount == 0:
                raise HttpError(404, "卡片不存在")
            row = conn.execute("SELECT * FROM cards WHERE id = ? AND user_id = ?", (card_id, user["id"])).fetchone()
        self.send_json(200, {"card": row_to_card(row)})

    def delete_card(self, user: dict, card_id: int) -> None:
        with get_db() as conn:
            cursor = conn.execute("DELETE FROM cards WHERE id = ? AND user_id = ?", (card_id, user["id"]))
            if cursor.rowcount == 0:
                raise HttpError(404, "卡片不存在")
        self.send_json(200, {"ok": True})

    def list_tags(self) -> None:
        user = self.require_user()
        counts: dict[str, int] = {}
        with get_db() as conn:
            rows = conn.execute(
                "SELECT tags FROM cards WHERE user_id = ? AND status = 'active'",
                (user["id"],),
            ).fetchall()
        for row in rows:
            for tag in json.loads(row["tags"] or "[]"):
                counts[tag] = counts.get(tag, 0) + 1
        tags = [{"name": name, "count": count} for name, count in sorted(counts.items())]
        self.send_json(200, {"tags": tags})

    def due_reminders(self) -> None:
        user = self.require_user()
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cards
                WHERE user_id = ? AND status = 'active' AND next_review_at <= ?
                ORDER BY next_review_at ASC
                LIMIT 20
                """,
                (user["id"], iso_utc()),
            ).fetchall()
        self.send_json(200, {"cards": [row_to_card(row) for row in rows]})


def main() -> None:
    init_db()
    worker = threading.Thread(target=reminder_worker, daemon=True)
    worker.start()
    server = ThreadingHTTPServer((APP_HOST, APP_PORT), MemoryHandler)
    print(f"Memory Reminder is running at http://{APP_HOST}:{APP_PORT}")
    print(f"Database: {DB_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
