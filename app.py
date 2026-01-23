import os
import json
import uuid
import time
import zipfile
import asyncio
from aiohttp import web

# =========================
# CONFIG
# =========================
PORT = int(os.environ.get("PORT", 10000))
UPLOAD_DIR = "uploads"
DB_FILE = "files.json"
MAX_FILE_AGE = 60 * 60  # 1 hour

os.makedirs(UPLOAD_DIR, exist_ok=True)

# =========================
# DATABASE
# =========================
def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, "w") as f:
            json.dump({}, f)
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

db = load_db()

# =========================
# HTML TEMPLATES
# =========================
def page(title, body):
    return f"""
<!DOCTYPE html>
<html>
<head>
<title>{title}</title>
<style>
body {{
    font-family: Arial;
    background:#0f0f0f;
    color:#eee;
}}
.container {{
    max-width:600px;
    margin:40px auto;
    background:#1a1a1a;
    padding:20px;
    border-radius:10px;
}}
input, button {{
    width:100%;
    padding:10px;
    margin-top:10px;
    border-radius:6px;
    border:none;
}}
button {{
    background:#5865F2;
    color:white;
    cursor:pointer;
}}
a {{ color:#58a6ff }}
small {{ opacity:0.6 }}
</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>
"""

# =========================
# ROUTES
# =========================
async def home(request):
    return web.Response(
        text=page("Upload",
        """
<h1>📦 File Upload</h1>
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="files" multiple required>
<input type="password" name="password" placeholder="Download password (optional)">
<button>Upload</button>
</form>
<small>One-time download · Auto-delete · ZIP</small>
"""),
        content_type="text/html"
    )

async def upload(request):
    reader = await request.multipart()
    files = []
    password = None

    while True:
        part = await reader.next()
        if not part:
            break

        if part.name == "password":
            password = await part.text()
        elif part.name == "files":
            file_id = str(uuid.uuid4())
            filepath = os.path.join(UPLOAD_DIR, file_id + "_" + part.filename)

            with open(filepath, "wb") as f:
                while chunk := await part.read_chunk():
                    f.write(chunk)

            files.append(filepath)

    bundle_id = str(uuid.uuid4())
    db[bundle_id] = {
        "files": files,
        "password": password,
        "created": time.time(),
        "used": False
    }
    save_db(db)

    return web.Response(
        text=page("Uploaded",
        f"""
<h2>✅ Upload Complete</h2>
<p><a href="/download/{bundle_id}">Download link</a></p>
<small>This link will self-destruct after one download.</small>
"""),
        content_type="text/html"
    )

async def download(request):
    bundle_id = request.match_info["id"]
    entry = db.get(bundle_id)

    if not entry or entry["used"]:
        return web.Response(text=page("Expired", "<h2>❌ Link expired</h2>"), content_type="text/html")

    if entry["password"]:
        return web.Response(
            text=page("Password",
            """
<h2>🔐 Enter Password</h2>
<form method="post">
<input type="password" name="password" required>
<button>Unlock</button>
</form>
"""),
            content_type="text/html"
        )

    return await serve_zip(bundle_id)

async def download_post(request):
    bundle_id = request.match_info["id"]
    entry = db.get(bundle_id)
    data = await request.post()

    if not entry or entry["used"]:
        return web.Response(text="Expired")

    if data.get("password") != entry["password"]:
        return web.Response(text=page("Wrong", "<h2>❌ Wrong password</h2>"), content_type="text/html")

    return await serve_zip(bundle_id)

async def serve_zip(bundle_id):
    entry = db[bundle_id]
    zip_path = os.path.join(UPLOAD_DIR, bundle_id + ".zip")

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for f in entry["files"]:
            zipf.write(f, arcname=os.path.basename(f))

    entry["used"] = True
    save_db(db)

    return web.FileResponse(zip_path)

# =========================
# CLEANUP LOOP
# =========================
async def cleanup_loop():
    while True:
        now = time.time()
        for key in list(db.keys()):
            if now - db[key]["created"] > MAX_FILE_AGE:
                for f in db[key]["files"]:
                    if os.path.exists(f):
                        os.remove(f)
                db.pop(key)
        save_db(db)
        await asyncio.sleep(300)

# =========================
# MAIN
# =========================
async def main():
    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_post("/upload", upload)
    app.router.add_get("/download/{id}", download)
    app.router.add_post("/download/{id}", download_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"🌐 Web server running on port {PORT}")

    asyncio.create_task(cleanup_loop())

    while True:
        await asyncio.sleep(3600)

asyncio.run(main())
