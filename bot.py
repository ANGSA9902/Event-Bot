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

# ============================================================
# SCRAPE TIKTOK PAKAI APIFY
# ============================================================

async def scrape_tiktok_apify(hashtag):
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
# BUILD UI CLAUDE-STYLE (TANPA AI)
# ============================================================

def build_claude_embed(video, keyword=None):
    """Buat embed dengan gaya Claude AI - minimalis & elegant."""
    caption = video.get("caption", "")
    author = video.get("author", "unknown")
    hashtag = video.get("hashtag", "")
    video_url = video.get("url", "")
    
    # ── DETEKSI STATUS ──
    dates = extract_all_dates(caption)
    now = datetime.now(WIB)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    
    has_today = any(d == today for d in dates)
    has_tomorrow = any(d == tomorrow for d in dates)
    
    if has_today:
        color = discord.Color.red()
        status_emoji = "●"
        status_text = "HARI INI"
    elif has_tomorrow:
        color = discord.Color.gold()
        status_emoji = "●"
        status_text = "BESOK"
    else:
        color = discord.Color.blue()
        status_emoji = "●"
        status_text = "MENDATANG"
    
    # ── BUILD ──
    embed = discord.Embed(
        title=f"{status_emoji}  Event {status_text}",
        description=f"```{caption[:600].strip()}```",
        color=color
    )
    
    # ── INFO ──
    embed.add_field(
        name="",
        value=f"**@{author}**  •  #{hashtag}",
        inline=False
    )
    
    # ── TANGGAL ──
    if dates:
        date_lines = []
        for date in sorted(dates):
            if date == today:
                date_lines.append(f"● {date.strftime('%d %B %Y')} — Hari ini")
            elif date == tomorrow:
                date_lines.append(f"● {date.strftime('%d %B %Y')} — Besok")
            else:
                date_lines.append(f"● {date.strftime('%d %B %Y')}")
        
        embed.add_field(
            name="",
            value="\n".join(date_lines),
            inline=False
        )
    
    # ── LINK ──
    if video_url:
        embed.add_field(
            name="",
            value=f"→ [Lihat di TikTok]({video_url})",
            inline=False
        )
    
    # ── FOOTER ──
    embed.set_footer(
        text=f"{datetime.now(WIB).strftime('%d %b %Y • %H:%M')} WIB",
        icon_url="https://cdn.discordapp.com/emojis/1298439013180113058.png"
    )
    
    return embed

# ============================================================
# COMMAND HANDLER
# ============================================================

def is_command(message):
    if not message.guild:
        return False
    
    bot_mention = f"<@{bot.user.id}>"
    bot_mention_alt = f"<@!{bot.user.id}>"
    
    if bot_mention in message.content or bot_mention_alt in message.content:
        keywords = ["event", "ada event", "cari event", "event apa"]
        for kw in keywords:
            if kw in message.content.lower():
                return True
    
    if message.content.startswith("/event") or message.content.startswith("!event"):
        return True
    
    return False

def extract_event_keyword(message):
    content = message.content.lower()
    
    bot_mention = f"<@{bot.user.id}>"
    bot_mention_alt = f"<@!{bot.user.id}>"
    content = content.replace(bot_mention, "").replace(bot_mention_alt, "")
    content = content.replace("/event", "").replace("!event", "")
    
    for word in ["event", "ada", "cari", "apa"]:
        content = content.replace(word, "")
    
    content = content.strip()
    
    if not content:
        return "roblox"
    
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

    async def on_ready(self):
        logger.info(f"✅ Bot {self.user} login!")
        
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="TikTok Event Finder",
                description=(
                    "Saya siap membantu menemukan event Roblox di TikTok.\n\n"
                    "── Cara pakai ──\n"
                    f"`@{self.user.name} event anomali`\n"
                    "`/event fashion`\n\n"
                    "── Keyword ──\n"
                    "`anomali` • `roblox` • `fashion` • `kalcer`"
                ),
                color=discord.Color.blue()
            )
            embed.set_footer(text="Made with ❤️")
            await channel.send(embed=embed)

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        if is_command(message):
            await self.handle_event_command(message)

    async def handle_event_command(self, message):
        keyword = extract_event_keyword(message)
        
        # ── WAIT ──
        wait_embed = discord.Embed(
            title="⋯  Mencari",
            description=f"Event **{keyword}**",
            color=discord.Color.blue()
        )
        wait_msg = await message.reply(embed=wait_embed)
        
        # ── SCRAPE ──
        all_videos = []
        for hashtag in HASHTAGS:
            videos = await scrape_tiktok_apify(hashtag)
            all_videos.extend(videos)
            await asyncio.sleep(1)
        
        # ── FILTER ──
        keyword_lower = keyword.lower()
        filtered_videos = []
        
        for video in all_videos:
            caption = video.get("caption", "").lower()
            if keyword_lower in caption or keyword_lower in video.get("hashtag", "").lower():
                dates = extract_all_dates(video.get("caption", ""))
                if dates:
                    filtered_videos.append(video)
        
        await wait_msg.delete()
        
        if not filtered_videos:
            embed = discord.Embed(
                title="Tidak ditemukan",
                description=f"Tidak ada event untuk **{keyword}**",
                color=discord.Color.red()
            )
            await message.reply(embed=embed)
            return
        
        # ── KIRIM ──
        result_embed = discord.Embed(
            title=f"{len(filtered_videos)} event ditemukan",
            description=f"Keyword: **{keyword}**",
            color=discord.Color.green()
        )
        await message.reply(embed=result_embed)
        
        for video in filtered_videos[:5]:
            embed = build_claude_embed(video, keyword)
            await message.channel.send(embed=embed)
            await asyncio.sleep(0.5)


# ============================================================
# MAIN
# ============================================================

bot = TikTokBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
