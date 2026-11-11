import hashlib
import json
import os
import secrets
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote

if not __debug__:
    raise RuntimeError("Run without -O: validation assertions must remain enabled")

password = secrets.token_hex(16)
os.environ.update({
    "PORTAL_ROLE": "staff",
    "PORTAL_SECRET_KEY": secrets.token_hex(32),
    "PORTAL_USERS": "restorecheck:" + password,
    "DATA_DIR": "/data",
    "SESSION_DB_PATH": "/tmp/restore-sessions.sqlite",
    "SESSION_COOKIE_SECURE": "1",
})

from app import app, load_notes  # noqa: E402


class CSRFParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("name") == "csrf_token":
            self.token = attrs.get("value")


app.config["TESTING"] = True
client = app.test_client()
base = "https://localhost"

response = client.get("/api/whoami", base_url=base)
assert response.status_code == 401, (
    f"Anonymous API access: expected 401, got {response.status_code}"
)
print("PASS anonymous API access denied")

response = client.get("/login", base_url=base)
assert response.status_code == 200, "Login form unavailable"

parser = CSRFParser()
parser.feed(response.get_data(as_text=True))
assert parser.token, "CSRF token missing from login form"

response = client.post(
    "/login",
    data={
        "username": "restorecheck",
        "password": password,
        "csrf_token": parser.token,
    },
    base_url=base,
)
assert response.status_code == 302, (
    f"Login failed: status={response.status_code}; "
    f"body={response.get_data(as_text=True)[:500]}"
)
print("PASS login with isolated test credentials")

response = client.get("/api/whoami", base_url=base)
assert response.status_code == 200, "Authenticated API failed"
identity = response.get_json()
assert identity["user"] == "restorecheck", "Unexpected user"
assert identity["role"] == "staff", "Unexpected role"
print("PASS authenticated identity and role")

response = client.get("/", base_url=base)
assert response.status_code == 200, "Restored home page failed"
print("PASS restored home page renders")

expected_notes = json.loads(
    Path("/data/notes.json").read_text(encoding="utf-8")
)
assert isinstance(expected_notes, list), "Notes must be a list"
assert load_notes() == expected_notes, (
    "Restored notes did not load correctly"
)
print(f"PASS restored notes loaded: {len(expected_notes)}")

files = sorted(
    path for path in Path("/data/files").iterdir()
    if path.is_file() and not path.is_symlink()
)
assert files, "No restored files available for download verification"

for path in files:
    response = client.get(
        "/files/" + quote(path.name, safe=""),
        base_url=base,
    )
    assert response.status_code == 200, (
        f"Download failed: status={response.status_code}"
    )
    expected = hashlib.sha256(path.read_bytes()).digest()
    actual = hashlib.sha256(response.data).digest()
    assert actual == expected, "Downloaded content differs"
    assert "attachment" in response.headers.get(
        "Content-Disposition", ""
    ), "Attachment header missing"
    response.close()

print(f"PASS downloaded file hashes match: {len(files)}")
print("All restored application checks passed.")
