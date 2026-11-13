"""NetLab portal: one small web portal that runs once per role (staff or guest).

The nginx gateway decides WHO MAY REACH a portal (by network zone); this app
decides WHO MAY USE it (login) and what each role can do.
"""
import functools
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template,
                   request, send_from_directory, session, stream_with_context, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from session_store import SessionStore

ROLE = os.environ.get("PORTAL_ROLE", "guest")
if ROLE not in ("staff", "guest"):
    raise RuntimeError("PORTAL_ROLE must be 'staff' or 'guest'")

SECRET = os.environ.get("PORTAL_SECRET_KEY", "")
if len(SECRET) < 16:
    raise RuntimeError("PORTAL_SECRET_KEY is required (at least 16 characters)")


def parse_users(raw):
    """'alice:pw1,bob:pw2' -> {'alice': 'pw1', 'bob': 'pw2'}"""
    users = {}
    for item in raw.split(","):
        name, sep, password = item.strip().partition(":")
        if sep and name and password:
            users[name] = password
    return users


USERS = parse_users(os.environ.get("PORTAL_USERS", ""))
if not USERS:
    raise RuntimeError("PORTAL_USERS needs at least one name:password pair")

CAN_WRITE = ROLE == "staff"  # only staff can upload files and post notes
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
FILES_DIR = DATA_DIR / "files"
NOTES_FILE = DATA_DIR / "notes.json"
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "20"))
SPEEDTEST_MAX_MB = int(os.environ.get("SPEEDTEST_MAX_MB", "50" if CAN_WRITE else "1"))
MAX_NOTE_CHARS = 300

ANNOUNCEMENTS = [
    "Welcome to the visitor network. Guest access is limited and monitored.",
    "Visitors can reach this portal only. Internal systems are not available to guests.",
    "Need help? Ask at the front desk and a staff member will assist you.",
]
VISITOR_GUIDE = (
    "NetLab visitor guide\n"
    "====================\n"
    "1. You are on the guest network (isolated_net).\n"
    "2. Guests can read announcements and download a small connection test file.\n"
    "3. Internal services, file sharing and management tools are staff/admin only.\n"
)


SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "28800"))
if not 1 <= SESSION_TTL_SECONDS <= 86400:
    raise RuntimeError("SESSION_TTL_SECONDS must be between 1 and 86400")

SECURE_COOKIES = os.environ.get("SESSION_COOKIE_SECURE", "0")
if SECURE_COOKIES not in ("0", "1"):
    raise RuntimeError("SESSION_COOKIE_SECURE must be 0 or 1")

app = Flask(__name__)
if SECURE_COOKIES == "1":
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
app.config.update(
    # each role gets its own signing key, so a staff session can never be valid on the guest portal
    SECRET_KEY=hashlib.sha256(f"{SECRET}:{ROLE}".encode()).hexdigest(),
    SESSION_COOKIE_NAME=f"{ROLE}_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=SECURE_COOKIES == "1",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_TTL_SECONDS),
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
)

# Persisted in a dedicated writable volume in Compose.
auth_sessions = SessionStore(
    os.environ.get("SESSION_DB_PATH", os.path.join(
        tempfile.gettempdir(), f"netlab-auth-{os.getuid()}", ROLE,
        "sessions.sqlite3")),
    app.config["SECRET_KEY"], ROLE,
)

if CAN_WRITE:
    FILES_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


# ---------- helpers ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def client_info():
    """Address and zone as seen by the gateway (it overwrites these headers)."""
    return {
        "ip": request.headers.get("X-Real-IP", request.remote_addr),
        "zone": request.headers.get("X-Zone", "unknown"),
    }


def current_user():
    name = session.get("user")
    password = USERS.get(name) if isinstance(name, str) else None
    if (session.get("role") == ROLE
            and auth_sessions.valid(session.get("sid"), name, password)):
        return name
    if "user" in session or "sid" in session:
        session.clear()
    return None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.path.startswith(("/api/", "/speedtest", "/files", "/guide")):
                return jsonify({"error": "login required"}), 401
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "role": ROLE, "client": client_info(),
            "secure_transport": request.is_secure}


def load_notes():
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_notes(notes):
    tmp = NOTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes[-50:]), encoding="utf-8")
    tmp.replace(NOTES_FILE)


def list_files():
    out = []
    for p in sorted(FILES_DIR.iterdir()) if FILES_DIR.exists() else []:
        if p.is_file():
            stat = p.stat()
            out.append({"name": p.name, "kb": round(stat.st_size / 1024, 1),
                        "bytes": stat.st_size,
                        "modified": datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


# ---------- security ----------
@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not hmac.compare_digest(sent.encode(), session.get("csrf", "").encode()):
            abort(400, "Invalid or missing CSRF token. Reload the page and try again.")


@app.errorhandler(sqlite3.Error)
def session_store_unavailable(error):
    app.logger.error("Session database unavailable: %s", error)
    return jsonify({"error": "sign-in service temporarily unavailable"}), 503


@app.after_request
def secure_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; img-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


# Preserve HTTP status codes; enhance browser errors without changing API contracts.
@app.errorhandler(500)
@app.errorhandler(HTTPException)
def request_error(error):
    code = error.code or 500
    description = error.description
    if (request.path.startswith("/api/")
            or request.accept_mimetypes.best_match(
                ["text/html", "application/json"]) == "application/json"):
        return jsonify({"error": description}), code
    titles = {400: "Please check your request", 401: "Please sign in again",
              403: "This action is not available", 404: "Page not found",
              409: "A file with this name already exists", 413: "The upload is too large",
              429: "Please wait before trying again"}
    hints = {400: "Reload the form and try again.",
             401: "Your session may have expired. Sign in before continuing.",
             403: "Check your account and network access.",
             404: "The page or file may not be available in this portal.",
             409: "Your existing file is safe. Rename the new file before uploading.",
             413: f"The request limit is {MAX_UPLOAD_MB} MiB, including form data.",
             429: "Wait a moment, then retry. No automatic retry has been made."}
    return render_template(
        "error.html", error_code=code, error_title=titles.get(code, error.name),
        error_message=description, error_hint=hints.get(code, "Please try again later."),
        user=None), code


# ---------- pages ----------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "role": ROLE})


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home"))
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        expected = USERS.get(name)
        # always compare, even for unknown names, so timing does not reveal valid users
        same = hmac.compare_digest(password.encode(), (expected or "-").encode())
        if expected is not None and same:
            auth_sessions.revoke(session.get("sid"))
            session.clear()
            session.permanent = True
            session["user"] = name
            session["role"] = ROLE
            session["sid"] = auth_sessions.create(
                name, expected, SESSION_TTL_SECONDS)
            csrf_token()
            return redirect(url_for("home"))
        return render_template("login.html", error="Invalid username or password."), 401
    return render_template("login.html", error=None)


@app.post("/logout")
@login_required
def logout():
    auth_sessions.revoke(session.get("sid"))
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    ctx = {"user": current_user(), "speedtest_max": SPEEDTEST_MAX_MB, "can_write": CAN_WRITE}
    if CAN_WRITE:
        ctx.update(notes=list(reversed(load_notes())), files=list_files(),
                   max_upload=MAX_UPLOAD_MB)
    else:
        ctx["announcements"] = ANNOUNCEMENTS
    return render_template("home.html", **ctx)


@app.get("/help")
def help_page():
    return render_template("help.html", user=None)


@app.get("/api/whoami")
@login_required
def whoami():
    info = client_info()
    return jsonify({"user": current_user(), "role": ROLE,
                    "client_ip": info["ip"], "zone": info["zone"]})


@app.get("/speedtest")
@login_required
def speedtest():
    """Stream N mebibytes of test traffic. This does not calculate internet speed."""
    try:
        mb = int(request.args.get("mb", "1"))
    except ValueError:
        return jsonify({"error": "mb must be a whole number"}), 400
    if not 1 <= mb <= SPEEDTEST_MAX_MB:
        return jsonify({"error": f"the {ROLE} portal allows 1 to {SPEEDTEST_MAX_MB} MB"}), 403
    chunk = b"x" * 65536
    total = mb * 1024 * 1024

    def generate():
        for _ in range(total // len(chunk)):
            yield chunk

    return Response(stream_with_context(generate()), mimetype="application/octet-stream",
                    headers={"Content-Length": str(total),
                             "Content-Disposition": f'attachment; filename="test-{mb}MB.bin"'})


if CAN_WRITE:
    @app.post("/notes")
    @login_required
    def add_note():
        text = request.form.get("text", "").strip()[:MAX_NOTE_CHARS]
        if text:
            with _lock:
                notes = load_notes()
                notes.append({"user": current_user(), "text": text,
                              "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")})
                save_notes(notes)
            flash("Your note was posted to the staff notice board.", "note-success")
        else:
            flash("Write a note before posting.", "note-error")
        return redirect(url_for("home", _anchor="notices"))

    @app.post("/files")
    @login_required
    def upload():
        uploaded = request.files.get("file")
        name = secure_filename(uploaded.filename) if uploaded and uploaded.filename else ""
        if not name:
            abort(400, "Choose a file with a normal name.")
        # Stage outside the shared directory so incomplete uploads are not visible.
        # Hard-link publication is atomic and never replaces an existing name,
        # including when another worker uploads the same filename concurrently.
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=DATA_DIR) as directory:
            staged = Path(directory) / "content"
            uploaded.save(staged)
            try:
                os.link(staged, FILES_DIR / name)
            except FileExistsError:
                abort(409, "A file with this name already exists. Existing file was kept. "
                           "Rename your file and try again.")
        flash("Your file was uploaded. The shared file list has been updated.", "upload-success")
        if request.accept_mimetypes.best_match(
                ["text/html", "application/json"]) == "application/json":
            return jsonify({"message": "File uploaded.", "redirect": "/#files"}), 201
        return redirect(url_for("home", _anchor="files"))

    @app.get("/files/<name>")
    @login_required
    def download(name):
        return send_from_directory(FILES_DIR, name, as_attachment=True)
else:
    @app.get("/guide.txt")
    @login_required
    def guide():
        return Response(VISITOR_GUIDE, mimetype="text/plain",
                        headers={"Content-Disposition": 'attachment; filename="visitor-guide.txt"'})
