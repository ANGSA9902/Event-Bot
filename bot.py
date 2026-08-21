import discord
import asyncio
import requests
import json
import os
from datetime import datetime
import pytz

# ============================================================
# KONFIGURASI
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

HASHTAGS = [
    "FashionShowRoblox",
    "RobloxEvent",
    "RobloxIndonesia",
    "RobloxFyp",
    "RobloxAnomali",
]

# Keyword yang dicari di caption
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
# SCRAPE TIKTOK
# ============================================================

async def scrape_tiktok(hashtag):
    """Ambil video dari TikTok hashtag."""
    videos = []
    
    try:
        url = "https://www.tiktok.com/api/challenge/item_list/"
        params = {
            "aid": "1988",
            "challengeName": hashtag,
            "count": 5,
            "cursor": "0",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json",
        }
        
        response = await asyncio.to_thread(
            requests.get, url, params=params, headers=headers, timeout=15
        )
        
        data = response.json()
        items = data.get("itemList", [])
        
        for item in items:
            caption = item.get("desc", "")
            author = item.get("author", {}).get("uniqueId", "unknown")
            video_id = item.get("id", "")
            video_url = f"https://www.tiktok.com/@{author}/video/{video_id}"
            
            if caption:
                videos.append({
                    "caption": caption,
                    "author": author,
                    "hashtag": hashtag,
                    "url": video_url,
                })
                
        print(f"✅ #{hashtag}: {len(videos)} video")
        
    except Exception as e:
        print(f"❌ Error #{hashtag}: {e}")
    
    return videos


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
        print(f"✅ Bot {self.user} login!")
        
        channel = self.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 TikTok Monitor ONLINE!",
                description="Langsung kirim konten TikTok ke Discord!\n\n"
                           f"📱 Hashtags:\n"
                           f"{chr(10).join(f'• #{tag}' for tag in HASHTAGS)}\n\n"
                           f"🔑 Keywords:\n"
                           f"{', '.join(KEYWORDS)}",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        
        # Mulai scan
        await self.scan_events()
        
        # Loop scan setiap 6 jam
        while True:
            await asyncio.sleep(21600)  # 21600 detik = 6 jam
            await self.scan_events()

    async def scan_events(self):
        if self.is_scanning:
            return
        
        self.is_scanning = True
        print(f"\n🔍 [{datetime.now(WIB).strftime('%H:%M WIB')}] Scan TikTok...")
        
        try:
            all_videos = []
            
            for hashtag in HASHTAGS:
                print(f"📱 Scraping #{hashtag}...")
                videos = await scrape_tiktok(hashtag)
                all_videos.extend(videos)
                await asyncio.sleep(2)
            
            print(f"📊 Total video: {len(all_videos)}")
            
            kirim_count = 0
            
            for video in all_videos:
                video_url = video.get("url", "")
                
                # Skip yang sudah dikirim
                if video_url in sent_videos:
                    continue
                
                # Cek keyword
                keywords = cek_keyword(video["caption"])
                
                # Kirim jika ada minimal 1 keyword
                if keywords:
                    await self.send_to_discord(video, keywords)
                    sent_videos.add(video_url)
                    save_sent(sent_videos)
                    kirim_count += 1
                    await asyncio.sleep(1)
            
            print(f"✅ Total dikirim: {kirim_count}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            self.is_scanning = False

    async def send_to_discord(self, video, keywords):
        channel = self.get_channel(self.channel_id)
        if not channel:
            print("❌ Channel tidak ditemukan!")
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
            print(f"📤 Terkirim: {video['caption'][:50]}")
        except Exception as e:
            print(f"❌ Gagal kirim: {e}")


# ============================================================
# MAIN
# ============================================================

bot = TikTokBot()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
