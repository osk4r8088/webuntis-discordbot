import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord.ext import commands, tasks
import webuntis

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("vertretungsplan-bot")

# ---------------------------------------------------------------------------
# Config (from environment)
# ---------------------------------------------------------------------------
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

UNTIS_SCHOOL = os.environ["UNTIS_SCHOOL"]
UNTIS_SERVER = os.environ["UNTIS_SERVER"]       # e.g. "neilo.webuntis.com"
UNTIS_USERNAME = os.environ["UNTIS_USERNAME"]
UNTIS_PASSWORD = os.environ["UNTIS_PASSWORD"]
UNTIS_CLASS = os.environ.get("UNTIS_CLASS", "")  # Your class name, e.g. "FI24"

POLL_INTERVAL_MINUTES = int(os.environ.get("POLL_INTERVAL_MINUTES", "5"))
LOOKAHEAD_DAYS = int(os.environ.get("LOOKAHEAD_DAYS", "3"))

STATE_FILE = Path(__file__).parent / "data" / "last_state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# WebUntis helpers
# ---------------------------------------------------------------------------

def create_session() -> webuntis.Session:
    return webuntis.Session(
        school=UNTIS_SCHOOL,
        server=UNTIS_SERVER,
        username=UNTIS_USERNAME,
        password=UNTIS_PASSWORD,
        useragent="vertretungsplan-discord-bot/1.0",
    )


def find_class(session: webuntis.Session, class_name: str):
    """Find a Klasse object by its name."""
    for klasse in session.klassen():
        if klasse.name.lower() == class_name.lower():
            return klasse
    return None


def period_key(period) -> str:
    """Create a unique key for a timetable period."""
    date_str = period.start.strftime("%Y-%m-%d")
    start_str = period.start.strftime("%H:%M")
    end_str = period.end.strftime("%H:%M")
    subjects = ",".join(sorted(_safe_names(period, "subjects")))
    return f"{date_str}|{start_str}-{end_str}|{subjects}"


def _safe_names(period, attr_name):
    """Safely extract .name from a period attribute (teachers, subjects, rooms).
    Returns empty list if the account lacks API rights for that field."""
    try:
        return [item.name for item in getattr(period, attr_name)]
    except Exception:
        return []


def period_to_dict(period) -> dict:
    """Serialize a period into a comparable dict."""
    return {
        "date": period.start.strftime("%Y-%m-%d"),
        "weekday": period.start.strftime("%A"),
        "start": period.start.strftime("%H:%M"),
        "end": period.end.strftime("%H:%M"),
        "subjects": _safe_names(period, "subjects"),
        "teachers": _safe_names(period, "teachers"),
        "rooms": _safe_names(period, "rooms"),
        "code": getattr(period, "code", None),           # "cancelled", "irregular", etc.
        "subst_text": getattr(period, "substText", ""),   # free-text substitution info
    }


def fetch_timetable() -> dict[str, dict]:
    """
    Fetch timetable for the configured class over the next LOOKAHEAD_DAYS days.
    Returns a dict mapping period_key -> period_dict.
    """
    session = create_session()
    try:
        session.login()

        klasse = find_class(session, UNTIS_CLASS)
        if klasse is None:
            log.error("Class '%s' not found in WebUntis. Available: %s",
                      UNTIS_CLASS,
                      [k.name for k in session.klassen()])
            return {}

        today = datetime.now().date()
        end_date = today + timedelta(days=LOOKAHEAD_DAYS)

        periods = session.timetable(
            klasse=klasse,
            start=today,
            end=end_date,
        )

        result = {}
        for p in periods:
            key = period_key(p)
            result[key] = period_to_dict(p)

        log.info("Fetched %d periods for class %s", len(result), UNTIS_CLASS)
        return result

    finally:
        try:
            session.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

CHANGE_CANCELLED = "cancelled"
CHANGE_SUBSTITUTION = "substitution"
CHANGE_ROOM_CHANGE = "room_change"


def detect_changes(old: dict, new: dict) -> list[dict]:
    """
    Compare old and new timetable state.
    Returns a list of change dicts with type, period info, and details.
    """
    changes = []

    for key, new_period in new.items():
        old_period = old.get(key)

        # -- Cancelled --
        if new_period.get("code") == "cancelled":
            if old_period is None or old_period.get("code") != "cancelled":
                changes.append({
                    "type": CHANGE_CANCELLED,
                    "period": new_period,
                    "detail": "Stunde entfaellt",
                })
                continue

        # -- Substitution (teacher change or irregular code) --
        if old_period is not None:
            old_teachers = set(old_period.get("teachers", []))
            new_teachers = set(new_period.get("teachers", []))

            is_irregular = new_period.get("code") == "irregular"
            teacher_changed = old_teachers != new_teachers and old_teachers

            if is_irregular or teacher_changed:
                if new_period.get("code") != "cancelled":
                    changes.append({
                        "type": CHANGE_SUBSTITUTION,
                        "period": new_period,
                        "detail": (
                            f"{', '.join(old_teachers)} -> {', '.join(new_teachers)}"
                            if teacher_changed
                            else new_period.get("subst_text", "Vertretung")
                        ),
                    })

            # -- Room change --
            old_rooms = set(old_period.get("rooms", []))
            new_rooms = set(new_period.get("rooms", []))

            if old_rooms != new_rooms and old_rooms:
                changes.append({
                    "type": CHANGE_ROOM_CHANGE,
                    "period": new_period,
                    "detail": f"{', '.join(old_rooms)} -> {', '.join(new_rooms)}",
                })

        # -- Brand-new irregular/cancelled period (not seen before) --
        elif new_period.get("code") in ("irregular",) and old_period is None:
            changes.append({
                "type": CHANGE_SUBSTITUTION,
                "period": new_period,
                "detail": new_period.get("subst_text", "Vertretung"),
            })

    return changes


# ---------------------------------------------------------------------------
# Discord embed formatting
# ---------------------------------------------------------------------------

COLORS = {
    CHANGE_CANCELLED: 0xE74C3C,     # red
    CHANGE_SUBSTITUTION: 0xF39C12,   # orange
    CHANGE_ROOM_CHANGE: 0x3498DB,    # blue
}

TITLES = {
    CHANGE_CANCELLED: "Entfall",
    CHANGE_SUBSTITUTION: "Vertretung",
    CHANGE_ROOM_CHANGE: "Raumwechsel",
}

WEEKDAYS_DE = {
    "Monday": "Montag",
    "Tuesday": "Dienstag",
    "Wednesday": "Mittwoch",
    "Thursday": "Donnerstag",
    "Friday": "Freitag",
    "Saturday": "Samstag",
    "Sunday": "Sonntag",
}


def build_embed(change: dict) -> discord.Embed:
    p = change["period"]
    change_type = change["type"]

    weekday = WEEKDAYS_DE.get(p["weekday"], p["weekday"])
    subject = ", ".join(p["subjects"]) or "—"
    teachers = ", ".join(p["teachers"]) or "—"
    rooms = ", ".join(p["rooms"]) or "—"

    embed = discord.Embed(
        title=f"{TITLES[change_type]}  |  {subject}",
        color=COLORS[change_type],
        timestamp=datetime.now(),
    )
    embed.add_field(name="Datum", value=f"{weekday}, {p['date']}", inline=True)
    embed.add_field(name="Zeit", value=f"{p['start']} – {p['end']}", inline=True)
    embed.add_field(name="Fach", value=subject, inline=True)
    embed.add_field(name="Lehrer", value=teachers, inline=True)
    embed.add_field(name="Raum", value=rooms, inline=True)
    embed.add_field(name="Details", value=change["detail"], inline=False)

    embed.set_footer(text="Vertretungsplan Bot")
    return embed


# ---------------------------------------------------------------------------
# Discord Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("Bot is online as %s", bot.user)
    if not poll_timetable.is_running():
        poll_timetable.start()


@tasks.loop(minutes=POLL_INTERVAL_MINUTES)
async def poll_timetable():
    log.info("Polling WebUntis ...")
    try:
        new_state = await asyncio.to_thread(fetch_timetable)
    except Exception as e:
        log.error("Failed to fetch timetable: %s", e)
        return

    if not new_state:
        return

    old_state = load_state()
    changes = detect_changes(old_state, new_state)

    if changes:
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            log.error("Channel %s not found", CHANNEL_ID)
            return

        for change in changes:
            embed = build_embed(change)
            await channel.send(embed=embed)
            log.info("Sent notification: %s – %s",
                     TITLES[change["type"]],
                     ", ".join(change["period"]["subjects"]))
    else:
        log.info("No changes detected.")

    save_state(new_state)


@poll_timetable.before_loop
async def before_poll():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Manual commands
# ---------------------------------------------------------------------------

@bot.command(name="vplan")
async def cmd_vplan(ctx):
    """Manually check the Vertretungsplan right now."""
    await ctx.send("Checking Vertretungsplan ...")
    await poll_timetable()
    await ctx.send("Done.")


@bot.command(name="status")
async def cmd_status(ctx):
    """Show bot status and config."""
    state = load_state()
    embed = discord.Embed(title="Bot Status", color=0x2ECC71)
    embed.add_field(name="Klasse", value=UNTIS_CLASS, inline=True)
    embed.add_field(name="Intervall", value=f"{POLL_INTERVAL_MINUTES} min", inline=True)
    embed.add_field(name="Lookahead", value=f"{LOOKAHEAD_DAYS} Tage", inline=True)
    embed.add_field(name="Tracked periods", value=str(len(state)), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="test")
async def cmd_test(ctx):
    """Post sample notifications using real data from the next school week."""
    await ctx.send("Fetching next school week from WebUntis ...")

    try:
        def _fetch():
            session = create_session()
            try:
                session.login()
                klasse = find_class(session, UNTIS_CLASS)
                if klasse is None:
                    return []

                from datetime import date
                start = date(2026, 4, 13)
                end = date(2026, 4, 17)

                periods = session.timetable(klasse=klasse, start=start, end=end)
                return [period_to_dict(p) for p in periods]
            finally:
                try:
                    session.logout()
                except Exception:
                    pass

        periods = await asyncio.to_thread(_fetch)
    except Exception as e:
        await ctx.send(f"Error: {e}")
        return

    if not periods:
        await ctx.send("No periods found for 13.04 - 17.04.")
        return

    # Pick up to 3 periods and fake different change types for preview
    samples = periods[:3]
    fake_types = [CHANGE_CANCELLED, CHANGE_SUBSTITUTION, CHANGE_ROOM_CHANGE]

    for i, period in enumerate(samples):
        change_type = fake_types[i % len(fake_types)]
        if change_type == CHANGE_CANCELLED:
            detail = "Stunde entfaellt"
        elif change_type == CHANGE_SUBSTITUTION:
            detail = "Musterfrau -> Mustermann"
        else:
            detail = "A101 -> B205"

        change = {"type": change_type, "period": period, "detail": detail}
        embed = build_embed(change)
        embed.set_footer(text="TEST — Vertretungsplan Bot")
        await ctx.send(embed=embed)

    await ctx.send(f"Showed 3 sample notifications from {len(periods)} periods found for that week.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
