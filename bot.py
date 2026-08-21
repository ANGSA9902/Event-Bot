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
# DETEKSI TANGGAL
# ============================================================

def extract_all_dates(caption):
    """Ambil semua tanggal yang disebutkan di caption."""
    now = datetime.now(WIB)
    today = now.date()
    
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
        dates_found.append(today + timedelta(days=1))
    
    return dates_found


def get_upcoming_dates(caption):
    """Ambil tanggal event yang HARI INI atau MENDATANG."""
    all_dates = extract_all_dates(caption)
    now = datetime.now(WIB)
    today = now.date()
    
    upcoming_dates = []
    for date in all_dates:
        if date >= today:
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
# COMMAND HANDLER
# ============================================================

def is_command(message):
    """Cek apakah pesan adalah perintah untuk bot."""
    if not message.guild:
        return False
    
    # Cek bot mention
    bot_mention = f"<@{bot.user.id}>"
    bot_mention_alt = f"<@!{bot.user.id}>"
    
    content = message.content.lower()
    
    # Cek apakah mention bot
    if bot_mention in message.content or bot_mention_alt in message.content:
        # Cek kata kunci event
        keywords = ["event", "ada event", "cari event", "event apa", "event roblox"]
        for kw in keywords:
            if kw in content:
                return True
    
    # Cek command /event
    if message.content.startswith("/event") or message.content.startswith("!event"):
        return True
    
    return False

def extract_event_keyword(message):
    """Ambil keyword event dari pesan."""
    content = message.content.lower()
    
    # Hapus mention bot
    bot_mention = f"<@{bot.user.id}>"
    bot_mention_alt = f"<@!{bot.user.id}>"
    content = content.replace(bot_mention, "").replace(bot_mention_alt, "")
    content = content.replace("/event", "").replace("!event", "")
    content = content.replace("event", "").replace("ada", "").replace("cari", "").replace("apa", "")
    content = content.strip()
    
    if not content:
        return None
    
    return content

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
        # Simpan proses pencarian per user
        self.user_search = {}

    async def on_ready(self):
        logger.info(f"✅ Bot {self.user} login!")
        
        if not APIFY_TOKEN:
            logger.warning("⚠️ APIFY_TOKEN belum diisi!")
        
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 TikTok Event Finder ONLINE!",
                description=f"📌 **Cara Pakai:**\n"
                           f"1. Mention bot + kata kunci event\n"
                           f"   Contoh: `@{self.user.name} event anomali`\n\n"
                           f"2. Atau ketik `/event [keyword]`\n"
                           f"   Contoh: `/event fashion`\n\n"
                           f"🔍 Bot akan cari event di TikTok berdasarkan keyword!",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        # Cek apakah ini command event
        if is_command(message):
            await self.handle_event_command(message)

    async def handle_event_command(self, message):
        """Handle perintah cari event."""
        keyword = extract_event_keyword(message)
        
        if not keyword:
            embed = discord.Embed(
                title="❓ Event apa yang mau dicari?",
                description=f"Contoh: `@{self.user.name} event anomali`\n"
                           f"Atau: `/event fashion`\n\n"
                           f"Keyword yang tersedia:\n"
                           f"• anomali\n"
                           f"• roblox\n"
                           f"• fashion\n"
                           f"• kalcer\n"
                           f"• dll (bebas)",
                color=discord.Color.orange(),
            )
            await message.reply(embed=embed)
            return
        
        # Cari event berdasarkan keyword
        await message.reply(f"🔍 Mencari event **{keyword}** di TikTok...")
        
        # Cari di hashtags
        all_videos = []
        for hashtag in HASHTAGS:
            logger.info(f"📱 Scraping #{hashtag} untuk keyword '{keyword}'...")
            videos = await scrape_tiktok_apify(hashtag)
            all_videos.extend(videos)
            await asyncio.sleep(1)
        
        # Filter berdasarkan keyword
        keyword_lower = keyword.lower()
        filtered_videos = []
        
        for video in all_videos:
            caption = video.get("caption", "").lower()
            
            # Cek apakah keyword ada di caption atau hashtag
            if keyword_lower in caption or keyword_lower in video.get("hashtag", "").lower():
                # Cek tanggal
                upcoming, _ = get_upcoming_dates(video.get("caption", ""))
                if upcoming:
                    filtered_videos.append(video)
        
        if not filtered_videos:
            embed = discord.Embed(
                title=f"❌ Tidak ada event **{keyword}** yang ditemukan",
                description=f"Coba keyword lain, atau cek TikTok langsung.\n\n"
                           f"🔍 Hashtag yang dicari:\n"
                           f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}",
                color=discord.Color.red(),
            )
            await message.reply(embed=embed)
            return
        
        # Kirim hasil
        await message.reply(f"✅ Ditemukan {len(filtered_videos)} event untuk **{keyword}**!")
        
        for video in filtered_videos[:5]:  # Max 5 hasil
            upcoming_dates, all_dates = get_upcoming_dates(video.get("caption", ""))
            await self.send_event_embed(message.channel, video, upcoming_dates, all_dates)
            await asyncio.sleep(1)

    async def send_event_embed(self, channel, video, upcoming_dates, all_dates):
        """Kirim embed event ke Discord."""
        caption = video.get("caption", "")
        
        now = datetime.now(WIB)
        today = now.date()
        tomorrow = (now + timedelta(days=1)).date()
        lusa = (now + timedelta(days=2)).date()
        
        date_lines = []
        for date in sorted(all_dates):
            if date == today:
                date_lines.append(f"🔴 {date.strftime('%d %B %Y')} - HARI INI!")
            elif date == tomorrow:
                date_lines.append(f"🟡 {date.strftime('%d %B %Y')} - BESOK!")
            elif date == lusa:
                date_lines.append(f"🟢 {date.strftime('%d %B %Y')} - LUSA!")
            elif date > today:
                date_lines.append(f"📅 {date.strftime('%d %B %Y')}")
            else:
                date_lines.append(f"⏰ {date.strftime('%d %B %Y')} - LEWAT")
        
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
        
        dates_str = "\n".join(date_lines) if date_lines else "Tidak ada tanggal"
        status_str = "\n".join(event_status) if event_status else "Event akan datang"
        
        embed = discord.Embed(
            title="📱 EVENT TERDETEKSI!",
            description=f"**Caption:**\n{caption[:800]}",
            color=discord.Color.blue(),
        )
        
        embed.add_field(name="👤 Author", value=f"@{video['author']}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{video['hashtag']}", inline=True)
        embed.add_field(name="📅 Tanggal", value=dates_str, inline=False)
        embed.add_field(name="📌 Status", value=status_str, inline=False)
        
        if video.get("url"):
            embed.add_field(name="🔗 Link", value=f"[Klik di sini]({video['url']})", inline=False)
        
        embed.set_footer(text=f"Ditemukan: {datetime.now(WIB).strftime('%d %b %Y %H:%M WIB')}")
        
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f"❌ Gagal kirim embed: {e}")


# ============================================================
# MAIN
# ============================================================

bot = TikTokBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
