import discord
import asyncio
import requests
import json
import os
import logging
from datetime import datetime
import pytz
from playwright.async_api import async_playwright

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
# SCRAPE TIKTOK (DENGAN PLAYWRIGHT)
# ============================================================

async def scrape_tiktok(hashtag):
    """Ambil video dari TikTok hashtag menggunakan Playwright."""
    videos = []
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Buka halaman hashtag TikTok
            url = f"https://www.tiktok.com/tag/{hashtag}"
            logger.info(f"Membuka {url}")
            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(3000)
            
            # Scroll untuk load lebih banyak video
            for _ in range(3):
                await page.mouse.wheel(0, 1000)
                await page.wait_for_timeout(1000)
            
            # Ambil data video
            video_elements = await page.query_selector_all('div[data-e2e="user-post-item"]')
            
            for element in video_elements[:5]:  # Ambil 5 video pertama
                try:
                    caption_elem = await element.query_selector('div[data-e2e="video-desc"]')
                    caption = await caption_elem.text_content() if caption_elem else ""
                    
                    link_elem = await element.query_selector('a[href*="/video/"]')
                    if link_elem:
                        video_url = await link_elem.get_attribute('href')
                        video_url = f"https://www.tiktok.com{video_url}"
                    else:
                        continue
                    
                    if caption:
                        videos.append({
                            "caption": caption.strip(),
                            "author": hashtag,
                            "hashtag": hashtag,
                            "url": video_url,
                        })
                except Exception as e:
                    logger.warning(f"Error parsing video: {e}")
            
            await browser.close()
            logger.info(f"✅ #{hashtag}: {len(videos)} video")
            
    except Exception as e:
        logger.error(f"❌ Error #{hashtag}: {e}")
    
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
        logger.info(f"✅ Bot {self.user} login!")
        
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
        logger.info(f"\n🔍 [{datetime.now(WIB).strftime('%H:%M WIB')}] Scan TikTok...")
        
        try:
            all_videos = []
            
            for hashtag in HASHTAGS:
                logger.info(f"📱 Scraping #{hashtag}...")
                videos = await scrape_tiktok(hashtag)
                all_videos.extend(videos)
                await asyncio.sleep(2)
            
            logger.info(f"📊 Total video: {len(all_videos)}")
            
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
