from aiohttp import web
import os
import time
import logging
import threading
import platform

try:
    import psutil
except ImportError:
    psutil = None


APP_NAME = "File Sharing Bot"
START_TIME = time.time()
PID = os.getpid()


logging.basicConfig(
    level=logging.INFO,
    format="🌐 [WEB] %(asctime)s | %(levelname)s | %(message)s",
)


# ---------- HELPERS ----------
def uptime():
    seconds = int(time.time() - START_TIME)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m {seconds}s"


def memory_mb():
    if not psutil:
        return None
    return round(psutil.Process(PID).memory_info().rss / 1024 / 1024, 2)


# ---------- ROUTES ----------
async def home(request):
    return web.Response(
        text=f"✅ {APP_NAME} running\nUptime: {uptime()}"
    )


async def health(request):
    return web.json_response({
        "status": "ok",
        "uptime": uptime()
    })


async def stats(request):
    data = {
        "app": APP_NAME,
        "uptime": uptime(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "pid": PID,
    }

    mem = memory_mb()
    if mem is not None:
        data["memory_mb"] = mem

    return web.json_response(data)


# ---------- SERVER ----------
def run_server():
    app = web.Application()
    app.add_routes([
        web.get("/", home),
        web.get("/health", health),
        web.get("/stats", stats),
    ])

    port = int(os.environ.get("PORT", 10000))
    logging.info(f"Web server starting on 0.0.0.0:{port}")

    web.run_app(
        app,
        host="0.0.0.0",
        port=port,
        access_log=None,
        print=None,
        handle_signals=False
    )


def keep_alive():
    thread = threading.Thread(
        target=run_server,
        daemon=True
    )
    thread.start()
