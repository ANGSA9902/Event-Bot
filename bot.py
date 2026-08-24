import discord
import asyncio
import requests
import json
import os
import logging
from datetime import datetime, timedelta
import pytz
import re
from discord.ui import Button, View

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
# SIMPAN VIDEO
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
# CEK HADIAH (ROBUX, UANG, GIVEAWAY)
# ============================================================

def has_prize(caption):
    """Cek apakah caption mengandung hadiah."""
    caption_lower = caption.lower()
    
    prize_keywords = [
        "robux", "hadiah", "giveaway", "prize", "uang", 
        "diamond", "gift", "reward", "free", "gratis",
        "menang", "winner", "chance", "kesempatan"
    ]
    
    for kw in prize_keywords:
        if kw in caption_lower:
            return True
    
    return False

# ============================================================
# CEK APAKAH CAPTION EVENT + HADIAH
# ============================================================

def is_event_with_prize(caption, keyword):
    """Cek apakah caption event dan ada hadiah, serta sesuai keyword."""
    caption_lower = caption.lower()
    keyword_lower = keyword.lower()
    
    # 1. Cek keyword
    if keyword_lower not in caption_lower:
        return False
    
    # 2. Cek event
    event_keywords = [
        "event", "giveaway", "lomba", "competition", 
        "fashion show", "battle", "challenge", "anomali"
    ]
    
    is_event = False
    for ek in event_keywords:
        if ek in caption_lower:
            is_event = True
            break
    
    if not is_event:
        return False
    
    # 3. Cek hadiah
    if not has_prize(caption):
        return False
    
    return True

# ============================================================
# SCRAPE TIKTOK PAKAI APIFY (DENGAN GAMBAR)
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
                    
                    # ── AMBIL GAMBAR ──
                    image_url = (
                        item.get("videoCoverUrl", "") or 
                        item.get("cover", "") or 
                        item.get("thumbnail", "") or 
                        item.get("thumbnailUrl", "")
                    )
                    
                    if isinstance(item.get("author"), dict):
                        avatar_url = item.get("author", {}).get("avatar", "")
                    else:
                        avatar_url = ""
                    
                    if caption:
                        videos.append({
                            "caption": caption,
                            "author": author,
                            "hashtag": hashtag,
                            "url": video_url,
                            "create_time": create_time,
                            "image_url": image_url,
                            "avatar_url": avatar_url,
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
# VIEW / TOMBOL
# ============================================================

class EventView(View):
    def __init__(self, video_url):
        super().__init__()
        self.add_item(Button(
            label="🔗 Lihat di TikTok",
            url=video_url,
            style=discord.ButtonStyle.link
        ))

# ============================================================
# BUILD UI DENGAN GAMBAR
# ============================================================

def build_embed(video, keyword=None):
    caption = video.get("caption", "")
    author = video.get("author", "unknown")
    hashtag = video.get("hashtag", "")
    video_url = video.get("url", "")
    image_url = video.get("image_url", "")
    avatar_url = video.get("avatar_url", "")
    
    dates = extract_all_dates(caption)
    now = datetime.now(WIB)
    today = now.date()
    tomorrow = (now + timedelta(days=1)).date()
    
    has_today = any(d == today for d in dates)
    has_tomorrow = any(d == tomorrow for d in dates)
    
    if has_today:
        color = 0xFF6B6B
        status = "🔥 HARI INI"
        icon = "🔴"
    elif has_tomorrow:
        color = 0xFFD93D
        status = "⏰ BESOK"
        icon = "🟡"
    else:
        color = 0x6BCBFF
        status = "📅 MENDATANG"
        icon = "🔵"
    
    embed = discord.Embed(
        title=f"✦ {icon} {status}",
        description=f"```\n{caption[:500].strip()}\n```",
        color=color,
        timestamp=datetime.now(WIB)
    )
    
    embed.set_author(
        name=f"@{author}",
        icon_url=avatar_url or "https://cdn.discordapp.com/emojis/1298439013180113058.png"
    )
    
    embed.add_field(
        name="🏷️ Hashtag",
        value=f"#{hashtag}",
        inline=False
    )
    
    # ── TAMBAH STATUS HADIAH ──
    if has_prize(caption):
        embed.add_field(
            name="🎁 Hadiah",
            value="✅ Ada hadiah! (Robux/Uang/Giveaway)",
            inline=False
        )
    
    if dates:
        date_lines = []
        for date in sorted(dates):
            if date == today:
                date_lines.append(f"`•` {date.strftime('%d %B %Y')} → **Hari ini**")
            elif date == tomorrow:
                date_lines.append(f"`•` {date.strftime('%d %B %Y')} → **Besok**")
            else:
                selisih = (date - today).days
                date_lines.append(f"`•` {date.strftime('%d %B %Y')} → {selisih} hari lagi")
        
        embed.add_field(
            name="📅 Tanggal Event",
            value="\n".join(date_lines),
            inline=False
        )
    
    # ── TAMPILKAN GAMBAR COVER ──
    if image_url:
        embed.set_image(url=image_url)
    
    embed.set_thumbnail(
        url="https://cdn.discordapp.com/emojis/1298439013180113058.png"
    )
    
    embed.set_footer(
        text=f"• {datetime.now(WIB).strftime('%d %b %Y %H:%M')} WIB",
        icon_url="https://cdn.discordapp.com/emojis/1298439013180113058.png"
    )
    
    return embed, EventView(video_url)

# ============================================================
# COMMAND HANDLER
# ============================================================

def is_command(message):
    if not message.guild:
        return False
    
    bot_mention = f"<@{bot.user.id}>"
    bot_mention_alt = f"<@!{bot.user.id}>"
    
    if bot_mention in message.content or bot_mention_alt in message.content:
        keywords = ["event", "ada event", "cari event", "event apa", "anomali"]
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
    
    for word in ["event", "ada", "cari", "apa", "hari", "ini", "besok", "yang", "dengan", "hadiah"]:
        content = content.replace(word, "")
    
    content = content.strip()
    
    if not content:
        return "anomali"
    
    return content

# ============================================================
# BOT UTAMA
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    logger.info(f"✅ Bot {bot.user} login!")
    
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="✦ TikTok Event Finder (GACOR!)",
            description=(
                "🔍 Cari event Roblox **anomali** dengan **HADIAH**!\n\n"
                "─── Cara Pakai ───\n"
                f"`@{bot.user.name} anomali`\n"
                "`@bot cari event anomali`\n"
                "`/event anomali`\n\n"
                "─── Command ───\n"
                "`!purge 10` → hapus 10 pesan\n"
                "`!purgebot 10` → hapus pesan bot\n"
                "`!purgeuser @user 10` → hapus pesan user\n\n"
                "─── Keyword ───\n"
                "`anomali` → khusus cari event anomali + hadiah"
            ),
            color=0x6BCBFF
        )
        embed.set_footer(text="✦ Made with ❤️ • Event Finder v4")
        await channel.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # ── !PURGE ──
    if message.content.startswith('!purge'):
        if not message.author.guild_permissions.manage_messages:
            await message.reply("❌ Kamu tidak punya izin `Manage Messages`!")
            return
        
        try:
            parts = message.content.split()
            amount = int(parts[1]) if len(parts) > 1 else 10
            if amount < 1 or amount > 100:
                await message.reply("❌ Jumlah harus 1-100!")
                return
            
            deleted = await message.channel.purge(limit=amount + 1)
            msg = await message.channel.send(f"✅ {len(deleted)-1} pesan dihapus!")
            await asyncio.sleep(3)
            await msg.delete()
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
        return
    
    # ── !PURGEBOT ──
    if message.content.startswith('!purgebot'):
        if not message.author.guild_permissions.manage_messages:
            await message.reply("❌ Kamu tidak punya izin `Manage Messages`!")
            return
        
        try:
            parts = message.content.split()
            amount = int(parts[1]) if len(parts) > 1 else 10
            if amount < 1 or amount > 100:
                await message.reply("❌ Jumlah harus 1-100!")
                return
            
            def check(msg):
                return msg.author.bot
            
            deleted = await message.channel.purge(limit=amount + 1, check=check)
            msg = await message.channel.send(f"✅ {len(deleted)-1} pesan bot dihapus!")
            await asyncio.sleep(3)
            await msg.delete()
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
        return
    
    # ── !PURGEUSER ──
    if message.content.startswith('!purgeuser'):
        if not message.author.guild_permissions.manage_messages:
            await message.reply("❌ Kamu tidak punya izin `Manage Messages`!")
            return
        
        try:
            parts = message.content.split()
            if len(parts) < 3:
                await message.reply("❌ Format: !purgeuser @user 10")
                return
            
            mention = parts[1]
            user_id = int(re.sub(r'[<@!>]', '', mention))
            user = await bot.fetch_user(user_id)
            amount = int(parts[2]) if len(parts) > 2 else 10
            
            if amount < 1 or amount > 100:
                await message.reply("❌ Jumlah harus 1-100!")
                return
            
            def check(msg):
                return msg.author == user
            
            deleted = await message.channel.purge(limit=amount + 1, check=check)
            msg = await message.channel.send(f"✅ {len(deleted)-1} pesan dari {user.mention} dihapus!")
            await asyncio.sleep(3)
            await msg.delete()
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
        return
    
    # ── EVENT COMMAND ──
    if is_command(message):
        await handle_event_command(message)

async def handle_event_command(message):
    keyword = extract_event_keyword(message)
    
    loading_embed = discord.Embed(
        title="✦ 🔍 Mencari...",
        description=f"Event **{keyword}** dengan hadiah di TikTok",
        color=0x6BCBFF
    )
    loading_msg = await message.reply(embed=loading_embed)
    
    # ── SCRAPE ──
    all_videos = []
    for hashtag in HASHTAGS:
        videos = await scrape_tiktok_apify(hashtag)
        all_videos.extend(videos)
        await asyncio.sleep(1)
    
    await loading_msg.delete()
    
    if not all_videos:
        embed = discord.Embed(
            title="✦ ❌ Tidak Ada Video",
            description="Tidak ada video dari TikTok.",
            color=0xFF6B6B
        )
        await message.reply(embed=embed)
        return
    
    # ── FILTER EVENT + HADIAH ──
    keyword_lower = keyword.lower()
    event_videos = []
    
    for video in all_videos:
        caption = video.get("caption", "")
        
        # Filter: event + hadiah + keyword
        if is_event_with_prize(caption, keyword_lower):
            event_videos.append(video)
    
    if not event_videos:
        embed = discord.Embed(
            title="✦ ❌ Tidak Ada Event dengan Hadiah",
            description=(
                f"Tidak ada event **{keyword}** yang ada **HADIAH**.\n\n"
                "─── Tips ───\n"
                "• Coba keyword lain\n"
                "• Cek langsung di TikTok\n\n"
                "─── Hashtag ───\n"
                f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}"
            ),
            color=0xFF6B6B
        )
        await message.reply(embed=embed)
        return
    
    result_embed = discord.Embed(
        title=f"✦ ✅ {len(event_videos)} Event dengan Hadiah Ditemukan",
        description=f"Keyword: **{keyword}**",
        color=0x6BCBFF
    )
    await message.reply(embed=result_embed)
    
    for video in event_videos[:5]:
        embed, view = build_embed(video, keyword)
        await message.channel.send(embed=embed, view=view)
        await asyncio.sleep(0.5)

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
