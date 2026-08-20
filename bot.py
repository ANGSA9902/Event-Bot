import discord
import asyncio
from google import genai
import json
import os
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
import pytz

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID = int(os.getenv("DASHBOARD_CHANNEL_ID"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

ACTOR_ID = "clockworks~tiktok-scraper"

HASHTAGS = [
    "RobloxEvent",
    "FashionShowRoblox",
    "GiveawayRoblox",
    "RobuxGiveaway",
    "AvatarKalcer",
    "EventRoblox",
    "RobloxIndonesia",
    "RobloxAnomali",
    "Roblox",
    "RobloxFyp"
]

client_gemini = genai.Client(api_key=GEMINI_API_KEY)

sent_events = set()

def ai_filter_event(text, today_str):
    prompt = f"""
    Kamu adalah AI yang memfilter event Roblox dari TikTok.
    Hari ini: {today_str}

    Konten TikTok: "{text}"

    Tugas:
    1. Tentukan apakah ini EVENT Roblox yang RELEVAN.
    2. HANYA event yang AKAN DATANG atau SEDANG BERLANGSUNG yang dianggap event.
    3. JIKA event sudah SELESAI, lewat, atau hanya pengumuman pemenang, TOLAK.

    Kategori event:
    - fashion_show
    - avatar_kalcer
    - giveaway
    - competition
    - community_event
    - event_kemerdekaan
    - event_anomali (event aneh, unik, misterius, glitch, horor, atau fenomena langka di Roblox)

    Output JSON:
    {{
        "is_event": true,
        "title": "Judul event",
        "category": "kategori",
        "description": "Deskripsi singkat dan jelas",
        "prize": "Hadiah (Robux/uang) atau 'Tidak disebutkan'",
        "deadline": "Tanggal/waktu deadline atau 'Tidak disebutkan'",
        "reason": "Alasan lolos/tolak"
    }}

    Jawab HANYA JSON.
    """
    try:
        response = client_gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        result_text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
    except Exception as e:
        print(f"Error AI: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}

async def get_tiktok_events():
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
                "resultsPerPage": 5
            }
            headers = {"Content-Type": "application/json"}
            resp = requests.post(url, json=payload, headers=headers)
            run_data = resp.json()
            run_id = run_data.get("data", {}).get("id")
            if not run_id:
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
                video_url = item.get("webVideoUrl") or item.get("url") or ""
                if caption:
                    all_videos.append({
                        "caption": caption,
                        "author": author,
                        "hashtag": hashtag,
                        "url": video_url
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

        await self.cek_dan_kirim_event()

    async def send_event_to_dashboard(self, event_info, source_author="unknown", source_hashtag="", video_url=""):
        channel = self.get_channel(self.dashboard_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=(
                f"**📝 Deskripsi**\n{event_info.get('description', 'Tidak ada deskripsi')}"
            ),
            color=discord.Color.blue()
        )

        embed.add_field(name="📂 Kategori", value=event_info.get('category', 'Tidak diketahui'), inline=True)
        embed.add_field(name="💰 Hadiah", value=event_info.get('prize', 'Tidak disebutkan'), inline=True)
        embed.add_field(name="⏰ Deadline", value=event_info.get('deadline', 'Tidak disebutkan'), inline=True)
        embed.add_field(name="📱 Sumber", value=f"TikTok @{source_author}", inline=True)
        embed.add_field(name="🏷️ Hashtag", value=f"#{source_hashtag}", inline=True)

        if video_url:
            embed.add_field(name="🔗 Link TikTok", value=f"[Klik di sini]({video_url})", inline=False)

        embed.set_footer(text="✅ Event lolos filter AI")
        await channel.send(embed=embed)
        print(f"Event dikirim: {event_info.get('title')}")

    async def cek_dan_kirim_event(self):
        print("\n[Bot] MULAI CEK EVENT TIKTOK...\n")
        videos = await get_tiktok_events()

        if not videos:
            print("Tidak ada video ditemukan.")
            return

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        event_count = 0

        for video in videos:
            unique_key = video.get('url', '') or video.get('caption', '')
            if unique_key in sent_events:
                continue

            result = ai_filter_event(video['caption'], today_str)

            if result.get('is_event'):
                title = result.get('title', 'Event Roblox')
                print(f"EVENT: {title}")
                await self.send_event_to_dashboard(
                    result,
                    video['author'],
                    video['hashtag'],
                    video.get('url', '')
                )
                sent_events.add(unique_key)
                event_count += 1
            else:
                print("Bukan event / sudah lewat")

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
