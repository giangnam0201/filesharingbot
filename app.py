import os
import json
import uuid
import time
import zipfile
import asyncio
from aiohttp import web

# =====================
# CONFIG
# =====================
PORT = int(os.environ.get("PORT", 10000))
UPLOAD_DIR = "uploads"
DB_FILE = "files.json"

MAX_FILE_SIZE = 50 * 1024 * 1024      # 50 MB per file
MAX_TOTAL_SIZE = 200 * 1024 * 1024    # 200 MB per upload
MAX_FILE_AGE = 60 * 60                # 1 hour

ADMIN_KEY = os.environ.get("ADMIN_KEY", "admin123")

os.makedirs(UPLOAD_DIR, exist_ok=True)

# =====================
# DATABASE
# =====================
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

# =====================
# UTIL
# =====================
def html(title, body):
    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
* {{ box-sizing:border-box }}
body {{
    margin:0;
    font-family:Inter,Arial;
    background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);
    color:#fff;
}}
.container {{
    max-width:720px;
    margin:50px auto;
    padding:30px;
    background:rgba(0,0,0,.45);
    backdrop-filter:blur(20px);
    border-radius:18px;
    box-shadow:0 0 60px rgba(0,0,0,.5);
}}
h1 {{ margin-top:0 }}
.drop {{
    border:2px dashed #888;
    padding:40px;
    text-align:center;
    border-radius:12px;
    transition:.2s;
}}
.drop.drag {{ border-color:#6cf; background:rgba(255,255,255,.05) }}
input,button {{
    width:100%;
    margin-top:15px;
    padding:12px;
    border-radius:10px;
    border:none;
    font-size:16px;
}}
button {{
    background:linear-gradient(135deg,#667eea,#764ba2);
    color:white;
    cursor:pointer;
}}
.progress {{
    width:100%;
    height:14px;
    background:#333;
    border-radius:10px;
    overflow:hidden;
    margin-top:15px;
}}
.bar {{
    height:100%;
    width:0%;
    background:linear-gradient(90deg,#00c6ff,#0072ff);
    transition:.2s;
}}
small {{ opacity:.7 }}
a {{ color:#7dd3fc }}
table {{
    width:100%;
    border-collapse:collapse;
}}
td,th {{
    padding:8px;
    border-bottom:1px solid #333;
}}
</style>
</head>
<body>
<div class="container">
{body}
</div>
</body>
</html>
"""

# =====================
# ROUTES
# =====================
async def home(request):
    return web.Response(
        text=html("Upload", """
<h1>📦 Secure Upload</h1>

<div class="drop" id="drop">
Drag & drop files here<br><small>or click</small>
<input type="file" id="files" multiple hidden>
</div>

<input type="password" id="password" placeholder="Download password (optional)">
<button onclick="upload()">Upload</button>

<div class="progress"><div class="bar" id="bar"></div></div>
<div id="status"></div>

<script>
const drop=document.getElementById("drop");
const filesInput=document.getElementById("files");
drop.onclick=()=>filesInput.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("drag")};
drop.ondragleave=()=>drop.classList.remove("drag");
drop.ondrop=e=>{
 e.preventDefault();
 drop.classList.remove("drag");
 filesInput.files=e.dataTransfer.files;
}

function upload(){
 const files=filesInput.files;
 if(!files.length){ alert("No files"); return; }

 let data=new FormData();
 for(let f of files) data.append("files",f);
 data.append("password",document.getElementById("password").value);

 let xhr=new XMLHttpRequest();
 xhr.open("POST","/upload");

 xhr.upload.onprogress=e=>{
  if(e.lengthComputable){
   document.getElementById("bar").style.width=(e.loaded/e.total*100)+"%";
  }
 };

 xhr.onload=()=>document.getElementById("status").innerHTML=xhr.responseText;
 xhr.send(data);
}
</script>
"""),
        content_type="text/html"
    )

async def upload(request):
    reader = await request.multipart()
    files=[]
    total_size=0
    password=None

    while True:
        part=await reader.next()
        if not part: break

        if part.name=="password":
            password=await part.text()

        elif part.name=="files":
            size=0
            fid=str(uuid.uuid4())
            path=os.path.join(UPLOAD_DIR,fid+"_"+part.filename)

            with open(path,"wb") as f:
                while chunk:=await part.read_chunk():
                    size+=len(chunk)
                    total_size+=len(chunk)
                    if size>MAX_FILE_SIZE or total_size>MAX_TOTAL_SIZE:
                        return web.Response(text="❌ File too large")
                    f.write(chunk)

            files.append(path)

    bundle=str(uuid.uuid4())
    db[bundle]={
        "files":files,
        "password":password,
        "created":time.time(),
        "used":False
    }
    save_db(db)

    return web.Response(text=f"✅ Uploaded<br><a href='/download/{bundle}'>Download link</a>")

async def download(request):
    bid=request.match_info["id"]
    e=db.get(bid)
    if not e or e["used"]:
        return web.Response(text=html("Expired","<h2>❌ Link expired</h2>"),content_type="text/html")

    if e["password"]:
        return web.Response(
            text=html("Password","""
<h2>🔐 Enter password</h2>
<form method="post">
<input name="password" type="password">
<button>Unlock</button>
</form>
"""),
            content_type="text/html"
        )

    return await serve_zip(bid)

async def download_post(request):
    bid=request.match_info["id"]
    e=db.get(bid)
    data=await request.post()

    if not e or e["used"]:
        return web.Response(text="Expired")

    if data.get("password")!=e["password"]:
        return web.Response(text="Wrong password")

    return await serve_zip(bid)

async def serve_zip(bid):
    e=db[bid]
    z=os.path.join(UPLOAD_DIR,bid+".zip")
    with zipfile.ZipFile(z,"w") as zipf:
        for f in e["files"]:
            zipf.write(f,arcname=os.path.basename(f))

    e["used"]=True
    save_db(db)
    return web.FileResponse(z)

# =====================
# ADMIN PANEL
# =====================
async def admin(request):
    if request.query.get("key")!=ADMIN_KEY:
        return web.Response(text="Forbidden",status=403)

    rows=""
    total=0
    for k,v in db.items():
        size=sum(os.path.getsize(f) for f in v["files"] if os.path.exists(f))
        total+=size
        rows+=f"<tr><td>{k}</td><td>{len(v['files'])}</td><td>{size//1024} KB</td><td>{'yes' if v['used'] else 'no'}</td></tr>"

    return web.Response(
        text=html("Admin",f"""
<h1>📊 Admin Dashboard</h1>
<p>Total bundles: {len(db)}</p>
<p>Total storage: {total//1024} KB</p>
<table>
<tr><th>ID</th><th>Files</th><th>Size</th><th>Used</th></tr>
{rows}
</table>
"""),
        content_type="text/html"
    )

# =====================
# CLEANUP
# =====================
async def cleanup_loop():
    while True:
        now=time.time()
        for k in list(db.keys()):
            if now-db[k]["created"]>MAX_FILE_AGE:
                for f in db[k]["files"]:
                    if os.path.exists(f): os.remove(f)
                db.pop(k)
        save_db(db)
        await asyncio.sleep(300)

# =====================
# MAIN
# =====================
async def main():
    app=web.Application()
    app.router.add_get("/",home)
    app.router.add_post("/upload",upload)
    app.router.add_get("/download/{id}",download)
    app.router.add_post("/download/{id}",download_post)
    app.router.add_get("/admin",admin)

    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner,"0.0.0.0",PORT)
    await site.start()

    print(f"🌐 Running on {PORT}")
    asyncio.create_task(cleanup_loop())

    while True:
        await asyncio.sleep(3600)

asyncio.run(main())
