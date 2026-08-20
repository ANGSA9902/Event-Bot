import discord
import asyncio
from google import genai
import json
import os
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timezone
import pytz

# ============================================================
# KONFIGURASI & VALIDASI ENV VAR
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DASHBOARD_CHANNEL_ID_RAW = os.getenv("DASHBOARD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

_missing = [
    name for name, val in [
        ("DISCORD_TOKEN", DISCORD_TOKEN),
        ("DASHBOARD_CHANNEL_ID", DASHBOARD_CHANNEL_ID_RAW),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
        ("APIFY_TOKEN", APIFY_TOKEN),
    ] if not val
]
if _missing:
    raise RuntimeError(f"Environment variable belum diset: {', '.join(_missing)}")

try:
    DASHBOARD_CHANNEL_ID = int(DASHBOARD_CHANNEL_ID_RAW)
except ValueError:
    raise RuntimeError("DASHBOARD_CHANNEL_ID harus berupa angka (channel ID).")

ACTOR_ID = "clockworks~tiktok-scraper"

HASHTAGS = [
    "FashionShowRoblox",
    "RobloxEvent",
    "RobloxIndonesia",
    "RobloxFyp",
    "RobloxAnomali",
]

client_gemini = genai.Client(api_key=GEMINI_API_KEY)

# ============================================================
# PERSISTENCE UNTUK sent_events
# ============================================================

SENT_EVENTS_FILE = "sent_events.json"


def load_sent_events():
    if os.path.exists(SENT_EVENTS_FILE):
        try:
            with open(SENT_EVENTS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            print(f"⚠️ Gagal load {SENT_EVENTS_FILE}: {e}")
    return set()


def save_sent_events(sent_events):
    try:
        with open(SENT_EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(sent_events), f)
    except Exception as e:
        print(f"⚠️ Gagal save {SENT_EVENTS_FILE}: {e}")


sent_events = load_sent_events()


# ============================================================
# AI FILTER (Gemini)
# ============================================================

def _call_gemini_sync(prompt):
    response = client_gemini.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
    )
    return response.text


async def ai_filter_event(text, today_str):
    prompt = f"""
    Kamu adalah AI yang memfilter event Roblox dari TikTok.
    Hari ini: {today_str}
    Konten TikTok: "{text}"

    Tugas:
    1. Tentukan apakah ini EVENT Roblox yang RELEVAN.
    2. HANYA event yang AKAN DATANG atau SEDANG BERLANGSUNG.
    3. Event yang sudah selesai / pengumuman pemenang = TOLAK.
    4. Prioritaskan event Indonesia.

    Kategori:
    fashion_show, avatar_kalcer, giveaway, competition, community_event, event_kemerdekaan, event_anomali

    Output JSON:
    {{
        "is_event": true,
        "title": "...",
        "category": "...",
        "description": "...",
        "prize": "...",
        "deadline": "...",
        "reason": "..."
    }}
    Jawab HANYA JSON.
    """
    raw_text = ""
    try:
        raw_text = await asyncio.to_thread(_call_gemini_sync, prompt)
        cleaned = raw_text.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"❌ Gemini tidak mengembalikan JSON valid: {e}")
        print(f"   Respons mentah: {raw_text[:300]}")
        return {"is_event": False, "reason": f"JSON parse error: {e}"}
    except Exception as e:
        print(f"❌ Error AI: {e}")
        return {"is_event": False, "reason": f"Error: {e}"}


# ============================================================
# APIFY SCRAPER (SEQUENTIAL, bukan parallel, untuk hindari memory limit)
# ============================================================

async def scrape_hashtag(hashtag):
    videos = []
    try:
        print(f"Mencari video dengan #{hashtag}...")
        run_url = f"https://api.apify.com/v2/acts/{ACTOR_ID}/runs?token={APIFY_TOKEN}"
        payload = {
            "hashtags": [hashtag],
            "resultsPerPage": 3,
            "maxResults": 3,
        }
        headers = {"Content-Type": "application/json"}

        resp = await asyncio.to_thread(
            requests.post, run_url, json=payload, headers=headers, timeout=30
        )
        if resp.status_code >= 400:
            print(f"❌ Apify run gagal dibuat untuk #{hashtag}: {resp.status_code} {resp.text[:200]}")
            return videos

        run_data = resp.json()
        run_id = run_data.get("data", {}).get("id")
        if not run_id:
            print(f"❌ Tidak dapat run_id untuk #{hashtag}: {run_data}")
            return videos

        status_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={APIFY_TOKEN}"
        dataset_url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items?token={APIFY_TOKEN}"

        final_status = None
        for _ in range(20):
            await asyncio.sleep(3)
            status_resp = await asyncio.to_thread(requests.get, status_url, timeout=30)
            status_data = status_resp.json()
            status = status_data.get("data", {}).get("status")
            if status in ("SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"):
                final_status = status
                break

        if final_status != "SUCCEEDED":
            print(f"⚠️ Run untuk #{hashtag} berakhir dengan status: {final_status}")

        items_resp = await asyncio.to_thread(requests.get, dataset_url, timeout=30)
        items = items_resp.json()
        if not isinstance(items, list):
            print(f"⚠️ Format dataset items tidak terduga untuk #{hashtag}")
            items = []

        for item in items:
            caption = item.get("text") or item.get("desc") or ""
            author = item.get("authorMeta", {}).get("name", "unknown")
            video_url = item.get("webVideoUrl") or item.get("url") or ""
            if caption:
                videos.append({
                    "caption": caption,
                    "author": author,
                    "hashtag": hashtag,
                    "url": video_url,
                })

    except Exception as e:
        print(f"❌ Error #{hashtag}: {e}")

    return videos


async def get_tiktok_events():
    all_videos = []
    for hashtag in HASHTAGS:
        videos = await scrape_hashtag(hashtag)
        all_videos.extend(videos)
        await asyncio.sleep(2)

    print(f"Total video ditemukan: {len(all_videos)}")
    return all_videos


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True


class DashboardBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.dashboard_channel_id = DASHBOARD_CHANNEL_ID
        self._initial_run_done = False

    async def on_ready(self):
        print(f"Bot {self.user} berhasil login!")

        if self._initial_run_done:
            print("(Reconnect terdeteksi, skip initial run ulang)")
            return
        self._initial_run_done = True

        channel = self.get_channel(self.dashboard_channel_id)
        if channel:
            embed = discord.Embed(
                title="🟢 Bot Event Filter ONLINE!",
                description="Siap memantau event Roblox dari TikTok...",
                color=discord.Color.green(),
            )
            await channel.send(embed=embed)
        else:
            print(f"⚠️ Channel dengan ID {self.dashboard_channel_id} tidak ditemukan!")

        await self.cek_dan_kirim_event()

    async def send_event_to_dashboard(self, event_info, source_author="unknown", source_hashtag="", video_url=""):
        channel = self.get_channel(self.dashboard_channel_id)
        if not channel:
            print("⚠️ Channel tidak ditemukan, event tidak terkirim.")
            return

        embed = discord.Embed(
            title=f"🎮 {event_info.get('title', 'Event Roblox')}",
            description=f"**📝 Deskripsi**\n{event_info.get('description', 'Tidak ada deskripsi')}",
            color=discord.Color.blue(),
        )

        embed.add_field(name="📂 Kategori", value=event_info.get("category", "Tidak diketahui"), inline=True)
        embed.add_field(name="💰 Hadiah", value=event_info.get("prize", "Tidak disebutkan"), inline=True)
        embed.add_field(name="⏰ Deadline", value=event_info.get("deadline", "Tidak disebutkan"), inline=True)
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
            unique_key = video.get("url", "") or video.get("caption", "")
            if unique_key in sent_events:
                continue

            result = await ai_filter_event(video["caption"], today_str)

            if result.get("is_event"):
                title = result.get("title", "Event Roblox")
                print(f"EVENT: {title}")
                await self.send_event_to_dashboard(
                    result,
                    video["author"],
                    video["hashtag"],
                    video.get("url", ""),
                )
                sent_events.add(unique_key)
                save_sent_events(sent_events)
                event_count += 1
            else:
                print("Bukan event / sudah lewat")

        print(f"\nTotal event dikirim: {event_count}")


# ============================================================
# SCHEDULER
# ============================================================

WIB = pytz.timezone("Asia/Jakarta")
scheduler = AsyncIOScheduler(timezone=WIB)

bot_instance: DashboardBot | None = None


async def update_pagi():
    print(f"\nUPDATE PAGI - {datetime.now(WIB).strftime('%H:%M WIB')}")
    if bot_instance is None or not bot_instance.is_ready():
        print("⚠️ Bot belum siap/belum login, skip update pagi ini.")
        return
    await bot_instance.cek_dan_kirim_event()


scheduler.add_job(update_pagi, "cron", hour=6, minute=0, id="update_pagi")


async def main():
    global bot_instance
    bot_instance = DashboardBot()
    scheduler.start()
    print("Scheduler aktif! Bot update jam 6 pagi WIB.")
    await bot_instance.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
