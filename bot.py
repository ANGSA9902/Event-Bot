import discord
import asyncio
from google import genai
import json
import os
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import pytz

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

ACTOR_ID = "clockworks/tiktok-scraper"

HASHTAGS = [
    "RobloxEvent",
    "FashionShowRoblox",
    "GiveawayRoblox",
    "RobuxGiveaway",
    "AvatarKalcer",
    "EventRoblox",
    "RobloxIndonesia"
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
    - fashion_show, avatar_kalcer, giveaway, competition, community_event, event_kemerdekaan
    Format output (JSON):
    {{
        "is_event": true/false,
        "title": "...",
        "category": "...",
        "description": "...",
        "prize": "...",
        "deadline": "...",
        "reason": "..."
    }}
    Jawab HANYA dengan JSON.
    """
    try:
        response = client_gemini.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        result_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
    except Exception as e:
        print(f"Error AI: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}

def get_tiktok_events():
    if not APIFY_TOKEN:
        print("❌ APIFY_TOKEN tidak ditemukan!")
        return []

    all_videos = []

    for hashtag in HASHTAGS:
        try:
            print(f"Mencari video dengan #{hashtag}...")
            url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
            payload = {
                "hashtags": [hashtag],
                "resultsPerPage": 5,
                "maxResults": 5
            }
            headers = {"Content-Type": "application/json"}
            resp = requests.post(url, json=payload, headers=headers)
            run_data = resp.json()
            run_id = run_data.get("data", {}).get("id")
            if not run_id:
                print(f"Gagal start run untuk #{hashtag}")
                continue

            dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}"

            items = []
            for _ in range(20):
                await asyncio.sleep(5)
                try:
                    items = requests.get(dataset_url).json()
                    if items:
                        break
                except Exception:
                    pass

            for item in items:
                caption = item.get("text") or item.get("desc") or ""
                author = item.get("authorMeta", {}).get("name", "unknown")
                if caption:
                    all_videos.append({
                        "caption": caption,
                        "author": author,
                        "hashtag": hashtag
                    })

        except Exception as e:
            print(f"Error #{hashtag}: {e}")

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

        test_event = {
            "is_event": True,
            "title": "Test Event Fashion Show",
            "category": "fashion_show",
            "description": "Event percobaan untuk memastikan Discord berfungsi.",
            "prize": "1000 Robux",
            "deadline": "Besok 19:00 WIB",
            "reason": "Test"
        }
        await self.send_event_to_dashboard(test_event, "test_user", "Test")

        await self.cek_dan_kirim_event()

    async def send_event_to_dashboard(self, event_info, source_author="unknown", source_hashtag=""):
        channel = self.get_channel(self.dashboard_channel_id)
        if not channel:
            print("❌ Channel tidak ditemukan!")
            return False

        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=event_info.get('description', ''),
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
        print("\n[Bot] MULAI CEK EVENT TIKTOK...\n")
        videos = get_tiktok_events()

        if not videos:
            print("Tidak ada video ditemukan.")
            return

        event_count = 0
        for video in videos:
            print(f"\nMenganalisa dari @{video['author']}...")
            result = ai_filter_event(video['caption'])
            if result.get('is_event'):
                print(f"EVENT: {result.get('title')}")
                await self.send_event_to_dashboard(result, video['author'], video['hashtag'])
                event_count += 1
            else:
                print("Bukan event")
        print(f"\nTotal event dikirim: {event_count}")

WIB = pytz.timezone('Asia/Jakarta')
scheduler = AsyncIOScheduler(timezone=WIB)

async def update_pagi():
    print(f"\nUPDATE PAGI - {datetime.now(WIB).strftime('%H:%M WIB')}")
    bot = DashboardBot()
    await bot.cek_dan_kirim_event()

scheduler.add_job(update_pagi, 'cron', hour=6, minute=0, id='update_pagi')

async def main():
    bot = DashboardBot()
    scheduler.start()
    print("Scheduler aktif! Bot update jam 6 pagi WIB.")
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
