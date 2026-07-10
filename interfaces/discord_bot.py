"""
Adaptor Discord untuk Asisten Bot.
Jalankan dari root folder project dengan:
    python -m interfaces.discord_bot

Setiap user Discord otomatis dapat sesi terpisah (memory, todo, note,
reminder masing-masing tidak saling campur), karena kita pakai user_id
Discord (dengan prefix "discord_") sebagai session_id.

PENTING: Bot ini butuh "Message Content Intent" diaktifkan di Discord
Developer Portal (Bot > Privileged Gateway Intents), supaya bisa membaca
isi pesan chat biasa (bukan cuma command).
"""
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import DISCORD_BOT_TOKEN, LLM_BASE_URL, MODEL_NAME
from core.brain import Brain
from core.memory import ConversationMemory
from services.todo_service import TodoService
from services.note_service import NoteService
from services.reminder_service import ReminderService
from storage import db
from main import handle_todo_command, handle_note_command, HELP_TEXT

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True  # wajib, biar bot bisa baca isi pesan chat biasa

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
scheduler = AsyncIOScheduler()

# Cache instance per user, supaya tidak reload dari DB tiap pesan.
_user_sessions: dict[str, dict] = {}


def get_session(session_id: str) -> dict:
    if session_id not in _user_sessions:
        memory = ConversationMemory(session_id=session_id)
        todo = TodoService(session_id=session_id)
        note = NoteService(session_id=session_id)
        reminder = ReminderService(session_id=session_id)
        _user_sessions[session_id] = {
            "brain": Brain(memory, services={"todo": todo, "note": note, "reminder": reminder}),
            "memory": memory,
            "todo": todo,
            "note": note,
            "reminder": reminder,
        }
    return _user_sessions[session_id]


def _session_id_from(author_id: int) -> str:
    return f"discord_{author_id}"


async def send_reminder_message(target_channel_id: str, message: str, reminder_id: int) -> None:
    """Dipanggil oleh APScheduler tepat di waktu yang dijadwalkan."""
    try:
        channel = bot.get_channel(int(target_channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(target_channel_id))
        await channel.send(f"⏰ Pengingat: {message}")
    finally:
        db.mark_reminder_sent(reminder_id)


def _schedule_reminder_job(reminder: dict) -> None:
    """Daftarkan satu reminder ke APScheduler agar dieksekusi di waktunya."""
    run_date = datetime.fromisoformat(reminder["remind_at_utc"])
    scheduler.add_job(
        send_reminder_message,
        trigger="date",
        run_date=run_date,
        args=[reminder["chat_id"], reminder["message"], reminder["id"]],
        id=f"reminder_{reminder['id']}",
        misfire_grace_time=None,  # tetap kirim meski bot sempat mati & telat
    )


@bot.event
async def on_ready():
    """Jalan sekali saat bot berhasil connect: nyalakan scheduler & jadwalkan
    ulang semua reminder yang belum terkirim (misalnya bot sempat mati)."""
    logger.info(f"Bot Discord login sebagai {bot.user}")

    if not scheduler.running:
        scheduler.start()

    pending = db.get_all_pending_reminders()
    now_utc = datetime.now(timezone.utc)
    for reminder in pending:
        if not reminder["session_id"].startswith("discord_"):
            continue  # biarkan reminder Telegram ditangani oleh telegram_bot.py
        run_date = datetime.fromisoformat(reminder["remind_at_utc"])
        if run_date <= now_utc:
            await send_reminder_message(reminder["chat_id"], reminder["message"], reminder["id"])
        else:
            _schedule_reminder_job(reminder)
    logger.info(f"Menjadwalkan ulang reminder Discord yang tertunda.")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return  # abaikan pesan dari bot lain (termasuk diri sendiri)

    # Biarkan command (prefix "!") diproses oleh command handler di bawah
    await bot.process_commands(message)
    if message.content.startswith("!"):
        return

    session_id = _session_id_from(message.author.id)
    session = get_session(session_id)
    channel_id = str(message.channel.id)

    async with message.channel.typing():
        reply = session["brain"].think(
            message.content,
            chat_id=channel_id,
            on_reminder_created=lambda r: _schedule_reminder_job(r),
        )
    await message.channel.send(reply)


@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    await ctx.send(HELP_TEXT)


@bot.command(name="reset")
async def reset_cmd(ctx: commands.Context):
    session_id = _session_id_from(ctx.author.id)
    session = get_session(session_id)
    session["memory"].reset()
    await ctx.send("Riwayat percakapan sudah dihapus permanen dari database.")


@bot.command(name="todo")
async def todo_cmd(ctx: commands.Context, *, args: str = ""):
    session_id = _session_id_from(ctx.author.id)
    session = get_session(session_id)
    reply = handle_todo_command(args, session["todo"])
    await ctx.send(reply)


@bot.command(name="note")
async def note_cmd(ctx: commands.Context, *, args: str = ""):
    session_id = _session_id_from(ctx.author.id)
    session = get_session(session_id)
    reply = handle_note_command(args, session["note"])
    await ctx.send(reply)


@bot.command(name="remind")
async def remind_cmd(ctx: commands.Context, *, args: str = ""):
    session_id = _session_id_from(ctx.author.id)
    channel_id = str(ctx.channel.id)
    session = get_session(session_id)

    parts = args.strip().split(maxsplit=1)
    if not parts:
        await ctx.send(
            "Format: !remind <waktu> | <pesan>\n"
            "Contoh: !remind 30m | Minum air putih\n"
            "Sub-command lain: !remind list, !remind delete <id>"
        )
        return

    sub_command = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub_command == "list":
        await ctx.send(session["reminder"].list())
        return
    elif sub_command == "delete":
        if not rest.strip().isdigit():
            await ctx.send("Butuh nomor id yang valid. Contoh: !remind delete 3")
            return
        await ctx.send(session["reminder"].delete(int(rest.strip())))
        return

    if "|" not in args:
        await ctx.send(
            "Format salah. Pisahkan waktu dan pesan dengan '|'.\n"
            "Contoh: !remind 30m | Minum air putih"
        )
        return

    time_text, message_text = args.split("|", maxsplit=1)
    reply, reminder = session["reminder"].add(
        chat_id=channel_id, time_text=time_text, message=message_text
    )
    await ctx.send(reply)

    if reminder is not None:
        _schedule_reminder_job(reminder)


def main() -> None:
    if not DISCORD_BOT_TOKEN:
        raise ValueError(
            "DISCORD_BOT_TOKEN belum diatur di .env. "
            "Buat bot di https://discord.com/developers/applications, "
            "aktifkan 'Message Content Intent', lalu tambahkan token ke .env"
        )

    db.init_db()
    logger.info(f"Konfigurasi LLM aktif -> base_url: {LLM_BASE_URL} | model: {MODEL_NAME}")
    logger.info("Bot Discord mulai berjalan...")
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()