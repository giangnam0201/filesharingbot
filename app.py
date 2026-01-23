import os, json, time, uuid, threading, requests
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
    return requests.get("https://api.gofile.io/servers").json()["data"]["servers"][0]["name"]

def cleanup():
    while True:
        time.sleep(3600)
        db = load_db()
        for k in list(db["files"].keys()):
            if time.time() - db["files"][k]["created"] > 60*60*24*30:
                del db["files"][k]
        save_db(db)

threading.Thread(target=cleanup, daemon=True).start()

# ================ CSS =====================
CSS = """
body{background:#0f1220;color:#fff;font-family:Arial}
.card{max-width:900px;margin:40px auto;background:#161a2e;padding:25px;border-radius:14px}
h1{color:#7aa2ff}
input,button{padding:12px;border-radius:10px;border:none;width:100%;margin-top:10px}
button{background:#7aa2ff;color:#000;font-weight:bold;cursor:pointer}
.progress{height:20px;background:#222;border-radius:10px;overflow:hidden;margin-top:10px}
.bar{height:100%;width:0%;background:#7aa2ff}
table{width:100%;border-collapse:collapse}
td,th{padding:10px;border-bottom:1px solid #333}
a{color:#7aa2ff}
"""

# ================ HOME ====================
@app.route("/")
def home():
    html = """
<!DOCTYPE html>
<html>
<head>
<style>%%CSS%%</style>
</head>
<body>
<div class="card">
<h1>🚀 Web File Share</h1>

<input type="file" id="file">
<input type="password" id="pw" placeholder="Password (optional)">
<button onclick="upload()">Upload</button>

<div class="progress"><div class="bar" id="bar"></div></div>
<pre id="out"></pre>

<script>
async function upload() {
  let f = document.getElementById('file').files[0];
  if(!f) return alert("Select file");

  let pw = document.getElementById('pw').value;
  let srv = await fetch('/api/server').then(r=>r.json());

  let fd = new FormData();
  fd.append('file', f);

  let x = new XMLHttpRequest();
  x.upload.onprogress = e => {
    document.getElementById('bar').style.width =
      Math.round(e.loaded/e.total*100) + '%';
  };

  x.onload = () => {
    let r = JSON.parse(x.responseText);
    fetch('/api/register', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        link:r.data.downloadPage,
        password:pw
      })
    }).then(r=>r.json()).then(j=>{
      document.getElementById('out').innerText =
        location.origin + '/d/' + j.code;
    });
  };

  x.open('POST','https://' + srv.server + '.gofile.io/uploadFile');
  x.send(fd);
}
</script>
</div>
</body>
</html>
"""
    return html.replace("%%CSS%%", CSS)

# ================ API =====================
@app.route("/api/server")
def api_server():
    return jsonify({"server": get_gofile_server()})

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    code = uuid.uuid4().hex[:6]
    db = load_db()
    db["files"][code] = {
        "link": data["link"],
        "password": generate_password_hash(data["password"]) if data["password"] else None,
        "created": time.time()
    }
    save_db(db)
    return jsonify({"code": code})

# ============== DOWNLOAD ==================
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
            page = """
            <html><style>%%CSS%%</style>
            <div class="card">
            <h1>🔐 Password</h1>
            <form method="post">
              <input type="password" name="password">
              <button>Unlock</button>
            </form>
            </div></html>
            """
            return page.replace("%%CSS%%", CSS)

    return redirect(f["link"])

# ================ ADMIN ===================
@app.route("/admin", methods=["GET","POST"])
def admin():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True

    if not session.get("admin"):
        page = """
        <html><style>%%CSS%%</style>
        <div class="card">
        <h1>Admin Login</h1>
        <form method="post">
          <input type="password" name="password">
          <button>Login</button>
        </form>
        </div></html>
        """
        return page.replace("%%CSS%%", CSS)

    db = load_db()
    rows = "".join(
        f"<tr><td>{k}</td><td><a href='{v['link']}'>Link</a></td></tr>"
        for k,v in db["files"].items()
    )

    page = f"""
    <html><style>{CSS}</style>
    <div class="card">
    <h1>📊 Admin Panel</h1>
    <p>Uptime: {uptime()}</p>
    <table>
    <tr><th>Code</th><th>Link</th></tr>
    {rows}
    </table>
    </div></html>
    """
    return page

# ============= KEEP ALIVE ================
@app.route("/keep-alive")
def keep_alive():
    return "OK"

# ================ START ===================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
