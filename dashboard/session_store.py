"""Persistent login sessions shared by Gunicorn workers in one service.

The cookie holds an opaque random ID; SQLite stores its hash and absolute expiry.
Credentials are represented only by a keyed fingerprint, never plaintext here.
Keep this module identical in the dashboard and portal build contexts.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class SessionStore:
    def __init__(self, path, key, role, clock=None):
        self.path = Path(path)
        self.key = key.encode()
        self.role = role
        self.clock = clock or time.time
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS login_sessions ("
                "token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, "
                "credential_tag TEXT NOT NULL, expires REAL NOT NULL)"
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def credential_tag(self, username, password):
        value = f"{self.role}\0{username}\0{password}".encode()
        return hmac.new(self.key, value, hashlib.sha256).hexdigest()

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def create(self, username, password, lifetime):
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.connection() as db:
            db.execute("DELETE FROM login_sessions WHERE expires <= ?", (now,))
            db.execute(
                "INSERT INTO login_sessions VALUES (?, ?, ?, ?)",
                (self.token_hash(token), username,
                 self.credential_tag(username, password), now + lifetime),
            )
        return token

    def valid(self, token, username, password):
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return False
        if not isinstance(username, str) or not isinstance(password, str):
            return False
        with self.connection() as db:
            row = db.execute(
                "SELECT username, credential_tag, expires FROM login_sessions "
                "WHERE token_hash = ?", (self.token_hash(token),),
            ).fetchone()
        return bool(row and row[0] == username and self.clock() < row[2]
                    and hmac.compare_digest(row[1], self.credential_tag(username, password)))

    def revoke(self, token):
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return
        with self.connection() as db:
            db.execute("DELETE FROM login_sessions WHERE token_hash = ?",
                       (self.token_hash(token),))
