import os
import json
import uuid
import time
import threading
import requests
from flask import Flask, request, redirect, abort, jsonify, render_template_string

APP_NAME = "Beautiful File Share"
ADMIN_PASSWORD = "changeme"
DATA_FILE = "files.json"

GOFILE_UPLOAD_API = "https://store1.gofile.io/uploadFile"

app = Flask(__name__)

# ------------------ Storage ------------------

def load_db():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({}, f)
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DATA_FILE, "w") as f:
        json.dump(db, f, indent=2)

# ------------------ Cleanup ------------------

def cleanup_loop():
    while True:
        db = load_db()
        now = time.time()
        changed = False
        for k in list(db.keys()):
            f = db[k]
            if f["expires"] and now > f["expires"]:
                del db[k]
                changed = True
            elif f["one_time"] and f["used"]:
                del db[k]
                changed = True
        if changed:
            save_db(db)
        time.sleep(60)

threading.Thread(target=cleanup_loop, daemon=True).start()

# ------------------ UI ------------------

BASE_HTML = """
<!doctype html>
<html>
<head>
<title>{{ title }}</title>
<style>
body {
    background: #0f172a;
    color: #e5e7eb;
    font-family: system-ui;
    padding: 40px;
}
.box {
    max-width: 700px;
    margin: auto;
    background: #020617;
    padding: 30px;
    border-radius: 14px;
    box-shadow: 0 0 40px #000;
}
h1 { text-align: center; }
input, button {
    width: 100%;
    padding: 14px;
    margin-top: 10px;
    border-radius: 8px;
    border: none;
}
button {
    background: #22c55e;
    color: black;
    font-weight: bold;
    cursor: pointer;
}
a { color: #38bdf8; }
.file {
    background: #020617;
    padding: 10px;
    border-radius: 8px;
    margin: 10px 0;
}
</style>
</head>
<body>
<div class="box">
{{ body }}
</div>
</body>
</html>
"""

# ------------------ Routes ------------------

@app.route("/")
def home():
    body = """
<h1>Upload File</h1>
<form method="post" action="/upload" enctype="multipart/form-data">
<input type="file" name="file" required>
<input type="password" name="password" placeholder="Download password (optional)">
<label><input type="checkbox" name="one_time"> One-time download</label>
<input type="number" name="ttl" placeholder="Expire seconds (optional)">
<button>Upload</button>
</form>
"""
    return render_template_string(BASE_HTML, title=APP_NAME, body=body)

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")
    if not file:
        abort(400)

    r = requests.post(
        GOFILE_UPLOAD_API,
        files={"file": (file.filename, file.stream)},
    ).json()

    if r["status"] != "ok":
        abort(500)

    fid = uuid.uuid4().hex[:8]
    db = load_db()

    db[fid] = {
        "name": file.filename,
        "gofile": r["data"]["downloadPage"],
        "password": request.form.get("password") or None,
        "one_time": bool(request.form.get("one_time")),
        "used": False,
        "expires": time.time() + int(request.form["ttl"]) if request.form.get("ttl") else None,
    }

    save_db(db)
    return redirect(f"/d/{fid}")

@app.route("/d/<fid>", methods=["GET", "POST"])
def download(fid):
    db = load_db()
    f = db.get(fid)
    if not f:
        abort(404)

    if f["password"]:
        if request.method == "POST":
            if request.form.get("password") != f["password"]:
                abort(403)
        else:
            return render_template_string(BASE_HTML, title="Password", body="""
<h1>Password Required</h1>
<form method="post">
<input type="password" name="password">
<button>Unlock</button>
</form>
""")

    if f["one_time"]:
        f["used"] = True
        save_db(db)

    body = f"""
<h1>{f['name']}</h1>
<button onclick="download()">Download</button>
<script>
function download() {{
    window.location.href = "{f['gofile']}";
}}
</script>
"""
    return render_template_string(BASE_HTML, title="Download", body=body)

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") != ADMIN_PASSWORD:
            abort(403)
        return redirect("/admin/panel")

    return render_template_string(BASE_HTML, title="Admin", body="""
<h1>Admin Login</h1>
<form method="post">
<input type="password" name="password">
<button>Login</button>
</form>
""")

@app.route("/admin/panel")
def admin_panel():
    db = load_db()
    body = "<h1>Admin Panel</h1>"
    for k, f in db.items():
        body += f"""
<div class="file">
<b>{f['name']}</b><br>
ID: {k}<br>
<a href="{f['gofile']}">GoFile Link</a>
</div>
"""
    return render_template_string(BASE_HTML, title="Admin", body=body)

@app.route("/health")
def health():
    return "OK"

# ------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
