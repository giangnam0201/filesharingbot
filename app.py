import os
import discord
from discord.ext import commands, tasks
import aiofiles
import aiohttp
import asyncio
import json
import uuid
import hashlib
import time
import shutil
from datetime import datetime
from typing import Optional, Dict
import mimetypes
import humanize

# Configuration
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set!")

CONFIG = {
    "STORAGE_PATH": "./file_storage",
    "DB_PATH": "./file_database.json",
    "MAX_FILE_SIZE": 100 * 1024 * 1024,
    "DEFAULT_EXPIRY_HOURS": 24,
    "MAX_DOWNLOADS": 10,
    "ALLOWED_FILE_TYPES": ["*"],
    "USER_QUOTA_MB": 500,
    "CLEANUP_INTERVAL_HOURS": 1,
    "LOG_CHANNEL_ID": None,
    "ADMIN_IDS": [],
    "BACKUP_ENABLED": True,
    "BACKUP_INTERVAL_HOURS": 24,
    "RATE_LIMIT_PER_MINUTE": 10
}

os.makedirs(CONFIG["STORAGE_PATH"], exist_ok=True)
os.makedirs("./backups", exist_ok=True)

# Database Manager
class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.data = self.load()
    
    def load(self) -> Dict:
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {
                "files": {},
                "users": {},
                "stats": {
                    "total_uploads": 0,
                    "total_downloads": 0,
                    "storage_used": 0
                }
            }
    
    def save(self):
        with open(self.db_path, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def add_file(self, file_code: str, metadata: Dict):
        self.data["files"][file_code] = metadata
        self.save()
    
    def get_file(self, file_code: str) -> Optional[Dict]:
        return self.data["files"].get(file_code)
    
    def remove_file(self, file_code: str):
        if file_code in self.data["files"]:
            file_data = self.data["files"][file_code]
            self.data["stats"]["storage_used"] -= file_data["size"]
            del self.data["files"][file_code]
            self.save()
    
    def update_user_quota(self, user_id: int, size_delta: int):
        user_id = str(user_id)
        if user_id not in self.data["users"]:
            self.data["users"][user_id] = {"used_space": 0, "uploads": []}
        self.data["users"][user_id]["used_space"] += size_delta
        self.save()

db = Database(CONFIG["DB_PATH"])

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    case_insensitive=True
)

user_cooldowns = {}

# Helper Functions
def generate_code() -> str:
    return str(uuid.uuid4())[:8].upper()

def get_file_hash(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def check_rate_limit(user_id: int) -> bool:
    now = time.time()
    if user_id not in user_cooldowns:
        user_cooldowns[user_id] = []
    
    user_cooldowns[user_id] = [
        t for t in user_cooldowns[user_id] 
        if now - t < 60
    ]
    
    if len(user_cooldowns[user_id]) >= CONFIG["RATE_LIMIT_PER_MINUTE"]:
        return False
    
    user_cooldowns[user_id].append(now)
    return True

def format_file_size(size: int) -> str:
    return humanize.naturalsize(size)

def is_admin(user_id: int) -> bool:
    return user_id in CONFIG["ADMIN_IDS"]

def validate_file_type(filename: str) -> bool:
    if "*" in CONFIG["ALLOWED_FILE_TYPES"]:
        return True
    ext = filename.split('.')[-1].lower()
    return ext in CONFIG["ALLOWED_FILE_TYPES"]

def get_file_preview(file_path: str, mime_type: str) -> Optional[str]:
    try:
        if mime_type.startswith('text/'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read(500)
                return "```" + content + "```"
        elif mime_type.startswith('image/'):
            return "🖼️ Image file (preview in Discord)"
    except:
        pass
    return None

# File Management Commands
class FileShare(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cleanup_task.start()
        if CONFIG["BACKUP_ENABLED"]:
            self.backup_task.start()

    @commands.command(name="upload", aliases=["up"])
    async def upload_file(self, ctx, *, description: str = "No description"):
        if not ctx.message.attachments:
            embed = discord.Embed(
                title="❌ No File Attached",
                description="Please attach a file with your message!",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        if not check_rate_limit(ctx.author.id):
            embed = discord.Embed(
                title="⏱️ Rate Limited",
                description="Please slow down! Try again in a minute.",
                color=0xFFA502
            )
            await ctx.reply(embed=embed)
            return
        
        attachment = ctx.message.attachments[0]
        
        if attachment.size > CONFIG["MAX_FILE_SIZE"]:
            embed = discord.Embed(
                title="❌ File Too Large",
                description=f"Max size: {format_file_size(CONFIG['MAX_FILE_SIZE'])}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        if not validate_file_type(attachment.filename):
            embed = discord.Embed(
                title="❌ Invalid File Type",
                description=f"Allowed types: {', '.join(CONFIG['ALLOWED_FILE_TYPES'])}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        user_id = str(ctx.author.id)
        user_quota = db.data["users"].get(user_id, {"used_space": 0})
        if user_quota["used_space"] + attachment.size > CONFIG["USER_QUOTA_MB"] * 1024 * 1024:
            embed = discord.Embed(
                title="❌ Quota Exceeded",
                description=f"Quota: {format_file_size(CONFIG['USER_QUOTA_MB'] * 1024 * 1024)}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        file_code = generate_code()
        file_path = os.path.join(CONFIG["STORAGE_PATH"], f"{file_code}_{attachment.filename}")
        
        try:
            await attachment.save(file_path)
            
            mime_type, _ = mimetypes.guess_type(file_path)
            file_hash = get_file_hash(file_path)
            
            metadata = {
                "filename": attachment.filename,
                "size": attachment.size,
                "code": file_code,
                "uploaded_by": ctx.author.id,
                "uploaded_at": time.time(),
                "description": description,
                "download_count": 0,
                "max_downloads": CONFIG["MAX_DOWNLOADS"],
                "expiry_time": time.time() + (CONFIG["DEFAULT_EXPIRY_HOURS"] * 3600),
                "file_path": file_path,
                "mime_type": mime_type,
                "file_hash": file_hash,
                "original_name": ctx.author.name
            }
            
            db.add_file(file_code, metadata)
            db.update_user_quota(ctx.author.id, attachment.size)
            db.data["stats"]["total_uploads"] += 1
            db.data["stats"]["storage_used"] += attachment.size
            db.save()
            
            file_desc = f"**📁 {attachment.filename}**\n📝 {description}"
            embed = discord.Embed(
                title="✅ File Uploaded Successfully!",
                description=file_desc,
                color=0x51CF66,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="🔑 Code", value=f"`{file_code}`", inline=True)
            embed.add_field(name="📊 Size", value=format_file_size(attachment.size), inline=True)
            embed.add_field(name="⏰ Expires", value=f"{CONFIG['DEFAULT_EXPIRY_HOURS']}h", inline=True)
            embed.add_field(name="🔗 Download Link", value=f"Use: `!download {file_code}`", inline=False)
            embed.set_footer(text=f"Uploaded by {ctx.author.name} | Max downloads: {CONFIG['MAX_DOWNLOADS']}")
            
            await ctx.reply(embed=embed)
            
            if CONFIG["LOG_CHANNEL_ID"]:
                await self.log_upload(ctx, metadata)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Upload Failed",
                description=f"Error: {str(e)}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            if os.path.exists(file_path):
                os.remove(file_path)

    @commands.command(name="download", aliases=["get", "dl"])
    async def download_file(self, ctx, code: str):
        code = code.upper().strip()
        file_data = db.get_file(code)
        
        if not file_data:
            embed = discord.Embed(
                title="❌ Invalid Code",
                description="File not found or expired!",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        if time.time() > file_data["expiry_time"]:
            embed = discord.Embed(
                title="⏰ File Expired",
                description="This file is no longer available.",
                color=0xFFA502
            )
            await ctx.reply(embed=embed)
            await self.cleanup_expired()
            return
        
        if file_data["download_count"] >= file_data["max_downloads"]:
            embed = discord.Embed(
                title="🚫 Download Limit Reached",
                description="This file has reached its maximum download limit.",
                color=0xFFA502
            )
            await ctx.reply(embed=embed)
            return
        
        if not os.path.exists(file_data["file_path"]):
            embed = discord.Embed(
                title="❌ File Missing",
                description="The file is missing from storage.",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            db.remove_file(code)
            return
        
        try:
            file = discord.File(file_data["file_path"], filename=file_data["filename"])
            preview = get_file_preview(file_data["file_path"], file_data["mime_type"])
            if preview:
                await ctx.reply(f"📄 **Preview:**\n{preview}", mention_author=False)
            
            await ctx.reply(
                f"📥 **{file_data['filename']}** ({format_file_size(file_data['size'])})",
                file=file
            )
            
            db.data["files"][code]["download_count"] += 1
            db.data["stats"]["total_downloads"] += 1
            db.save()
            
            if CONFIG["LOG_CHANNEL_ID"]:
                await self.log_download(ctx, file_data)
            
            if db.data["files"][code]["download_count"] >= file_data["max_downloads"]:
                await ctx.send("🗑️ File reached max downloads and was deleted.")
                await self.delete_file(code)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Download Failed",
                description=f"Error: {str(e)}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)

    @commands.command(name="info", aliases=["code"])
    async def file_info(self, ctx, code: str):
        code = code.upper().strip()
        file_data = db.get_file(code)
        
        if not file_data:
            embed = discord.Embed(
                title="❌ Invalid Code",
                description="File not found!",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        expires_in = file_data["expiry_time"] - time.time()
        downloads_left = file_data["max_downloads"] - file_data["download_count"]
        
        embed = discord.Embed(
            title=f"📊 File Information: {code}",
            color=0x3398DB
        )
        
        embed.add_field(name="📁 Filename", value=file_data["filename"], inline=False)
        embed.add_field(name="📊 Size", value=format_file_size(file_data["size"]), inline=True)
        embed.add_field(name="📥 Downloads", value=f"{file_data['download_count']}/{file_data['max_downloads']}", inline=True)
        embed.add_field(name="⏰ Expires In", value=f"{int(expires_in // 3600)}h {int((expires_in % 3600) // 60)}m", inline=True)
        embed.add_field(name="📝 Description", value=file_data["description"], inline=False)
        embed.add_field(name="👤 Uploaded By", value=file_data["original_name"], inline=True)
        
        if downloads_left <= 0 or expires_in <= 0:
            embed.color = 0xFF6B6B
        
        await ctx.reply(embed=embed)

    @commands.command(name="list", aliases=["files"])
    async def list_files(self, ctx, user: discord.Member = None):
        target_user = user or ctx.author
        user_id = str(target_user.id)
        
        if user_id not in db.data["users"]:
            embed = discord.Embed(
                title="📭 No Files",
                description=f"{target_user.name} hasn't uploaded any files.",
                color=0xFFA502
            )
            await ctx.reply(embed=embed)
            return
        
        user_files = []
        for code, file_data in db.data["files"].items():
            if file_data["uploaded_by"] == int(user_id):
                user_files.append(file_data)
        
        if not user_files:
            embed = discord.Embed(
                title="📭 No Active Files",
                description="No active files found.",
                color=0xFFA502
            )
            await ctx.reply(embed=embed)
            return
        
        page_size = 5
        pages = [user_files[i:i + page_size] for i in range(0, len(user_files), page_size)]
        
        for page_num, page in enumerate(pages):
            embed = discord.Embed(
                title=f"📋 {target_user.name}'s Files (Page {page_num + 1}/{len(pages)})",
                color=0x3398DB
            )
            
            for file_data in page:
                expires_in = file_data["expiry_time"] - time.time()
                status = "🟢" if expires_in > 0 else "🔴"
                
                embed.add_field(
                    name=f"{status} {file_data['code']} - {file_data['filename']}",
                    value=f"📥 {file_data['download_count']}/{file_data['max_downloads']} | "
                          f"⏰ {int(expires_in // 3600)}h left | "
                          f"📊 {format_file_size(file_data['size'])}",
                    inline=False
                )
            
            embed.set_footer(text=f"Quota: {format_file_size(db.data['users'][user_id]['used_space'])} / {CONFIG['USER_QUOTA_MB']}MB")
            await ctx.reply(embed=embed)

    @commands.command(name="delete", aliases=["remove"])
    async def delete_file(self, ctx, code: str):
        code = code.upper().strip()
        file_data = db.get_file(code)
        
        if not file_data:
            embed = discord.Embed(
                title="❌ Invalid Code",
                description="File not found!",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        if file_data["uploaded_by"] != ctx.author.id and not is_admin(ctx.author.id):
            embed = discord.Embed(
                title="🚫 Access Denied",
                description="You can only delete your own files!",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)
            return
        
        try:
            if os.path.exists(file_data["file_path"]):
                os.remove(file_data["file_path"])
            
            db.remove_file(code)
            
            embed = discord.Embed(
                title="🗑️ File Deleted",
                description=f"**{file_data['filename']}** has been permanently deleted.",
                color=0x51CF66
            )
            await ctx.reply(embed=embed)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Delete Failed",
                description=f"Error: {str(e)}",
                color=0xFF6B6B
            )
            await ctx.reply(embed=embed)

    @commands.command(name="quota", aliases=["space"])
    async def check_quota(self, ctx, user: discord.Member = None):
        target_user = user or ctx.author
        user_id = str(target_user.id)
        
        used = db.data["users"].get(user_id, {"used_space": 0})["used_space"]
        percentage = (used / (CONFIG["USER_QUOTA_MB"] * 1024 * 1024)) * 100
        
        embed = discord.Embed(
            title=f"💾 {target_user.name}'s Storage Quota",
            color=0x3398DB
        )
        
        embed.add_field(
            name=f"📊 Used: {format_file_size(used)} / {CONFIG['USER_QUOTA_MB']}MB",
            value=f"{'█' * int(percentage / 10)}{'░' * (10 - int(percentage / 10))} {percentage:.1f}%",
            inline=False
        )
        
        await ctx.reply(embed=embed)

    @commands.command(name="admin_delete")
    @commands.check(is_admin)
    async def admin_delete(self, ctx, code: str):
        await self.delete_file(ctx, code)

    # FIX: Renamed from bot_stats to show_stats
    @commands.command(name="stats", aliases=["statistics"])
    async def show_stats(self, ctx):
        embed = discord.Embed(
            title="📈 Bot Statistics",
            color=0x9B59B6
        )
        
        stats = db.data["stats"]
        embed.add_field(name="📤 Total Uploads", value=stats["total_uploads"], inline=True)
        embed.add_field(name="📥 Total Downloads", value=stats["total_downloads"], inline=True)
        embed.add_field(name="💾 Storage Used", value=format_file_size(stats["storage_used"]), inline=True)
        embed.add_field(name="📁 Active Files", value=len(db.data["files"]), inline=True)
        embed.add_field(name="👥 Active Users", value=len(db.data["users"]), inline=True)
        embed.add_field(name="⏰ Uptime", value=f"{int(time.time() // 3600)} hours", inline=True)
        
        await ctx.reply(embed=embed)

    @commands.command(name="setexpiry", aliases=["extend"])
    async def set_expiry(self, ctx, code: str, hours: int):
        code = code.upper().strip()
        file_data = db.get_file(code)
        
        if not file_data:
            await ctx.reply("❌ File not found!")
            return
        
        if file_data["uploaded_by"] != ctx.author.id and not is_admin(ctx.author.id):
            await ctx.reply("🚫 You don't own this file!")
            return
        
        new_expiry = time.time() + (hours * 3600)
        db.data["files"][code]["expiry_time"] = new_expiry
        db.save()
        
        await ctx.reply(f"✅ Expiry set to {hours} hours from now.")

    @commands.command(name="setdownloads", aliases=["limit"])
    async def set_download_limit(self, ctx, code: str, limit: int):
        code = code.upper().strip()
        file_data = db.get_file(code)
        
        if not file_data:
            await ctx.reply("❌ File not found!")
            return
        
        if file_data["uploaded_by"] != ctx.author.id and not is_admin(ctx.author.id):
            await ctx.reply("🚫 You don't own this file!")
            return
        
        db.data["files"][code]["max_downloads"] = limit
        db.save()
        
        await ctx.reply(f"✅ Download limit set to {limit}.")

    @commands.command(name="search", aliases=["find"])
    async def search_files(self, ctx, *, query: str):
        results = []
        for code, file_data in db.data["files"].items():
            if query.lower() in file_data["filename"].lower() or \
               query.lower() in file_data["description"].lower():
                results.append(file_data)
        
        if not results:
            await ctx.reply("🔍 No files found matching your search.")
            return
        
        embed = discord.Embed(
            title=f"🔍 Search Results for '{query}'",
            description=f"Found {len(results)} files:",
            color=0x3398DB
        )
        
        for file_data in results[:5]:
            expires_in = file_data["expiry_time"] - time.time()
            embed.add_field(
                name=f"{file_data['code']} - {file_data['filename']}",
                value=f"📥 {file_data['download_count']} downloads | "
                      f"⏰ {int(expires_in // 3600)}h left | "
                      f"👤 {file_data['original_name']}",
                inline=False
            )
        
        await ctx.reply(embed=embed)

    @commands.command(name="help", aliases=["commands"])
    async def show_help(self, ctx):
        embed = discord.Embed(
            title="📚 FileShare Bot Commands",
            description="A powerful file sharing bot with codes!",
            color=0x3398DB
        )
        
        embed.add_field(
            name="📤 Upload & Download",
            value="`!upload <description>` - Upload with description\n"
                  "`!download <code>` - Download file\n"
                  "`!info <code>` - File information",
            inline=False
        )
        
        embed.add_field(
            name="📋 Management",
            value="`!list [@user]` - List your files\n"
                  "`!delete <code>` - Delete your file\n"
                  "`!quota [@user]` - Check storage quota",
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Advanced",
            value="`!setexpiry <code> <hours>` - Set expiry time\n"
                  "`!setdownloads <code> <limit>` - Set download limit\n"
                  "`!search <query>` - Search files",
            inline=False
        )
        
        embed.add_field(
            name="📈 Info",
            value="`!stats` - Bot statistics\n"
                  "`!help` - Show this message",
            inline=False
        )
        
        if is_admin(ctx.author.id):
            embed.add_field(
                name="🔧 Admin",
                value="`!admin_delete <code>` - Delete any file\n"
                      "`!cleanup` - Force cleanup\n"
                      "`!backup` - Create backup",
                inline=False
            )
        
        embed.set_footer(text="Bot by FileShare | Secure file sharing made easy!")
        await ctx.reply(embed=embed)

    async def log_upload(self, ctx, file_data: Dict):
        if not CONFIG["LOG_CHANNEL_ID"]:
            return
        
        channel = self.bot.get_channel(CONFIG["LOG_CHANNEL_ID"])
        if not channel:
            return
        
        embed = discord.Embed(
            title="📤 File Uploaded",
            color=0x51CF66,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="👤 User", value=f"{ctx.author} ({ctx.author.id})", inline=True)
        embed.add_field(name="📁 File", value=file_data["filename"], inline=True)
        embed.add_field(name="📊 Size", value=format_file_size(file_data["size"]), inline=True)
        embed.add_field(name="🔑 Code", value=file_data["code"], inline=True)
        embed.add_field(name="📝 Description", value=file_data["description"], inline=False)
        
        await channel.send(embed=embed)

    async def log_download(self, ctx, file_data: Dict):
        if not CONFIG["LOG_CHANNEL_ID"]:
            return
        
        channel = self.bot.get_channel(CONFIG["LOG_CHANNEL_ID"])
        if not channel:
            return
        
        embed = discord.Embed(
            title="📥 File Downloaded",
            color=0x3398DB,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="👤 Downloaded By", value=f"{ctx.author} ({ctx.author.id})", inline=True)
        embed.add_field(name="📁 File", value=file_data["filename"], inline=True)
        embed.add_field(name="👤 Uploaded By", value=file_data["original_name"], inline=True)
        embed.add_field(name="🔑 Code", value=file_data["code"], inline=True)
        embed.add_field(name="📊 Download Count", value=f"{file_data['download_count']}/{file_data['max_downloads']}", inline=True)
        
        await channel.send(embed=embed)

    @tasks.loop(hours=CONFIG["CLEANUP_INTERVAL_HOURS"])
    async def cleanup_task(self):
        await self.cleanup_expired()
    
    async def cleanup_expired(self):
        now = time.time()
        expired = []
        
        for code, file_data in list(db.data["files"].items()):
            if now > file_data["expiry_time"] or \
               file_data["download_count"] >= file_data["max_downloads"]:
                expired.append(code)
        
        for code in expired:
            file_data = db.data["files"][code]
            try:
                if os.path.exists(file_data["file_path"]):
                    os.remove(file_data["file_path"])
                db.remove_file(code)
            except:
                pass
    
    @tasks.loop(hours=CONFIG["BACKUP_INTERVAL_HOURS"])
    async def backup_task(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"./backups/database_{timestamp}.json"
        shutil.copy(CONFIG["DB_PATH"], backup_path)
        
        backups = sorted(os.listdir("./backups"))
        for old_backup in backups[:-10]:
            os.remove(f"./backups/{old_backup}")

    @cleanup_task.before_loop
    @backup_task.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()

    @commands.command(name="cleanup")
    @commands.check(is_admin)
    async def force_cleanup(self, ctx):
        await ctx.send("🧹 Starting cleanup...")
        await self.cleanup_expired()
        await ctx.send("✅ Cleanup complete!")

    @commands.command(name="backup")
    @commands.check(is_admin)
    async def force_backup(self, ctx):
        await self.backup_task()
        await ctx.send("💾 Backup created!")

@bot.event
async def on_ready():
    print(f"🚀 Bot is ready! Logged in as {bot.user}")
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name="!help | File Sharing"
    )
    await bot.change_presence(activity=activity)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        embed = discord.Embed(
            title="❌ Unknown Command",
            description="Use `!help` to see available commands.",
            color=0xFF6B6B
        )
        await ctx.reply(embed=embed)
    elif isinstance(error, commands.CheckFailure):
        await ctx.reply("🚫 You don't have permission to use this command!")
    else:
        embed = discord.Embed(
            title="❌ Error",
            description=f"An error occurred: {str(error)}",
            color=0xFF6B6B
        )
        await ctx.reply(embed=embed)
        print(f"Error: {error}")

async def main():
    async with bot:
        await bot.add_cog(FileShare(bot))
        
        if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
            print("❌ DISCORD_TOKEN environment variable not set!")
            print("Set it with: export DISCORD_TOKEN='your_token_here'")
            return
        
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
