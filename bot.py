import discord
import asyncio
import requests
import json
import os
import logging
from datetime import datetime, timedelta
import pytz
import re

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
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

# HASHTAGS YANG DIPANTAU
HASHTAGS = [
    "RobloxFyp",
    "RobloxIndonesia",
    "RobloxAnomali",
    "FashionShowRoblox",
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
# CEK TANGGAL VIDEO (DARI UPLOAD TIME)
# ============================================================

def is_recent_video(create_time):
    """Cek apakah video diupload hari ini atau besok."""
    if not create_time:
        return False
    
    try:
        # Coba parse timestamp (bisa berupa integer atau string)
        if isinstance(create_time, (int, float)):
            video_date = datetime.fromtimestamp(create_time, tz=WIB)
        elif isinstance(create_time, str):
            # Coba format ISO
            if create_time.isdigit():
                video_date = datetime.fromtimestamp(int(create_time), tz=WIB)
            else:
                # Coba parse string date
                video_date = datetime.fromisoformat(create_time.replace('Z', '+00:00'))
                video_date = video_date.astimezone(WIB)
        else:
            return False
        
        now = datetime.now(WIB)
        
        # Cek apakah video dari hari ini atau besok
        if video_date.date() == now.date():
            return True
        if video_date.date() == (now + timedelta(days=1)).date():
            return True
        
        logger.info(f"📅 Skip video dari {video_date.strftime('%d/%m/%Y')}")
        return False
        
    except Exception as e:
        logger.warning(f"⚠️ Gagal parse tanggal: {e}")
        return False


def is_today_or_tomorrow_caption(caption):
    """Cek apakah caption mention tanggal hari ini atau besok (fallback)."""
    now = datetime.now(WIB)
    today_str = now.strftime("%d/%m/%Y")
    tomorrow_str = (now + timedelta(days=1)).strftime("%d/%m/%Y")
    
    date_patterns = [
        r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})',
        r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+(\d{4})',
        r'(\d{1,2})\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+(\d{4})',
        r'tanggal\s+(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})',
        r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})',
    ]
    
    caption_lower = caption.lower()
    
    for pattern in date_patterns:
        matches = re.findall(pattern, caption)
        for match in matches:
            try:
                if len(match) == 3:
                    if len(match[2]) == 2:
                        year = 2000 + int(match[2])
                    else:
                        year = int(match[2])
                    
                    day = int(match[0])
                    month = int(match[1])
                    
                    try:
                        date_obj = datetime(year, month, day, tzinfo=WIB)
                        date_str = date_obj.strftime("%d/%m/%Y")
                        
                        if date_str == today_str or date_str == tomorrow_str:
                            return True
                    except:
                        pass
            except:
                pass
    
    if "hari ini" in caption_lower or "today" in caption_lower:
        return True
    if "besok" in caption_lower or "tomorrow" in caption_lower:
        return True
    
    return False

# ============================================================
# SCRAPE TIKTOK PAKAI APIFY
# ============================================================

async def scrape_tiktok_apify(hashtag):
    """Scrape TikTok pake Apify API."""
    videos = []
    
    if not APIFY_TOKEN:
        logger.error("❌ APIFY_TOKEN tidak ditemukan!")
        return videos
    
    try:
        run_url = "https://api.apify.com/v2/acts/clockworks~tiktok-scraper/runs"
        params = {"token": APIFY_TOKEN}
        payload = {
            "searchQueries": [hashtag],
            "maxResults": 10,
            "resultsPerPage": 10,
            "shouldDownloadVideos": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadComments": False
        }
        
        response = await asyncio.to_thread(
            requests.post, run_url, params=params, json=payload, timeout=30
        )
        
        if response.status_code not in [200, 201]:
            logger.error(f"❌ Apify error: {response.status_code}")
            return videos
        
        run_data = response.json()
        
        if "data" in run_data and "id" in run_data["data"]:
            run_id = run_data["data"]["id"]
        elif "id" in run_data:
            run_id = run_data["id"]
        else:
            logger.error(f"❌ Gagal dapat run_id")
            return videos
        
        logger.info(f"🔄 Apify running... (ID: {run_id})")
        
        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
        max_wait = 45
        waited = 0
        
        while waited < max_wait:
            status_response = await asyncio.to_thread(
                requests.get, status_url, params={"token": APIFY_TOKEN}, timeout=10
            )
            status_data = status_response.json()
            status = status_data.get("data", {}).get("status")
            
            if status == "SUCCEEDED":
                dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items"
                dataset_response = await asyncio.to_thread(
                    requests.get, dataset_url, params={"token": APIFY_TOKEN, "limit": 10}, timeout=10
                )
                items = dataset_response.json()
                
                for item in items:
                    caption = item.get("text", "") or item.get("caption", "") or item.get("desc", "")
                    author = item.get("author", {}).get("uniqueId", "unknown") if isinstance(item.get("author"), dict) else item.get("author", "unknown")
                    video_url = item.get("webVideoUrl", "") or item.get("url", "")
                    create_time = item.get("createTime", "") or item.get("createdAt", "") or item.get("timestamp", "")
                    
                    if caption:
                        videos.append({
                            "caption": caption,
                            "author": author,
                            "hashtag": hashtag,
                            "url": video_url,
                            "create_time": create_time,
                        })
                
                logger.info(f"✅ #{hashtag}: {len(videos)} video dari Apify")
                break
                
            elif status in ["FAILED", "TIMED-OUT", "ABORTED"]:
                logger.error(f"❌ Apify run {status}")
                break
            
            await asyncio.sleep(3)
            waited += 3
        
    except Exception as e:
        logger.error(f"❌ Error Apify: {e}")
    
    return videos

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
            logger.warning("⚠️ APIFY_TOKEN belum diisi!")
        
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 TikTok Monitor ONLINE! (Apify)",
                description=f"📱 Hashtags:\n"
                           f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}\n\n"
                           f"📅 Filter: Hanya video HARI INI & BESOK (dari upload time)",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        
        await self.scan_events()
        
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
            skip_tanggal = 0
            skip_lama = 0
            
            for video in all_videos:
                video_url = video.get("url", "")
                
                if video_url in sent_videos:
                    continue
                
                # FILTER UTAMA: Cek tanggal upload video
                create_time = video.get("create_time", "")
                
                if not is_recent_video(create_time):
                    # Fallback: cek caption
                    caption = video.get("caption", "")
                    if not is_today_or_tomorrow_caption(caption):
                        skip_tanggal += 1
                        continue
                    else:
                        # Caption bilang hari ini, tapi upload time tidak mendukung
                        skip_lama += 1
                        continue
                
                # KIRIM VIDEO
                await self.send_to_discord(video)
                sent_videos.add(video_url)
                save_sent(sent_videos)
                kirim_count += 1
                await asyncio.sleep(1)
            
            logger.info(f"✅ Total dikirim: {kirim_count} | Skip (bukan hari ini/besok): {skip_tanggal} | Skip (video lama): {skip_lama}")
            
        except Exception as e:
            logger.error(f"❌ Error scan: {e}")
        finally:
            self.is_scanning = False

    async def send_to_discord(self, video):
        channel = self.get_channel(self.channel_id)
        if not channel:
            logger.error("❌ Channel tidak ditemukan!")
            return
        
        caption = video.get("caption", "")
        
        # Tandai kalau event hari ini
        now = datetime.now(WIB)
        today_str = now.strftime("%d/%m/%Y")
        tomorrow_str = (now + timedelta(days=1)).strftime("%d/%m/%Y")
        
        if today_str in caption or "hari ini" in caption.lower() or "today" in caption.lower():
            tag = "🔴 HARI INI!"
        elif tomorrow_str in caption or "besok" in caption.lower() or "tomorrow" in caption.lower():
            tag = "🟡 BESOK!"
        else:
            tag = "📅 CEK TANGGAL"
        
        embed = discord.Embed(
            title=f"📱 {tag}",
            description=f"**Caption:**\n{caption[:500]}",
            color=discord.Color.blue(),
        )
        
        embed.add_field(name="👤 Author", value=f"@{video['author']}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{video['hashtag']}", inline=True)
        
        if video.get("url"):
            embed.add_field(name="🔗 Link TikTok", value=f"[Klik di sini]({video['url']})", inline=False)
        
        embed.set_footer(text=f"Ditemukan: {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}")
        
        try:
            await channel.send(embed=embed)
            logger.info(f"📤 Terkirim: {caption[:50]}")
        except Exception as e:
            logger.error(f"❌ Gagal kirim: {e}")


# ============================================================
# MAIN
# ============================================================

bot = TikTokBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
