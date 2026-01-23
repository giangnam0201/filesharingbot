import os
import json
import time
import uuid
import threading
import requests
from flask import Flask, request, redirect, session, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import humanize

# ================= CONFIG =================
APP_SECRET = os.getenv("APP_SECRET", "super-secret-key")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
DB_FILE = "database.json"
PORT = int(os.getenv("PORT", 10000))

# =========================================

app = Flask(__name__)
app.secret_key = APP_SECRET
START_TIME = time.time()

# =============== DATABASE =================
def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({"files": {}}, f, indent=2)
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

# ================ UTILS ===================
def uptime():
    return humanize.naturaldelta(int(time.time() - START_TIME))

def get_gofile_server():
    r = requests.get("https://api.gofile.io/servers").json()
    return r["data"]["servers"][0]["name"]

def cleanup_task():
    while True:
        time.sleep(3600)
        db = load_db()
        changed = False
        for code in list(db["files"].keys()):
            if time.time() - db["files"][code]["created"] > 60 * 60 * 24 * 30:
                del db["files"][code]
                changed = True
        if changed:
            save_db(db)

threading.Thread(target=cleanup_task, daemon=True).start()

# ================ STYLES ==================
BASE_CSS = """
body{background:#0f1220;color:#fff;font-family:Arial}
.card{max-width:900px;margin:40px auto;background:#161a2e;padding:25px;border-radius:14px}
h1{color:#7aa2ff}
input,button{padding:12px;border-radius:10px;border:none;width:100%;margin-top:10px}
button{background:#7aa2ff;color:#000;font-weight:bold;cursor:pointer}
a{color:#7aa2ff;text-decoration:none}
.progress{height:20px;background:#222;border-radius:10px;overflow:hidden;margin-top:10px}
.bar{height:100%;width:0%;background:#7aa2ff}
"""

# ================ ROUTES ==================

@app.route("/")
def home():
    return """
<!DOCTYPE html>
<html>
<head>
<style>{css}</style>
</head>
<body>
<div class="card">
<h1>🚀 Web File Share</h1>
<p>Unlimited uploads · Gofile powered · Password protected</p>

<input type="file" id="file">
<input type="password" id="password" placeholder="Password (optional)">
<button onclick="upload()">Upload</button>

<div class="progress"><div class="bar" id="bar"></div></div>
<pre id="out"></pre>

<script>
async function upload() {{
  let file = document.getElementById('file').files[0];
  if(!file) return alert("No file selected");

  let pw = document.getElementById('password').value;

  let s = await fetch('/api/server').then(r=>r.json());

  let fd = new FormData();
  fd.append('file', file);

  let xhr = new XMLHttpRequest();
  xhr.upload.onprogress = function(e) {{
    document.getElementById('bar').style.width =
      Math.round(e.loaded / e.total * 100) + '%';
  }};

  xhr.onload = function() {{
    let r = JSON.parse(xhr.responseText);
    fetch('/api/register', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{
        id:r.data.fileId,
        link:r.data.downloadPage,
        password:pw
      }})
    }).then(r=>r.json()).then(j=>{
      document.getElementById('out').innerText =
        location.origin + '/d/' + j.code;
    });
  }};

  xhr.open('POST','https://' + s.server + '.gofile.io/uploadFile');
  xhr.send(fd);
}}
</script>
</div>
</body>
</html>
""".format(css=BASE_CSS)

@app.route("/api/server")
def api_server():
    return jsonify({"server": get_gofile_server()})

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.json
    code = uuid.uuid4().hex[:6]
    db = load_db()
    db["files"][code] = {
        "gofile": data["link"],
        "password": generate_password_hash(data["password"]) if data["password"] else None,
        "created": time.time()
    }
    save_db(db)
    return jsonify({"code": code})

@app.route("/d/<code>", methods=["GET","POST"])
def download(code):
    db = load_db()
    f = db["files"].get(code)
    if not f:
        abort(404)

    if f["password"]:
        if request.method == "POST":
            if check_password_hash(f["password"], request.form["password"]):
                session["ok_"+code] = True
            else:
                return "Wrong password"

        if not session.get("ok_"+code):
            return """
            <html><style>{css}</style>
            <div class="card">
            <h1>🔐 Password Required</h1>
            <form method="post">
              <input type="password" name="password">
              <button>Unlock</button>
            </form>
            </div></html>
            """.format(css=BASE_CSS)

    return redirect(f["gofile"])

@app.route("/admin", methods=["GET","POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True

    if not session.get("admin"):
        return """
        <html><style>{css}</style>
        <div class="card">
        <h1>Admin Login</h1>
        <form method="post">
          <input type="password" name="password">
          <button>Login</button>
        </form>
        </div></html>
        """.format(css=BASE_CSS)

    db = load_db()
    rows = ""
    for k,v in db["files"].items():
        rows += f"<tr><td>{k}</td><td><a href='{v['gofile']}'>Gofile</a></td></tr>"

    return f"""
    <html><style>{BASE_CSS}</style>
    <div class="card">
    <h1>📊 Admin Panel</h1>
    <p>Uptime: {uptime()}</p>
    <table border="1" cellpadding="10">
    <tr><th>Code</th><th>Link</th></tr>
    {rows}
    </table>
    </div></html>
    """

@app.route("/keep-alive")
def keep_alive():
    return "OK"

# ================= START ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
