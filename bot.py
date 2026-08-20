import discord
import asyncio
from google import genai
import json
from TikTokApi import TikTokApi
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

HASHTAGS = [
    "RobloxEvent",
    "FashionShowRoblox",
    "GiveawayRoblox",
    "RobloxGiveaway",
    "AvatarKalcer",
    "EventRoblox",
    "RobloxIndonesia",
    "KontesAvatar",
    "LombaRoblox",
    "RobuxGiveaway",
    "EventMerahPutih",
    "EventKemerdekaan",
    "GiveawayRobux",
    "EventRobloxIndonesia"
]

client_gemini = genai.Client(api_key=GEMINI_API_KEY)

def ai_filter_event(text):
    prompt = f"""
    Kamu adalah AI yang bertugas memfilter informasi event Roblox dari TikTok.
    
    Konten TikTok: "{text}"
    
    Tugas:
    1. Tentukan apakah konten ini adalah EVENT Roblox yang RELEVAN
    2. Event yang relevan: fashion show, giveaway Robux/uang, kontes avatar, turnamen, event komunitas, event merah putih, event kemerdekaan, avatar kalcer
    3. BUKAN event jika: promo biasa, jual beli, chating, spam, tidak jelas, video biasa
    
    Kategori event:
    - fashion_show (kontes outfit/style)
    - avatar_kalcer (avatar dengan gaya budaya/estetik)
    - giveaway (giveaway Robux/uang/item)
    - competition (turnamen/kompetisi)
    - community_event (meetup/gathering)
    - event_kemerdekaan (event merah putih/17 agustus)
    
    Format output (JSON):
    {{
        "is_event": true/false,
        "title": "Judul event jika is_event=true",
        "category": "kategori event",
        "description": "Deskripsi singkat event",
        "prize": "Hadiah (Robux/uang) jika ada",
        "deadline": "Deadline jika ada",
        "reason": "Alasan mengapa ini event relevan atau tidak"
    }}
    
    Jawab HANYA dengan JSON, tanpa teks lain.
    """
    
    try:
        response = client_gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        
        result_text = response.text
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        
        result = json.loads(result_text)
        return result
        
    except Exception as e:
        print(f"Error AI: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}

async def get_tiktok_events():
    api = TikTokApi()
    
    all_videos = []
    
    for hashtag in HASHTAGS:
        try:
            print(f"Mencari video dengan #{hashtag}...")
            
            async for video in api.hashtag(name=hashtag).videos(count=5):
                caption = video.as_dict.get("desc", "")
                author = video.as_dict.get("author", {}).get("uniqueId", "unknown")
                
                if caption:
                    all_videos.append({
                        "caption": caption,
                        "author": author,
                        "hashtag": hashtag
                    })
                    
        except Exception as e:
            print(f"Error mencari #{hashtag}: {e}")
    
    print(f"Total video ditemukan: {len(all_videos)}")
    return all_videos

intents = discord.Intents.default()
intents.message_content = True

class DashboardBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.dashboard_channel_id = DASHBOARD_CHANNEL_ID
    
    async def on_ready(self):
        print(f"Bot {self.user} berhasil login!")
        
        channel = self.get_channel(self.dashboard_channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 Bot Event Filter ONLINE!",
                description="Siap memantau event Roblox dari TikTok...",
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
        
        await self.cek_dan_kirim_event()
    
    async def send_event_to_dashboard(self, event_info, source_author="unknown", source_hashtag=""):
        channel = self.get_channel(self.dashboard_channel_id)
        
        if not channel:
            print("Channel dashboard tidak ditemukan!")
            return False
        
        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=event_info.get('description', 'Tidak ada deskripsi'),
            color=discord.Color.blue()
        )
        
        if event_info.get('category'):
            embed.add_field(name="📂 Kategori", value=event_info['category'], inline=True)
        
        if event_info.get('prize'):
            embed.add_field(name="💰 Hadiah", value=event_info['prize'], inline=True)
        
        if event_info.get('deadline'):
            embed.add_field(name="⏰ Deadline", value=event_info['deadline'], inline=True)
        
        embed.add_field(name="📱 Sumber", value=f"TikTok @{source_author}", inline=True)
        if source_hashtag:
            embed.add_field(name="🏷️ Hashtag", value=f"#{source_hashtag}", inline=True)
        
        embed.set_footer(text="✅ Event lolos filter AI")
        
        await channel.send(embed=embed)
        print(f"Event dikirim: {event_info.get('title')}")
        return True
    
    async def cek_dan_kirim_event(self):
        print("\nMULAI CEK EVENT TIKTOK...\n")
        
        videos = await get_tiktok_events()
        
        if not videos:
            print("Tidak ada video ditemukan.")
            return
        
        event_count = 0
        
        for video in videos:
            caption = video['caption']
            author = video['author']
            hashtag = video['hashtag']
            
            print(f"\nMenganalisa dari @{author}...")
            print(f"Caption: {caption[:80]}...")
            
            result = ai_filter_event(caption)
            
            if result.get('is_event'):
                print(f"EVENT: {result.get('title')}")
                await self.send_event_to_dashboard(result, author, hashtag)
                event_count += 1
            else:
                print("Bukan event")
        
        print(f"\nTotal event dikirim: {event_count}")

WIB = pytz.timezone('Asia/Jakarta')
scheduler = AsyncIOScheduler(timezone=WIB)

async def update_pagi():
    print("\n" + "="*50)
    print(f"UPDATE PAGI - {datetime.now(WIB).strftime('%H:%M WIB')}")
    print("="*50 + "\n")
    
    bot = DashboardBot()
    await bot.cek_dan_kirim_event()

scheduler.add_job(
    update_pagi,
    'cron',
    hour=6,
    minute=0,
    id='update_pagi'
)

async def main():
    bot = DashboardBot()
    
    scheduler.start()
    print("Scheduler aktif! Bot update jam 6 pagi WIB.")
    
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())