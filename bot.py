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
# KONFIGURASI & VALIDASI ENV VAR
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID_RAW = os.getenv("DASHBOARD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

_missing = [
    name for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("DASHBOARD_CHANNEL_ID", DASHBOARD_CHANNEL_ID_RAW),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("APIFY_TOKEN", APIFY_TOKEN),
    ] if not val
]
if _missing:
    raise RuntimeError(f"Environment variable belum diset: {', '.join(_missing)}")

try:
    DASHBOARD_CHANNEL_ID = int(DASHBOARD_CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError("DASHBOARD_CHANNEL_ID harus berupa angka (channel ID).")

ACTOR_ID = "clockworks~tiktok-scraper"

HASHTAGS = [
    "FashionShowRoblox",
    "RobloxEvent",
    "RobloxIndonesia",
    "RobloxFyp",
    "RobloxAnomali",
]

client_gemini = genai.Client(api_key=GEMINI_API_KEY)

# ============================================================
# PERSISTENCE UNTUK sent_events
# ============================================================

SENT_EVENTS_FILE = "sent_events.json"
MAX_EVENTS_TO_STORE = 1000


def load_sent_events():
    if os.path.exists(SENT_EVENTS_FILE):
        try:
            with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as f:
                events = json.load(f)
                return set(events[-MAX_EVENTS_TO_STORE:])
        except Exception as e:
            logger.warning(f"Gagal load {SENT_EVENTS_FILE}: {e}")
    return set()


def save_sent_events(sent_events):
    try:
        events_list = list(sent_events)[-MAX_EVENTS_TO_STORE:]
        with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(events_list, f, indent=2)
    except Exception as e:
        logger.warning(f"Gagal save {SENT_EVENTS_FILE}: {e}")


sent_events = load_sent_events()
logger.info(f"Loaded {len(sent_events)} sent events dari file")


# ============================================================
# AI FILTER (Gemini)
# ============================================================

def _call_gemini_sync(prompt):
    response = client_gemini.models.generate_content(
        model="gemini-2.0-flash",  # ✅ MODEL TERBARU (FIXED)
        contents=prompt,
    )
    return response.text


async def ai_filter_event(text, today_str):
    prompt = f"""
    Kamu adalah AI yang memfilter event Roblox dari TikTok.
    Hari ini: {today_str}
    Konten TikTok: "{text}"

    Tugas:
    1. Tentukan apakah ini EVENT Roblox yang RELEVAN.
    2. HANYA event yang AKAN DATANG atau SEDANG BERLANGSUNG.
    3. Event yang sudah selesai / pengumuman pemenang = TOLAK.
    4. Prioritaskan event Indonesia.

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
    raw_text = ""
    try:
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(_call_gemini_sync, prompt),
            timeout=15
        )
        cleaned = raw_text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
    except asyncio.TimeoutError:
        logger.error("AI filter timeout setelah 15 detik")
        return {"is_event": False, "reason": "Timeout"}
    except json.JSONDecodeError as e:
        logger.error(f"Gemini tidak mengembalikan JSON valid: {e}")
        logger.error(f"Respons mentah: {raw_text[:300]}")
        return {"is_event": False, "reason": f"JSON parse error: {e}"}
    except Exception as e:
        logger.error(f"Error AI: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}


# ============================================================
# APIFY SCRAPER
# ============================================================

async def scrape_hashtag(hashtag, max_retries=3):
    videos = []
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Mencari video dengan #{hashtag}... (Attempt {attempt + 1}/{max_retries})")
            
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
                logger.error(f"Apify run gagal dibuat untuk #{hashtag}: {resp.status_code} {resp.text[:200]}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                return videos

            run_data = resp.json()
            run_id = run_data.get("data", {}).get("id")
            if not run_id:
                logger.error(f"Tidak dapat run_id untuk #{hashtag}: {run_data}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                return videos

            status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
            dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}"

            final_status = None
            for _ in range(20):
                await asyncio.sleep(3)
                status_resp = await asyncio.to_thread(requests.get, status_url, timeout=30)
                status_data = status_resp.json()
                status = status_data.get("data", {}).get("status")
                if status in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"):
                    final_status = status
                    break

            if final_status != "SUCCEEDED":
                logger.warning(f"Run untuk #{hashtag} berakhir dengan status: {final_status}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5 * (attempt + 1))
                    continue

            items_resp = await asyncio.to_thread(requests.get, dataset_url, timeout=30)
            items = items_resp.json()
            if not isinstance(items, list):
                logger.warning(f"Format dataset items tidak terduga untuk #{hashtag}")
                items = []

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
            
            logger.info(f"Berhasil scrape {len(videos)} videos dari #{hashtag}")
            break
            
        except Exception as e:
            logger.error(f"Error scraping #{hashtag} (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_time = 5 * (attempt + 1)
                logger.info(f"Menunggu {wait_time} detik sebelum retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Scraping #{hashtag} gagal total setelah {max_retries} attempts")

    return videos


async def get_tiktok_events():
    all_videos = []
    
    logger.info(f"Memulai scraping untuk {len(HASHTAGS)} hashtags...")
    
    for i, hashtag in enumerate(HASHTAGS, 1):
        logger.info(f"Scraping hashtag {i}/{len(HASHTAGS)}: #{hashtag}")
        videos = await scrape_hashtag(hashtag)
        all_videos.extend(videos)
        
        if i < len(HASHTAGS):
            await asyncio.sleep(2)

    logger.info(f"Total video ditemukan: {len(all_videos)}")
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
        self._initial_run_done = False
        self.is_scraping = False

    async def on_ready(self):
        logger.info(f"Bot {self.user} berhasil login!")

        if self._initial_run_done:
            logger.info("Reconnect terdeteksi, skip initial run ulang")
            return
        self._initial_run_done = True

        channel = self.get_channel(self.dashboard_channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 Bot Event Filter ONLINE!",
                description="Siap memantau event Roblox dari TikTok...",
                color=discord.Color.green(),
            )
            embed.add_field(name="📊 Hashtags dipantau", value=", ".join(f"#{tag}" for tag in HASHTAGS), inline=False)
            embed.add_field(name="⏰ Jadwal update", value="Setiap hari jam 06:00 & 18:00 WIB", inline=False)
            await channel.send(embed=embed)
        else:
            logger.warning(f"Channel dengan ID {self.dashboard_channel_id} tidak ditemukan!")

        await self.cek_dan_kirim_event()

    async def send_event_to_dashboard(self, event_info, source_author="unknown", source_hashtag="", video_url=""):
        channel = self.get_channel(self.dashboard_channel_id)
        if not channel:
            logger.warning("Channel tidak ditemukan, event tidak terkirim.")
            return

        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=f"**📝 Deskripsi**\n{event_info.get('description', 'Tidak ada deskripsi')}",
            color=discord.Color.blue(),
        )

        embed.add_field(name="📂 Kategori", value=event_info.get("category", "Tidak diketahui"), inline=True)
        embed.add_field(name="💰 Hadiah", value=event_info.get("prize", "Tidak disebutkan"), inline=True)
        embed.add_field(name="⏰ Deadline", value=event_info.get("deadline", "Tidak disebutkan"), inline=True)
        embed.add_field(name="📱 Sumber", value=f"TikTok @{source_author}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{source_hashtag}", inline=True)

        if video_url:
            embed.add_field(name="🔗 Link TikTok", value=f"[Klik di sini]({video_url})", inline=False)

        embed.set_footer(text="✅ Event lolos filter AI")
        
        try:
            await channel.send(embed=embed)
            logger.info(f"Event dikirim: {event_info.get('title')}")
        except discord.errors.HTTPException as e:
            if e.status == 429:
                logger.warning("Rate limited! Menunggu 5 detik...")
                await asyncio.sleep(5)
                try:
                    await channel.send(embed=embed)
                    logger.info(f"Event dikirim setelah retry: {event_info.get('title')}")
                except Exception as e2:
                    logger.error(f"Gagal kirim event setelah retry: {e2}")
            else:
                logger.error(f"HTTP Error saat kirim event: {e}")

    async def cek_dan_kirim_event(self):
        if self.is_scraping:
            logger.warning("Sudah ada proses scraping berjalan")
            return
        
        self.is_scraping = True
        logger.info("MULAI CEK EVENT TIKTOK...")
        
        try:
            videos = await get_tiktok_events()

            if not videos:
                logger.info("Tidak ada video ditemukan.")
                return

            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            event_count = 0

            for video in videos:
                unique_key = video.get("url", "") or video.get("caption", "")
                if unique_key in sent_events:
                    logger.debug(f"Skip duplicate: {unique_key[:50]}")
                    continue

                result = await ai_filter_event(video["caption"], today_str)

                if result.get("is_event"):
                    title = result.get("title", "Event Roblox")
                    logger.info(f"EVENT DITEMUKAN: {title}")
                    await self.send_event_to_dashboard(
                        result,
                        video["author"],
                        video["hashtag"],
                        video.get("url", ""),
                    )
                    sent_events.add(unique_key)
                    save_sent_events(sent_events)
                    event_count += 1
                else:
                    logger.debug(f"Bukan event: {result.get('reason', 'Tidak ada alasan')}")

            logger.info(f"Total event dikirim: {event_count}")
            
        except Exception as e:
            logger.error(f"Error dalam cek_dan_kirim_event: {e}")
        finally:
            self.is_scraping = False


# ============================================================
# SCHEDULER
# ============================================================

WIB = pytz.timezone("Asia/Jakarta")
scheduler = AsyncIOScheduler(timezone=WIB)

bot_instance = None


async def update_pagi():
    logger.info(f"UPDATE PAGI - {datetime.now(WIB).strftime('%H:%M WIB')}")
    if bot_instance is None or not bot_instance.is_ready():
        logger.warning("Bot belum siap/belum login, skip update pagi ini.")
        return
    await bot_instance.cek_dan_kirim_event()


async def update_sore():
    logger.info(f"UPDATE SORE - {datetime.now(WIB).strftime('%H:%M WIB')}")
    if bot_instance is None or not bot_instance.is_ready():
        logger.warning("Bot belum siap/belum login, skip update sore ini.")
        return
    await bot_instance.cek_dan_kirim_event()


scheduler.add_job(update_pagi, "cron", hour=6, minute=0, id="update_pagi")
scheduler.add_job(update_sore, "cron", hour=18, minute=0, id="update_sore")


async def main():
    global bot_instance
    bot_instance = DashboardBot()
    scheduler.start()
    logger.info("Scheduler aktif! Bot update jam 6 pagi & 6 sore WIB.")
    
    while True:
        try:
            await bot_instance.start(DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logger.error("Token Discord invalid!")
            break
        except Exception as e:
            logger.error(f"Connection error: {e}")
            logger.info("Mencoba reconnect dalam 5 detik...")
            await asyncio.sleep(5)
            bot_instance._initial_run_done = False


if __name__ == "__main__":
    asyncio.run(main())
