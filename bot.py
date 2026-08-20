import discord
import asyncio
from google import genai
import json
import os
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
import pytz
import logging

# ============================================================
# LOGGING SETUP
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ============================================================
# KONFIGURASI
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID_RAW = os.getenv("DASHBOARD_CHANNEL_ID")
OWNER_ID_RAW = os.getenv("OWNER_ID")  # ID Discord kamu untuk validasi
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# Validasi
_missing = [
    name for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("DASHBOARD_CHANNEL_ID", DASHBOARD_CHANNEL_ID_RAW),
        ("OWNER_ID", OWNER_ID_RAW),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("APIFY_TOKEN", APIFY_TOKEN),
    ] if not val
]
if _missing:
    raise RuntimeError(f"Environment variable belum diset: {', '.join(_missing)}")

DASHBOARD_CHANNEL_ID = int(DASHBOARD_CHANNEL_ID_RAW)
OWNER_ID = int(OWNER_ID_RAW)

# ============================================================
# KONFIGURASI HASHTAG
# ============================================================

HASHTAGS = [
    "FashionShowRoblox",
    "RobloxEvent",
    "RobloxIndonesia",
    "RobloxFyp",
    "RobloxAnomali",
]

ACTOR_ID = "clockworks~tiktok-scraper"
client_gemini = genai.Client(api_key=GEMINI_API_KEY)

# ============================================================
# PERSISTENCE
# ============================================================

SENT_EVENTS_FILE = "sent_events.json"
PENDING_EVENTS_FILE = "pending_events.json"
MAX_EVENTS = 1000

def load_json_file(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Gagal load {filename}: {e}")
    return default

def save_json_file(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Gagal save {filename}: {e}")

# Load data
sent_events = set(load_json_file(SENT_EVENTS_FILE, []))
pending_events = load_json_file(PENDING_EVENTS_FILE, {})

# ============================================================
# AI FILTER
# ============================================================

def _call_gemini_sync(prompt):
    response = client_gemini.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
    )
    return response.text

async def ai_filter_event(text, today_str):
    prompt = f"""
    Kamu adalah AI filter event Roblox dari TikTok.
    Hari ini: {today_str}
    Caption TikTok: "{text}"

    Tugas:
    1. Tentukan apakah ini EVENT Roblox RELEVAN
    2. HANYA event AKAN DATANG atau SEDANG BERLANGSUNG
    3. Event selesai/pengumuman pemenang = TOLAK
    4. Prioritaskan event Indonesia
    5. Event harus ada hadiah (Robux/uang/item)

    Kategori:
    fashion_show, avatar_kalcer, giveaway, competition, community_event, event_kemerdekaan, event_anomali

    Output JSON:
    {{
        "is_event": true,
        "title": "...",
        "category": "...",
        "description": "...",
        "prize": "...",
        "deadline": "...",
        "reason": "..."
    }}
    Jawab HANYA JSON.
    """
    
    try:
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(_call_gemini_sync, prompt),
            timeout=15
        )
        cleaned = raw_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(cleaned)
        return result
    except Exception as e:
        logger.error(f"Error AI: {e}")
        return {"is_event": False, "reason": str(e)}

# ============================================================
# SCRAPER
# ============================================================

async def scrape_hashtag(hashtag):
    videos = []
    try:
        logger.info(f"📱 Scraping #{hashtag}...")
        
        run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
        payload = {
            "hashtags": [hashtag],
            "resultsPerPage": 3,
            "maxResults": 3,
        }
        headers = {"Content-Type": "application/json"}

        resp = await asyncio.to_thread(
            requests.post, run_url, json=payload, headers=headers, timeout=30
        )
        
        if resp.status_code >= 400:
            logger.error(f"❌ Gagal buat run untuk #{hashtag}")
            return videos

        run_id = resp.json().get("data", {}).get("id")
        if not run_id:
            return videos

        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}"

        # Poll status
        for _ in range(20):
            await asyncio.sleep(3)
            status_resp = await asyncio.to_thread(requests.get, status_url, timeout=30)
            status = status_resp.json().get("data", {}).get("status")
            if status in ("SUCCEEDED", "FAILED", "ABORTED"):
                if status != "SUCCEEDED":
                    logger.warning(f"⚠️ Run #{hashtag} status: {status}")
                    return videos
                break

        # Get items
        items_resp = await asyncio.to_thread(requests.get, dataset_url, timeout=30)
        items = items_resp.json()
        
        if not isinstance(items, list):
            return videos

        for item in items:
            caption = item.get("text") or item.get("desc") or ""
            author = item.get("authorMeta", {}).get("name", "unknown")
            video_url = item.get("webVideoUrl") or item.get("url") or ""
            
            if caption:
                videos.append({
                    "caption": caption,
                    "author": author,
                    "hashtag": hashtag,
                    "url": video_url,
                })
        
        logger.info(f"✅ #{hashtag}: {len(videos)} video ditemukan")
        
    except Exception as e:
        logger.error(f"❌ Error #{hashtag}: {e}")
    
    return videos

async def get_tiktok_events():
    all_videos = []
    logger.info(f"🔍 Mulai scanning {len(HASHTAGS)} hashtags...")
    
    for hashtag in HASHTAGS:
        videos = await scrape_hashtag(hashtag)
        all_videos.extend(videos)
        await asyncio.sleep(2)
    
    logger.info(f"📊 Total video: {len(all_videos)}")
    return all_videos

# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

class DashboardBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.dashboard_channel_id = DASHBOARD_CHANNEL_ID
        self.owner_id = OWNER_ID
        self._initial_run_done = False
        self.is_scanning = False

    async def on_ready(self):
        logger.info(f"✅ Bot {self.user} login!")
        
        if self._initial_run_done:
            return
        self._initial_run_done = True

        channel = self.get_channel(self.dashboard_channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 BOT EVENT FILTER ONLINE!",
                description="**Sistem pemantau event Roblox dari TikTok**\n\n"
                           "📱 Memantau hashtag:\n"
                           f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}\n\n"
                           "⏰ Update otomatis: **06:00 WIB**\n"
                           "🤖 Filter: **AI Gemini**\n"
                           "✅ Validasi: **Manual oleh Owner**",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        else:
            logger.warning("⚠️ Channel dashboard tidak ditemukan!")

        # Mulai scanning pertama
        await self.scan_and_send_events()

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        # Command untuk trigger scan manual
        if message.content.lower() == "!scan":
            if message.author.id == self.owner_id:
                await message.channel.send("🔍 Memulai scanning TikTok...")
                await self.scan_and_send_events()
            else:
                await message.channel.send("❌ Hanya owner yang bisa menggunakan command ini!")
        
        # Command untuk lihat pending events
        elif message.content.lower() == "!pending":
            if message.author.id == self.owner_id:
                await self.show_pending_events(message.channel)
        
        # Command untuk approve event
        elif message.content.lower().startswith("!approve"):
            if message.author.id == self.owner_id:
                event_id = message.content.replace("!approve", "").strip()
                await self.approve_event(event_id, message.channel)
        
        # Command untuk reject event
        elif message.content.lower().startswith("!reject"):
            if message.author.id == self.owner_id:
                event_id = message.content.replace("!reject", "").strip()
                await self.reject_event(event_id, message.channel)

    async def scan_and_send_events(self):
        if self.is_scanning:
            logger.warning("⚠️ Scanning sedang berjalan")
            return
        
        self.is_scanning = True
        logger.info("🚀 MULAI SCANNING EVENT...")
        
        try:
            videos = await get_tiktok_events()
            
            if not videos:
                logger.info("ℹ️ Tidak ada video ditemukan")
                return

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            found_count = 0

            for video in videos:
                unique_key = video.get("url", "") or video.get("caption", "")
                
                # Skip jika sudah pernah diproses
                if unique_key in sent_events or unique_key in pending_events:
                    continue

                # AI Filter
                result = await ai_filter_event(video["caption"], today_str)

                if result.get("is_event"):
                    logger.info(f"🎯 EVENT DITEMUKAN: {result.get('title')}")
                    
                    # Kirim ke owner untuk validasi
                    await self.send_validation_request(result, video)
                    
                    # Simpan ke pending
                    event_id = str(len(pending_events) + 1)
                    pending_events[event_id] = {
                        "event_info": result,
                        "video": video,
                        "unique_key": unique_key,
                        "timestamp": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
                    }
                    save_json_file(PENDING_EVENTS_FILE, pending_events)
                    
                    found_count += 1

            logger.info(f"✅ Total event ditemukan: {found_count}")
            
        except Exception as e:
            logger.error(f"❌ Error scanning: {e}")
        finally:
            self.is_scanning = False

    async def send_validation_request(self, event_info, video):
        """Kirim request validasi ke owner via DM."""
        owner = await self.fetch_user(self.owner_id)
        if not owner:
            logger.error("❌ Tidak bisa menemukan owner")
            return

        embed = discord.Embed(
            title="🔍 EVENT PERLU VALIDASI",
            description=f"**Event ditemukan, mohon validasi:**\n\n"
                       f"**📝 Judul:** {event_info.get('title', 'Tidak ada judul')}\n"
                       f"**📂 Kategori:** {event_info.get('category', 'Unknown')}\n"
                       f"**💰 Hadiah:** {event_info.get('prize', 'Tidak disebutkan')}\n"
                       f"**⏰ Deadline:** {event_info.get('deadline', 'Tidak disebutkan')}\n\n"
                       f"**📝 Deskripsi:**\n{event_info.get('description', 'Tidak ada deskripsi')}\n\n"
                       f"**📱 Sumber:** TikTok @{video.get('author', 'unknown')}\n"
                       f"**🏷️ Hashtag:** #{video.get('hashtag', '')}",
            color=discord.Color.orange(),
        )
        
        if video.get("url"):
            embed.add_field(name="🔗 Link", value=video["url"], inline=False)
        
        embed.set_footer(text="Gunakan command: !approve <id> atau !reject <id>")
        
        try:
            await owner.send(embed=embed)
            logger.info(f"📤 Request validasi terkirim ke owner: {event_info.get('title')}")
        except Exception as e:
            logger.error(f"❌ Gagal kirim DM ke owner: {e}")

    async def show_pending_events(self, channel):
        """Tampilkan semua pending events."""
        if not pending_events:
            await channel.send("📭 Tidak ada event pending.")
            return
        
        embed = discord.Embed(
            title="📋 PENDING EVENTS",
            description="Event yang menunggu validasi:",
            color=discord.Color.orange(),
        )
        
        for event_id, data in pending_events.items():
            event_info = data.get("event_info", {})
            embed.add_field(
                name=f"ID: {event_id} - {event_info.get('title', 'No title')}",
                value=f"Kategori: {event_info.get('category', 'Unknown')}\n"
                      f"Hadiah: {event_info.get('prize', 'Tidak disebutkan')}",
                inline=False
            )
        
        embed.set_footer(text="Approve: !approve <id> | Reject: !reject <id>")
        await channel.send(embed=embed)

    async def approve_event(self, event_id, channel):
        """Approve event dan kirim ke dashboard."""
        if event_id not in pending_events:
            await channel.send(f"❌ Event dengan ID {event_id} tidak ditemukan!")
            return
        
        data = pending_events[event_id]
        event_info = data.get("event_info", {})
        video = data.get("video", {})
        unique_key = data.get("unique_key", "")
        
        # Kirim ke dashboard
        await self.send_event_to_dashboard(event_info, video)
        
        # Pindahkan dari pending ke sent
        sent_events.add(unique_key)
        save_json_file(SENT_EVENTS_FILE, list(sent_events))
        
        # Hapus dari pending
        del pending_events[event_id]
        save_json_file(PENDING_EVENTS_FILE, pending_events)
        
        await channel.send(f"✅ Event ID {event_id} di-approve dan dikirim ke dashboard!")

    async def reject_event(self, event_id, channel):
        """Reject event."""
        if event_id not in pending_events:
            await channel.send(f"❌ Event dengan ID {event_id} tidak ditemukan!")
            return
        
        data = pending_events[event_id]
        unique_key = data.get("unique_key", "")
        
        # Tambahkan ke sent_events agar tidak di-scan lagi
        sent_events.add(unique_key)
        save_json_file(SENT_EVENTS_FILE, list(sent_events))
        
        # Hapus dari pending
        del pending_events[event_id]
        save_json_file(PENDING_EVENTS_FILE, pending_events)
        
        await channel.send(f"❌ Event ID {event_id} di-reject.")

    async def send_event_to_dashboard(self, event_info, video):
        """Kirim event yang sudah di-approve ke dashboard."""
        channel = self.get_channel(self.dashboard_channel_id)
        if not channel:
            logger.error("❌ Channel dashboard tidak ditemukan")
            return

        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=f"**📝 Deskripsi**\n{event_info.get('description', 'Tidak ada deskripsi')}",
            color=discord.Color.blue(),
        )

        embed.add_field(name="📂 Kategori", value=event_info.get("category", "Unknown"), inline=True)
        embed.add_field(name="💰 Hadiah", value=event_info.get("prize", "Tidak disebutkan"), inline=True)
        embed.add_field(name="⏰ Deadline", value=event_info.get("deadline", "Tidak disebutkan"), inline=True)
        embed.add_field(name="📱 Sumber", value=f"TikTok @{video.get('author', 'unknown')}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{video.get('hashtag', '')}", inline=True)
        
        if video.get("url"):
            embed.add_field(name="🔗 Link TikTok", value=f"[Klik di sini]({video['url']})", inline=False)

        embed.set_footer(text="✅ Event tervalidasi oleh owner")
        
        try:
            await channel.send(embed=embed)
            logger.info(f"📤 Event terkirim ke dashboard: {event_info.get('title')}")
        except Exception as e:
            logger.error(f"❌ Gagal kirim ke dashboard: {e}")

# ============================================================
# SCHEDULER
# ============================================================

WIB = pytz.timezone("Asia/Jakarta")
scheduler = AsyncIOScheduler(timezone=WIB)
bot_instance = None

async def scheduled_update():
    logger.info(f"⏰ UPDATE JADWAL - {datetime.now(WIB).strftime('%H:%M WIB')}")
    if bot_instance and bot_instance.is_ready():
        await bot_instance.scan_and_send_events()

# Update setiap jam 6 pagi WIB
scheduler.add_job(scheduled_update, "cron", hour=6, minute=0, id="update_pagi")

# ============================================================
# MAIN
# ============================================================

async def main():
    global bot_instance
    bot_instance = DashboardBot()
    scheduler.start()
    logger.info("⏰ Scheduler aktif! Update jam 06:00 WIB")
    
    await bot_instance.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
