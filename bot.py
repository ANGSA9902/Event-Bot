import discord
import asyncio
import requests
import json
import os
import logging
from datetime import datetime
import pytz

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# KONFIGURASI
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
APIFY_TOKEN = os.getenv("APIFY_TOKEN")  # <-- WAJIB diisi!

HASHTAGS = [
    "FashionShowRoblox",
    "RobloxEvent",
    "RobloxIndonesia",
    "RobloxFyp",
    "RobloxAnomali",
]

KEYWORDS = [
    "event", "giveaway", "hadiah", "robux", "competition",
    "lomba", "fashion show", "fashionshow", "anomali",
    "avatar", "kalcer", "kemerdekaan", "join", "ikuti"
]

WIB = pytz.timezone("Asia/Jakarta")

# ============================================================
# SIMPAN VIDEO YANG SUDAH DIKIRIM
# ============================================================

SENT_FILE = "sent_videos.json"

def load_sent():
    try:
        with open(SENT_FILE, "r") as f:
            return set(json.load(f))
    except:
        return set()

def save_sent(sent):
    with open(SENT_FILE, "w") as f:
        json.dump(list(sent), f)

sent_videos = load_sent()

# ============================================================
# SCRAPE TIKTOK PAKAI APIFY
# ============================================================

async def scrape_tiktok_apify(hashtag):
    """Scrape TikTok pake Apify API."""
    videos = []
    
    if not APIFY_TOKEN:
        logger.error("❌ APIFY_TOKEN tidak ditemukan! Set di Railway Variables.")
        return videos
    
    try:
        # 1. Jalankan actor Apify
        run_url = "https://api.apify.com/v2/acts/makework36~tiktok-scraper-api/runs"
        params = {
            "token": APIFY_TOKEN
        }
        payload = {
            "hashtags": [hashtag],
            "resultsLimit": 5,
            "shouldDownloadVideos": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadComments": False
        }
        
        response = await asyncio.to_thread(
            requests.post, run_url, params=params, json=payload, timeout=30
        )
        
        if response.status_code != 200:
            logger.error(f"❌ Apify error: {response.status_code}")
            return videos
        
        run_data = response.json()
        run_id = run_data.get("data", {}).get("id")
        
        if not run_id:
            logger.error("❌ Gagal dapat run_id dari Apify")
            return videos
        
        logger.info(f"🔄 Apify running... (ID: {run_id})")
        
        # 2. Tunggu hasil (polling)
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
        max_wait = 30  # detik
        waited = 0
        
        while waited < max_wait:
            status_response = await asyncio.to_thread(
                requests.get, status_url, params={"token": APIFY_TOKEN}, timeout=10
            )
            status_data = status_response.json()
            status = status_data.get("data", {}).get("status")
            
            if status == "SUCCEEDED":
                # 3. Ambil hasil
                dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items"
                dataset_response = await asyncio.to_thread(
                    requests.get, dataset_url, params={"token": APIFY_TOKEN}, timeout=10
                )
                items = dataset_response.json()
                
                for item in items:
                    caption = item.get("caption", "")
                    author = item.get("author", {}).get("uniqueId", "unknown") if isinstance(item.get("author"), dict) else item.get("author", "unknown")
                    video_id = item.get("id", "")
                    video_url = f"https://www.tiktok.com/@{author}/video/{video_id}" if video_id else item.get("webVideoUrl", "")
                    
                    if caption:
                        videos.append({
                            "caption": caption,
                            "author": author,
                            "hashtag": hashtag,
                            "url": video_url,
                        })
                
                logger.info(f"✅ #{hashtag}: {len(videos)} video dari Apify")
                break
                
            elif status in ["FAILED", "TIMED-OUT", "ABORTED"]:
                logger.error(f"❌ Apify run {status}")
                break
            
            await asyncio.sleep(3)
            waited += 3
        
        if waited >= max_wait:
            logger.warning(f"⏰ Timeout waiting for Apify #{hashtag}")
        
    except Exception as e:
        logger.error(f"❌ Error Apify #{hashtag}: {e}")
    
    return videos

# ============================================================
# CEK KEYWORD
# ============================================================

def cek_keyword(caption):
    """Cek apakah caption mengandung keyword."""
    caption_lower = caption.lower()
    found = []
    
    for keyword in KEYWORDS:
        if keyword in caption_lower:
            found.append(keyword)
    
    return found

# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True

class TikTokBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.channel_id = CHANNEL_ID
        self.is_scanning = False

    async def on_ready(self):
        logger.info(f"✅ Bot {self.user} login!")
        
        if not APIFY_TOKEN:
            logger.warning("⚠️ APIFY_TOKEN belum diisi! Bot tidak bisa scrape.")
        
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 TikTok Monitor ONLINE! (Apify)",
                description=f"Scraping TikTok pake Apify API\n\n"
                           f"📱 Hashtags:\n"
                           f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}\n\n"
                           f"🔑 Keywords:\n"
                           f"{', '.join(KEYWORDS)}",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        
        # Mulai scan
        await self.scan_events()
        
        # Scan setiap 6 jam
        while True:
            await asyncio.sleep(21600)
            await self.scan_events()

    async def scan_events(self):
        if self.is_scanning:
            return
        
        self.is_scanning = True
        logger.info(f"\n🔍 [{datetime.now(WIB).strftime('%H:%M WIB')}] Scan TikTok (Apify)...")
        
        try:
            all_videos = []
            
            for hashtag in HASHTAGS:
                logger.info(f"📱 Scraping #{hashtag}...")
                videos = await scrape_tiktok_apify(hashtag)
                all_videos.extend(videos)
                await asyncio.sleep(2)
            
            logger.info(f"📊 Total video: {len(all_videos)}")
            
            kirim_count = 0
            
            for video in all_videos:
                video_url = video.get("url", "")
                
                if video_url in sent_videos:
                    continue
                
                keywords = cek_keyword(video["caption"])
                
                if keywords:
                    await self.send_to_discord(video, keywords)
                    sent_videos.add(video_url)
                    save_sent(sent_videos)
                    kirim_count += 1
                    await asyncio.sleep(1)
            
            logger.info(f"✅ Total dikirim: {kirim_count}")
            
        except Exception as e:
            logger.error(f"❌ Error scan: {e}")
        finally:
            self.is_scanning = False

    async def send_to_discord(self, video, keywords):
        channel = self.get_channel(self.channel_id)
        if not channel:
            logger.error("❌ Channel tidak ditemukan!")
            return
        
        embed = discord.Embed(
            title="📱 Konten TikTok Relevan!",
            description=f"**Caption:**\n{video['caption'][:500]}",
            color=discord.Color.blue(),
        )
        
        embed.add_field(name="👤 Author", value=f"@{video['author']}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{video['hashtag']}", inline=True)
        embed.add_field(name="🔑 Keywords", value=", ".join(keywords), inline=False)
        
        if video.get("url"):
            embed.add_field(name="🔗 Link TikTok", value=f"[Klik di sini]({video['url']})", inline=False)
        
        embed.set_footer(text=f"Ditemukan: {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}")
        
        try:
            await channel.send(embed=embed)
            logger.info(f"📤 Terkirim: {video['caption'][:50]}")
        except Exception as e:
            logger.error(f"❌ Gagal kirim: {e}")


# ============================================================
# MAIN
# ============================================================

bot = TikTokBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
