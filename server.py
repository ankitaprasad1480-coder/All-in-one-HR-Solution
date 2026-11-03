#!/usr/bin/env python3
"""DevDuo HR dashboard server: company accounts, password sign-in, per-company data.

    pip install -r requirements.txt
    python server.py                      # http://127.0.0.1:8000

Settings (environment variables, all optional):
    HR_HOST            bind address (default 127.0.0.1; use 0.0.0.0 inside Docker)
    HR_PORT            port (default 8000)
    HR_DATA_DIR        where the database and secret key live (default ./data)
    HR_SECRET_KEY      Fernet key for encrypting employee data at rest (default: generated in HR_DATA_DIR)
    HR_SIGNUP_CODE     if set, new companies must enter this invite code to create an account
    HR_COOKIE_SECURE   set to 1 when served over HTTPS (also enabled automatically behind a proxy that
                       sends X-Forwarded-Proto: https and HR_TRUST_PROXY=1)
    HR_TRUST_PROXY     set to 1 when running behind nginx/Caddy/a platform proxy
    HR_DEMO            set to 0 to hide the "view demo dashboard" button (default: shown)
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.fernet import Fernet

import hrcore

HERE = Path(__file__).parent
DATA_DIR = Path(os.environ.get("HR_DATA_DIR", HERE / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "hr.db"
HOST = os.environ.get("HR_HOST", "127.0.0.1")
PORT = int(os.environ.get("HR_PORT", "8000"))
SIGNUP_CODE = os.environ.get("HR_SIGNUP_CODE", "")
DEMO_ENABLED = os.environ.get("HR_DEMO", "1") != "0"
FORCE_SECURE = os.environ.get("HR_COOKIE_SECURE", "") == "1"
TRUST_PROXY = os.environ.get("HR_TRUST_PROXY", "") == "1"
SESSION_ABS, SESSION_IDLE = 8 * 3600, 30 * 60
MAX_BODY = 8 * 1024 * 1024
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class Bad(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def load_fernet():
    env = os.environ.get("HR_SECRET_KEY")
    if env:
        return Fernet(env.encode())
    p = DATA_DIR / "secret.key"
    if not p.exists():
        p.write_bytes(Fernet.generate_key())
        os.chmod(p, 0o600)
    return Fernet(p.read_bytes().strip())


FERNET = load_fernet()


def db():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db():
    c = db()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, name TEXT, industry TEXT, size TEXT, country TEXT,
        data_currency TEXT, display_currency TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
        email TEXT UNIQUE COLLATE NOCASE, full_name TEXT, phone TEXT, pw_hash TEXT, recovery_hash TEXT,
        failed INTEGER DEFAULT 0, locked_until REAL DEFAULT 0, created_at REAL);
    CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        created REAL, last_seen REAL);
    CREATE TABLE IF NOT EXISTS datasets(company_id INTEGER PRIMARY KEY REFERENCES companies(id) ON DELETE CASCADE,
        payload BLOB, report TEXT, filename TEXT, updated_at REAL);
    """)
    c.commit()
    c.close()


def hash_secret(s):
    salt = os.urandom(16)
    h = hashlib.scrypt(s.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_secret(s, stored):
    try:
        _, salt, h = stored.split("$")
        salt, h = base64.b64decode(salt), base64.b64decode(h)
    except Exception:
        return False
    return hmac.compare_digest(hashlib.scrypt(s.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32), h)


DUMMY_HASH = hash_secret("not-a-real-password")
sha = lambda s: hashlib.sha256(s.encode()).hexdigest()


def new_recovery_code():
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(20))
    return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


norm_code = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
RATE = defaultdict(deque)


def rate_limited(key, limit=20, window=60):
    now, q = time.time(), RATE[key]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def need(b, key, lo=1, hi=200):
    v = hrcore.clean_text(b.get(key, ""), hi)
    if len(v) < lo:
        raise Bad("Please fill in: " + key.replace("_", " "))
    return v


def check_password(pw):
    if not isinstance(pw, str) or not (10 <= len(pw) <= 128):
        raise Bad("Password must be 10 to 128 characters.")
    return pw


def company_fields(b):
    f = {"name": need(b, "company", 2, 100), "industry": need(b, "industry", 1, 40),
         "size": need(b, "size", 1, 20), "country": need(b, "country", 2, 60),
         "data_currency": need(b, "data_currency", 3, 3).upper(),
         "display_currency": need(b, "display_currency", 3, 3).upper()}
    if f["industry"] not in hrcore.INDUSTRIES or f["size"] not in hrcore.SIZES:
        raise Bad("Please choose an industry and company size from the lists.")
    if f["data_currency"] not in hrcore.CURRENCIES or f["display_currency"] not in hrcore.CURRENCIES:
        raise Bad("Please choose currencies from the lists.")
    return f


def company_json(row):
    return {k: row[k] for k in ("name", "industry", "size", "country", "data_currency", "display_currency")}


class H(BaseHTTPRequestHandler):
    server_version, sys_version, protocol_version = "HRDash", "", "HTTP/1.1"
    INDEX = None

    def log_message(self, fmt, *args):
        if os.environ.get("HR_LOG"):
            super().log_message(fmt, *args)

    # ---- helpers
    def ip(self):
        if TRUST_PROXY and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def https(self):
        return FORCE_SECURE or (TRUST_PROXY and self.headers.get("X-Forwarded-Proto", "") == "https")

    def send(self, code, body, ctype="application/json", extra=None, cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        if code >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        for k, v in {"Content-Type": ctype, "Content-Length": str(len(body)), "Cache-Control": cache,
                     "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                     "X-Frame-Options": "DENY", "Content-Security-Policy": CSP}.items():
            self.send_header(k, v)
        if self.https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def cookie_hdr(self, token, clear=False):
        parts = [f"sid={'' if clear else token}", "Path=/", "HttpOnly", "SameSite=Strict",
                 "Max-Age=0" if clear else f"Max-Age={SESSION_ABS}"]
        if self.https():
            parts.append("Secure")
        return [("Set-Cookie", "; ".join(parts))]

    def token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sid"].value if "sid" in c else None

    def body(self):
        if not (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"):
            raise Bad("Expected JSON.", 415)
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise Bad("That request is too large.", 413)
        try:
            b = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise Bad("Invalid request.")
        if not isinstance(b, dict):
            raise Bad("Invalid request.")
        return b

    def user(self, c):
        tok = self.token()
        if not tok:
            raise Bad("Please sign in.", 401)
        now = time.time()
        r = c.execute("SELECT s.token_hash, s.created, s.last_seen, u.id uid, u.company_id, u.email, u.full_name, u.phone "
                      "FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (sha(tok),)).fetchone()
        if not r or now - r["created"] > SESSION_ABS or now - r["last_seen"] > SESSION_IDLE:
            if r:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (r["token_hash"],))
                c.commit()
            raise Bad("Your session has ended. Please sign in again.", 401)
        if now - r["last_seen"] > 30:
            c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, r["token_hash"]))
            c.commit()
        return r

    def start_session(self, c, uid):
        tok, now = secrets.token_urlsafe(32), time.time()
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (sha(tok), uid, now, now))
        c.execute("DELETE FROM sessions WHERE created<?", (now - SESSION_ABS,))
        return self.cookie_hdr(tok)

    def me(self, c, u):
        co = c.execute("SELECT * FROM companies WHERE id=?", (u["company_id"],)).fetchone()
        d = c.execute("SELECT updated_at, filename FROM datasets WHERE company_id=?", (u["company_id"],)).fetchone()
        return {"account": {"email": u["email"], "name": u["full_name"], "phone": u["phone"]},
                "company": company_json(co), "has_data": bool(d),
                "updated_at": d["updated_at"] if d else None, "filename": d["filename"] if d else None}

    # ---- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                return self.send(200, H.INDEX, "text/html; charset=utf-8", cache="no-cache")
            if path == "/healthz":
                return self.send(200, {"ok": True})
            if path == "/favicon.ico":
                return self.send(204, b"")
            if path == "/api/config":
                return self.send(200, {"industries": hrcore.INDUSTRIES, "sizes": hrcore.SIZES,
                                       "currencies": hrcore.CURRENCIES, "signup_code_required": bool(SIGNUP_CODE),
                                       "demo": DEMO_ENABLED})
            if path == "/api/template.csv":
                return self.send(200, hrcore.TEMPLATE_CSV, "text/csv; charset=utf-8",
                                 [("Content-Disposition", 'attachment; filename="employees_template.csv"')])
            if path in ("/api/me", "/api/data"):
                c = db()
                try:
                    u = self.user(c)
                    if path == "/api/me":
                        return self.send(200, self.me(c, u))
                    d = c.execute("SELECT * FROM datasets WHERE company_id=?", (u["company_id"],)).fetchone()
                    if not d:
                        return self.send(200, {"records": [], "report": None})
                    return self.send(200, {"records": json.loads(FERNET.decrypt(d["payload"])),
                                           "report": json.loads(d["report"]), "updated_at": d["updated_at"]})
                finally:
                    c.close()
            return self.send(404, {"error": "Not found."})
        except Bad as e:
            self.send(e.code, {"error": str(e)})
        except Exception:
            self.send(500, {"error": "Something went wrong on the server."})

    def do_POST(self):
        path = self.path.split("?")[0]
        c = None
        try:
            if not path.startswith("/api/"):
                raise Bad("Not found.", 404)
            if path in ("/api/login", "/api/signup", "/api/reset") and rate_limited("auth:" + self.ip()):
                raise Bad("Too many attempts. Please wait a minute and try again.", 429)
            b = self.body()
            c = db()
            fn = getattr(self, "p_" + path[5:], None)
            if not fn:
                raise Bad("Not found.", 404)
            code, out, extra = fn(c, b)
            c.commit()
            self.send(code, out, extra=extra)
        except Bad as e:
            self.send(e.code, {"error": str(e)})
        except Exception:
            self.send(500, {"error": "Something went wrong on the server."})
        finally:
            if c:
                c.close()

    # ---- endpoints: each returns (status, json, extra_headers)
    def p_signup(self, c, b):
        if SIGNUP_CODE and not hmac.compare_digest(str(b.get("invite_code", "")), SIGNUP_CODE):
            raise Bad("That invite code is not valid.", 403)
        f = company_fields(b)
        email = need(b, "email", 5, 254).lower()
        if not EMAIL_RE.match(email):
            raise Bad("Please enter a valid email address.")
        name, pw = need(b, "name", 2, 80), check_password(b.get("password"))
        phone = hrcore.clean_text(b.get("phone", ""), 30)
        if phone and not re.match(r"^[0-9+()\-\s]{5,30}$", phone):
            raise Bad("Phone number can only contain digits, spaces, + ( ) and -.")
        if email == pw.lower():
            raise Bad("Password cannot be your email address.")
        code = new_recovery_code()
        try:
            cur = c.execute("INSERT INTO companies(name,industry,size,country,data_currency,display_currency,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (f["name"], f["industry"], f["size"], f["country"],
                                                      f["data_currency"], f["display_currency"], time.time()))
            uid = c.execute("INSERT INTO users(company_id,email,full_name,phone,pw_hash,recovery_hash,created_at) "
                            "VALUES(?,?,?,?,?,?,?)", (cur.lastrowid, email, name, phone, hash_secret(pw),
                                                       hash_secret(norm_code(code)), time.time())).lastrowid
        except sqlite3.IntegrityError:
            c.rollback()
            raise Bad("An account with this email already exists. Try signing in.", 409)
        extra = self.start_session(c, uid)
        c.commit()
        return 200, {"recovery_code": code, **self.me(c, self.user_by_id(c, uid))}, extra

    def user_by_id(self, c, uid):
        return c.execute("SELECT id uid, company_id, email, full_name, phone FROM users WHERE id=?", (uid,)).fetchone()

    def p_login(self, c, b):
        email, pw = str(b.get("email", "")).strip().lower(), str(b.get("password", ""))
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        generic = Bad("Incorrect email or password.", 401)
        if not u:
            verify_secret(pw, DUMMY_HASH)
            raise generic
        if u["locked_until"] > time.time():
            raise Bad("Too many failed attempts. Please try again in a few minutes, or reset with your recovery code.", 429)
        if not verify_secret(pw, u["pw_hash"]):
            failed = u["failed"] + 1
            lock = time.time() + LOCK_SECONDS if failed >= LOCK_AFTER else 0
            c.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?", (0 if lock else failed, lock, u["id"]))
            c.commit()
            raise generic
        c.execute("UPDATE users SET failed=0, locked_until=0 WHERE id=?", (u["id"],))
        extra = self.start_session(c, u["id"])
        c.commit()
        return 200, self.me(c, self.user_by_id(c, u["id"])), extra

    def p_logout(self, c, b):
        tok = self.token()
        if tok:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (sha(tok),))
        return 200, {"ok": True}, self.cookie_hdr("", clear=True)

    def p_reset(self, c, b):
        email, code = str(b.get("email", "")).strip().lower(), norm_code(b.get("recovery_code", ""))
        pw = check_password(b.get("new_password"))
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        ok = verify_secret(code, u["recovery_hash"] if u else DUMMY_HASH)
        if not u or not ok:
            raise Bad("The email or recovery code is not correct.", 401)
        new_code = new_recovery_code()
        c.execute("UPDATE users SET pw_hash=?, recovery_hash=?, failed=0, locked_until=0 WHERE id=?",
                  (hash_secret(pw), hash_secret(norm_code(new_code)), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
        extra = self.start_session(c, u["id"])
        c.commit()
        return 200, {"recovery_code": new_code, **self.me(c, self.user_by_id(c, u["id"]))}, extra

    def p_password(self, c, b):
        u = self.user(c)
        row = c.execute("SELECT pw_hash FROM users WHERE id=?", (u["uid"],)).fetchone()
        if not verify_secret(str(b.get("current", "")), row["pw_hash"]):
            raise Bad("Your current password is not correct.", 403)
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_secret(check_password(b.get("new"))), u["uid"]))
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (u["uid"], u["token_hash"]))
        return 200, {"ok": True}, None

    def p_recovery(self, c, b):
        u = self.user(c)
        row = c.execute("SELECT pw_hash FROM users WHERE id=?", (u["uid"],)).fetchone()
        if not verify_secret(str(b.get("password", "")), row["pw_hash"]):
            raise Bad("Your password is not correct.", 403)
        code = new_recovery_code()
        c.execute("UPDATE users SET recovery_hash=? WHERE id=?", (hash_secret(norm_code(code)), u["uid"]))
        return 200, {"recovery_code": code}, None

    def p_company(self, c, b):
        u = self.user(c)
        f = company_fields(b)
        c.execute("UPDATE companies SET name=?,industry=?,size=?,country=?,data_currency=?,display_currency=? WHERE id=?",
                  (f["name"], f["industry"], f["size"], f["country"], f["data_currency"], f["display_currency"],
                   u["company_id"]))
        return 200, self.me(c, u), None

    def p_upload(self, c, b):
        u = self.user(c)
        text = b.get("csv")
        if not isinstance(text, str) or not text.strip():
            raise Bad("The file is empty.")
        co = c.execute("SELECT data_currency FROM companies WHERE id=?", (u["company_id"],)).fetchone()
        try:
            recs, rep = hrcore.clean_records(text, co["data_currency"])
        except ValueError as e:
            raise Bad(str(e))
        c.execute("INSERT OR REPLACE INTO datasets VALUES(?,?,?,?,?)",
                  (u["company_id"], FERNET.encrypt(json.dumps(recs, separators=(",", ":")).encode()),
                   json.dumps(rep), hrcore.clean_text(b.get("filename", "employees.csv"), 120), time.time()))
        return 200, {"records": recs, "report": rep}, None

    def p_delete(self, c, b):
        u = self.user(c)
        row = c.execute("SELECT pw_hash FROM users WHERE id=?", (u["uid"],)).fetchone()
        if not verify_secret(str(b.get("password", "")), row["pw_hash"]):
            raise Bad("Your password is not correct.", 403)
        c.execute("DELETE FROM companies WHERE id=?", (u["company_id"],))
        return 200, {"ok": True}, self.cookie_hdr("", clear=True)


def main():
    init_db()
    H.INDEX = (HERE / "static" / "index.html").read_bytes()
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"HR dashboard running on http://{HOST}:{PORT}")
    if not (FORCE_SECURE or TRUST_PROXY):
        print("Note: serve this over HTTPS in production (see DEPLOY.md) and set HR_COOKIE_SECURE=1.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
