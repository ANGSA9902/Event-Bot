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
from typing import Optional, List, Dict, Any

# ============================================================
# LOGGING CONFIGURATION
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
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
MAX_STORED_EVENTS = 1000  # Batasi jumlah events yang disimpan


def load_sent_events() -> set:
    """Load sent events dari file JSON."""
    if os.path.exists(SENT_EVENTS_FILE):
        try:
            with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as f:
                events = json.load(f)
                logger.info(f"Loaded {len(events)} sent events dari file")
                return set(events)
        except Exception as e:
            logger.error(f"Gagal load {SENT_EVENTS_FILE}: {e}")
    return set()


def save_sent_events(sent_events: set) -> None:
    """Save sent events ke file JSON dengan batasan jumlah."""
    try:
        # Simpan maksimal MAX_STORED_EVENTS events terakhir
        events_list = list(sent_events)[-MAX_STORED_EVENTS:]
        with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(events_list, f, indent=2)
        logger.debug(f"Saved {len(events_list)} sent events ke file")
    except Exception as e:
        logger.error(f"Gagal save {SENT_EVENTS_FILE}: {e}")


sent_events = load_sent_events()


# ============================================================
# AI FILTER (Gemini)
# ============================================================

def _call_gemini_sync(prompt: str) -> str:
    """Panggil Gemini API secara synchronous."""
    response = client_gemini.models.generate_content(
        model="gemini-1.5-flash",  # Model yang valid
        contents=prompt,
    )
    return response.text


async def ai_filter_event(text: str, today_str: str) -> Dict[str, Any]:
    """
    Filter event menggunakan AI Gemini.
    Returns dictionary dengan hasil filter.
    """
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
    
    try:
        # Tambahkan timeout 15 detik untuk AI filter
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(_call_gemini_sync, prompt),
            timeout=15
        )
        
        # Clean up response
        cleaned = raw_text.replace("```json", "").replace("```", "").strip()
        
        # Parse JSON
        result = json.loads(cleaned)
        
        # Validasi required fields
        if not isinstance(result, dict):
            raise ValueError("Response bukan dictionary")
        
        return result
        
    except asyncio.TimeoutError:
        logger.error("AI filter timeout setelah 15 detik")
        return {"is_event": False, "reason": "Timeout: AI filter terlalu lama"}
        
    except json.JSONDecodeError as e:
        logger.error(f"Gemini tidak mengembalikan JSON valid: {e}")
        logger.debug(f"Respons mentah: {raw_text[:300]}")
        return {"is_event": False, "reason": f"JSON parse error: {e}"}
        
    except Exception as e:
        logger.error(f"Error AI filter: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}


# ============================================================
# APIFY SCRAPER (SEQUENTIAL dengan retry)
# ============================================================

async def scrape_hashtag(hashtag: str, max_retries: int = 3) -> List[Dict[str, str]]:
    """
    Scrape TikTok hashtag menggunakan Apify.
    Dengan retry logic dan error handling yang lebih baik.
    """
    videos = []
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Mencari video dengan #{hashtag} (attempt {attempt + 1}/{max_retries})...")
            
            # Create run
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
                raise Exception(f"HTTP {resp.status_code}")

            run_data = resp.json()
            run_id = run_data.get("data", {}).get("id")
            
            if not run_id:
                logger.error(f"Tidak dapat run_id untuk #{hashtag}: {run_data}")
                raise Exception("No run_id")

            # Poll status
            status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
            dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}"

            final_status = None
            for _ in range(20):  # Max 60 detik polling
                await asyncio.sleep(3)
                status_resp = await asyncio.to_thread(requests.get, status_url, timeout=30)
                status_data = status_resp.json()
                status = status_data.get("data", {}).get("status")
                
                if status in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"):
                    final_status = status
                    break

            if final_status != "SUCCEEDED":
                logger.warning(f"Run untuk #{hashtag} berakhir dengan status: {final_status}")
                if final_status in ("FAILED", "TIMED-OUT", "ABORTED"):
                    raise Exception(f"Run status: {final_status}")

            # Get dataset items
            items_resp = await asyncio.to_thread(requests.get, dataset_url, timeout=30)
            items = items_resp.json()
            
            if not isinstance(items, list):
                logger.warning(f"Format dataset items tidak terduga untuk #{hashtag}")
                items = []

            # Process items
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
            
            # Success, break retry loop
            logger.info(f"Berhasil scrape {len(videos)} videos dari #{hashtag}")
            break
            
        except Exception as e:
            logger.error(f"Error scraping #{hashtag} (attempt {attempt + 1}/{max_retries}): {e}")
            
            if attempt < max_retries - 1:
                # Exponential backoff: 5s, 10s, 20s
                wait_time = 5 * (2 ** attempt)
                logger.info(f"Menunggu {wait_time} detik sebelum retry...")
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Scraping #{hashtag} gagal total setelah {max_retries} attempts")

    return videos


async def get_tiktok_events() -> List[Dict[str, str]]:
    """
    Dapatkan semua events TikTok dari hashtags.
    Sequential untuk menghindari rate limit.
    """
    all_videos = []
    
    logger.info(f"Memulai scraping untuk {len(HASHTAGS)} hashtags...")
    
    for i, hashtag in enumerate(HASHTAGS, 1):
        logger.info(f"Scraping hashtag {i}/{len(HASHTAGS)}: #{hashtag}")
        videos = await scrape_hashtag(hashtag)
        all_videos.extend(videos)
        
        # Delay antar hashtags
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
        self.health_check_task = None

    async def on_ready(self):
        """Called when bot is ready and connected to Discord."""
        logger.info(f"Bot {self.user} berhasil login!")

        if self._initial_run_done:
            logger.info("Reconnect terdeteksi, skip initial run ulang")
            return
        
        self._initial_run_done = True

        # Kirim status bot online
        channel = self.get_channel(self.dashboard_channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 Bot Event Filter ONLINE!",
                description="Siap memantau event Roblox dari TikTok...",
                color=discord.Color.green(),
            )
            embed.add_field(name="📊 Hashtags dipantau", value=", ".join(f"#{tag}" for tag in HASHTAGS), inline=False)
            embed.add_field(name="⏰ Jadwal update", value="Setiap hari jam 06:00 WIB", inline=False)
            await channel.send(embed=embed)
        else:
            logger.warning(f"Channel dengan ID {self.dashboard_channel_id} tidak ditemukan!")

        # Start health check
        self.health_check_task = self.loop.create_task(self.health_check())

        # Initial run
        await self.cek_dan_kirim_event()

    async def health_check(self):
        """Kirim health check setiap 30 menit."""
        while True:
            await asyncio.sleep(1800)  # 30 menit
            
            try:
                channel = self.get_channel(self.dashboard_channel_id)
                if channel:
                    embed = discord.Embed(
                        title="💚 Bot Health Check",
                        description=f"Bot masih berjalan normal\nEvents tracked: {len(sent_events)}",
                        color=discord.Color.green(),
                    )
                    embed.set_footer(text=f"Health check: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
                    await channel.send(embed=embed)
                    logger.info("Health check sent")
            except Exception as e:
                logger.error(f"Error dalam health check: {e}")

    async def send_event
