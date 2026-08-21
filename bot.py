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
# DETEKSI SEMUA TANGGAL DI CAPTION
# ============================================================

def extract_all_dates(caption):
    """Ambil semua tanggal yang disebutkan di caption."""
    now = datetime.now(WIB)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    
    dates_found = []
    
    patterns = [
        r'(\d{1,2})\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+(\d{4})',
        r'(\d{1,2})\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\s+(\d{4})',
        r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})',
        r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})',
    ]
    
    bulan_map = {
        'januari': 1, 'februari': 2, 'maret': 3, 'april': 4, 'mei': 5, 'juni': 6,
        'juli': 7, 'agustus': 8, 'september': 9, 'oktober': 10, 'november': 11, 'desember': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'mei': 5, 'jun': 6,
        'jul': 7, 'agu': 8, 'sep': 9, 'okt': 10, 'nov': 11, 'des': 12
    }
    
    for pattern in patterns:
        matches = re.findall(pattern, caption, re.IGNORECASE)
        for match in matches:
            try:
                if len(match) == 3:
                    if match[0].isdigit() and match[2].isdigit():
                        day = int(match[0])
                        
                        if match[1].lower() in bulan_map:
                            month = bulan_map[match[1].lower()]
                        elif match[1].isdigit():
                            month = int(match[1])
                        else:
                            continue
                        
                        year = int(match[2])
                        if year < 100:
                            year = 2000 + year
                        
                        try:
                            date_obj = datetime(year, month, day, tzinfo=WIB)
                            dates_found.append(date_obj.date())
                        except:
                            pass
            except:
                pass
    
    dates_found = list(set(dates_found))
    
    caption_lower = caption.lower()
    if "hari ini" in caption_lower or "today" in caption_lower:
        dates_found.append(today)
    if "besok" in caption_lower or "tomorrow" in caption_lower:
        dates_found.append(tomorrow)
    
    return dates_found


def get_upcoming_dates(caption):
    """
    Ambil tanggal event yang HARI INI atau LEBIH (masih akan datang).
    Skip tanggal yang sudah lewat (kemarin atau lebih).
    Return: (list tanggal akan datang, list semua tanggal)
    """
    all_dates = extract_all_dates(caption)
    
    now = datetime.now(WIB)
    today = now.date()
    
    upcoming_dates = []
    for date in all_dates:
        if date >= today:  # Hari ini atau lebih
            upcoming_dates.append(date)
    
    return upcoming_dates, all_dates

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
                           f"📅 Filter: Event HARI INI atau MENDATANG\n"
                           f"⏰ Skip event yang sudah LEWAT",
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
            skip_count = 0
            
            for video in all_videos:
                video_url = video.get("url", "")
                
                if video_url in sent_videos:
                    continue
                
                caption = video.get("caption", "")
                
                # Cek apakah ada event hari ini atau mendatang
                upcoming_dates, all_dates = get_upcoming_dates(caption)
                
                if upcoming_dates:
                    # Kirim dengan tanggal yang akan datang
                    await self.send_to_discord(video, upcoming_dates, all_dates)
                    sent_videos.add(video_url)
                    save_sent(sent_videos)
                    kirim_count += 1
                    await asyncio.sleep(1)
                else:
                    skip_count += 1
            
            logger.info(f"✅ Total dikirim: {kirim_count} | Skip (event sudah lewat): {skip_count}")
            
        except Exception as e:
            logger.error(f"❌ Error scan: {e}")
        finally:
            self.is_scanning = False

    async def send_to_discord(self, video, upcoming_dates, all_dates):
        channel = self.get_channel(self.channel_id)
        if not channel:
            logger.error("❌ Channel tidak ditemukan!")
            return
        
        caption = video.get("caption", "")
        
        # Tampilkan semua tanggal yang ditemukan
        now = datetime.now(WIB)
        today = now.date()
        tomorrow = (now + timedelta(days=1)).date()
        lusa = (now + timedelta(days=2)).date()
        
        date_lines = []
        
        # Tampilkan semua tanggal yang ditemukan
        for date in sorted(all_dates):
            if date == today:
                date_lines.append(f"🔴 {date.strftime('%d %B %Y')} - HARI INI!")
            elif date == tomorrow:
                date_lines.append(f"🟡 {date.strftime('%d %B %Y')} - BESOK!")
            elif date == lusa:
                date_lines.append(f"🟢 {date.strftime('%d %B %Y')} - LUSA!")
            elif date > today:
                date_lines.append(f"📅 {date.strftime('%d %B %Y')} - AKAN DATANG")
            else:
                date_lines.append(f"⏰ {date.strftime('%d %B %Y')} - SUDAH LEWAT")
        
        # Status event yang akan datang
        event_status = []
        for date in upcoming_dates:
            if date == today:
                event_status.append("🔴 BERLANGSUNG HARI INI!")
            elif date == tomorrow:
                event_status.append("🟡 BESOK!")
            elif date == lusa:
                event_status.append("🟢 LUSA!")
            else:
                selisih = (date - today).days
                event_status.append(f"📅 {selisih} HARI LAGI!")
        
        status_str = "\n".join(event_status) if event_status else "Event akan datang"
        
        dates_str = "\n".join(date_lines) if date_lines else "Tidak ada tanggal terdeteksi"
        
        embed = discord.Embed(
            title="📱 EVENT TERDETEKSI!",
            description=f"**Caption:**\n{caption[:800]}",
            color=discord.Color.blue(),
        )
        
        embed.add_field(name="👤 Author", value=f"@{video['author']}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{video['hashtag']}", inline=True)
        embed.add_field(name="📅 Tanggal Event", value=dates_str, inline=False)
        embed.add_field(name="📌 Status", value=status_str, inline=False)
        
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
