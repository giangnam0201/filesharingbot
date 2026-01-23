import os
import json
import uuid
import asyncio
import zipfile
import time
from aiohttp import web
import discord
from discord.ext import commands

# =========================
# CONFIG
# =========================
PORT = int(os.environ.get("PORT", 10000))
UPLOAD_DIR = "uploads"
DB_FILE = "files.json"
MAX_FILE_AGE = 60 * 60  # 1 hour
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

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
# WEB ROUTES
# =========================
async def index(request):
    return web.Response(
        text="""
<!DOCTYPE html>
<html>
<head>
<title>File Upload</title>
<style>
body { font-family: Arial; background:#111; color:#eee; text-align:center }
.box { background:#222; padding:20px; border-radius:10px; width:400px; margin:auto }
</style>
</head>
<body>
<h1>📦 Upload Files</h1>
<div class="box">
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="files" multiple><br><br>
<input type="password" name="password" placeholder="Download password (optional)"><br><br>
<button type="submit">Upload</button>
</form>
</div>
</body>
</html>
""",
        content_type="text/html"
    )

async def upload(request):
    reader = await request.multipart()
    password = None
    files = []

    while True:
        part = await reader.next()
        if not part:
            break

        if part.name == "password":
            password = await part.text()
        elif part.name == "files":
            filename = part.filename
            file_id = str(uuid.uuid4())
            filepath = os.path.join(UPLOAD_DIR, file_id + "_" + filename)

            with open(filepath, "wb") as f:
                while chunk := await part.read_chunk():
                    f.write(chunk)

            files.append((file_id, filename, filepath))

    bundle_id = str(uuid.uuid4())
    db[bundle_id] = {
        "files": [f[2] for f in files],
        "password": password,
        "created": time.time(),
        "used": False
    }
    save_db(db)

    link = f"/download/{bundle_id}"

    return web.Response(
        text=f"<h2>✅ Uploaded</h2><p><a href='{link}'>Download Link</a></p>",
        content_type="text/html"
    )

async def download(request):
    bundle_id = request.match_info["id"]
    entry = db.get(bundle_id)

    if not entry or entry["used"]:
        return web.Response(text="❌ Link expired")

    if entry["password"]:
        return web.Response(
            text=f"""
<form method="post">
<input type="password" name="password">
<button>Unlock</button>
</form>
""",
            content_type="text/html"
        )

    return await serve_zip(bundle_id)

async def download_post(request):
    bundle_id = request.match_info["id"]
    entry = db.get(bundle_id)
    data = await request.post()

    if not entry or entry["used"]:
        return web.Response(text="❌ Link expired")

    if data.get("password") != entry["password"]:
        return web.Response(text="❌ Wrong password")

    return await serve_zip(bundle_id)

async def serve_zip(bundle_id):
    entry = db[bundle_id]
    zip_path = os.path.join(UPLOAD_DIR, bundle_id + ".zip")

    with zipfile.ZipFile(zip_path, "w") as zipf:
        for file in entry["files"]:
            zipf.write(file, arcname=os.path.basename(file))

    entry["used"] = True
    save_db(db)

    return web.FileResponse(zip_path)

# =========================
# CLEANUP TASK
# =========================
async def cleanup_loop():
    while True:
        now = time.time()
        for k in list(db.keys()):
            if now - db[k]["created"] > MAX_FILE_AGE:
                for f in db[k]["files"]:
                    if os.path.exists(f):
                        os.remove(f)
                db.pop(k)
        save_db(db)
        await asyncio.sleep(300)

# =========================
# DISCORD BOT
# =========================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🤖 Logged in as {bot.user}")

@bot.command()
async def upload(ctx):
    await ctx.send("📤 Upload files at: https://YOUR_RENDER_URL")

# =========================
# MAIN
# =========================
async def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/upload", upload)
    app.router.add_get("/download/{id}", download)
    app.router.add_post("/download/{id}", download_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    asyncio.create_task(cleanup_loop())
    asyncio.create_task(bot.start(DISCORD_TOKEN))

    while True:
        await asyncio.sleep(3600)

asyncio.run(main())
