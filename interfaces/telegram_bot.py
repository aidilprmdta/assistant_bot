"""
Adaptor Telegram untuk Asisten Bot.
Jalankan dari root folder project dengan:
    python -m interfaces.telegram_bot

Setiap user Telegram otomatis dapat sesi terpisah (memory, todo, note,
reminder masing-masing tidak saling campur), karena kita pakai user_id
Telegram sebagai session_id - skema database yang sama dengan versi CLI.

Reminder betul-betul bisa "push" notifikasi ke user tanpa perlu mereka
ngetik apa-apa dulu, karena APScheduler jalan di background selama
proses bot ini aktif.
"""
import logging
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import TELEGRAM_BOT_TOKEN, LLM_BASE_URL, MODEL_NAME
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

# Cache instance per user, supaya tidak reload dari DB tiap pesan.
# Key: session_id (str user_id Telegram) -> dict berisi brain/todo/note/reminder service.
_user_sessions: dict[str, dict] = {}

scheduler = AsyncIOScheduler()


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


def _session_id_from(update: Update) -> str:
    return f"tg_{update.effective_user.id}"


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    nama = update.effective_user.first_name or "kamu"
    await update.message.reply_text(
        f"Halo {nama}! 👋 Aku asisten bot kamu, siap ngobrol dan bantu tugas.\n"
        f"Ketik /help untuk lihat semua perintah yang tersedia."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = _session_id_from(update)
    session = get_session(session_id)
    session["memory"].reset()
    await update.message.reply_text("Riwayat percakapan sudah dihapus permanen dari database.")


async def todo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = _session_id_from(update)
    session = get_session(session_id)
    args = " ".join(context.args) if context.args else ""
    reply = handle_todo_command(args, session["todo"])
    await update.message.reply_text(reply)


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = _session_id_from(update)
    session = get_session(session_id)
    args = " ".join(context.args) if context.args else ""
    reply = handle_note_command(args, session["note"])
    await update.message.reply_text(reply)


async def send_reminder_message(bot, chat_id: str, message: str, reminder_id: int) -> None:
    """Dipanggil oleh APScheduler tepat di waktu yang dijadwalkan."""
    try:
        await bot.send_message(chat_id=chat_id, text=f"⏰ Pengingat: {message}")
    finally:
        db.mark_reminder_sent(reminder_id)


def _schedule_reminder_job(app: Application, reminder: dict) -> None:
    """Daftarkan satu reminder ke APScheduler agar dieksekusi di waktunya."""
    run_date = datetime.fromisoformat(reminder["remind_at_utc"])
    scheduler.add_job(
        send_reminder_message,
        trigger="date",
        run_date=run_date,
        args=[app.bot, reminder["chat_id"], reminder["message"], reminder["id"]],
        id=f"reminder_{reminder['id']}",
        misfire_grace_time=None,  # tetap kirim meski bot sempat mati & telat
    )


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = _session_id_from(update)
    chat_id = str(update.effective_chat.id)
    session = get_session(session_id)

    args = " ".join(context.args) if context.args else ""
    parts = args.strip().split(maxsplit=1)

    if not parts:
        await update.message.reply_text(
            "Format: /remind <waktu> | <pesan>\n"
            "Contoh: /remind 30m | Minum air putih\n"
            "Sub-command lain: /remind list, /remind delete <id>"
        )
        return

    sub_command = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub_command == "list":
        await update.message.reply_text(session["reminder"].list())
        return
    elif sub_command == "delete":
        if not rest.strip().isdigit():
            await update.message.reply_text("Butuh nomor id yang valid. Contoh: /remind delete 3")
            return
        await update.message.reply_text(session["reminder"].delete(int(rest.strip())))
        return

    # Selain "list"/"delete", asumsikan ini format "<waktu> | <pesan>"
    if "|" not in args:
        await update.message.reply_text(
            "Format salah. Pisahkan waktu dan pesan dengan '|'.\n"
            "Contoh: /remind 30m | Minum air putih"
        )
        return

    time_text, message = args.split("|", maxsplit=1)
    reply, reminder = session["reminder"].add(chat_id=chat_id, time_text=time_text, message=message)
    await update.message.reply_text(reply)

    if reminder is not None:
        _schedule_reminder_job(context.application, reminder)


async def chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session_id = _session_id_from(update)
    session = get_session(session_id)
    chat_id = str(update.effective_chat.id)

    # Kasih tahu user kalau bot sedang mengetik, biar berasa "hidup"
    await update.message.chat.send_action(action="typing")

    reply = session["brain"].think(
        update.message.text,
        chat_id=chat_id,
        on_reminder_created=lambda r: _schedule_reminder_job(context.application, r),
    )
    await update.message.reply_text(reply)


async def _on_startup(app: Application) -> None:
    """Jalan sekali saat bot start: nyalakan scheduler & jadwalkan ulang
    semua reminder yang belum terkirim (misalnya bot sempat mati)."""
    scheduler.start()
    pending = db.get_all_pending_reminders()
    now_utc = datetime.now(timezone.utc)

    for reminder in pending:
        run_date = datetime.fromisoformat(reminder["remind_at_utc"])
        if run_date <= now_utc:
            # Sudah lewat waktunya waktu bot mati - kirim sekarang juga
            await send_reminder_message(app.bot, reminder["chat_id"], reminder["message"], reminder["id"])
        else:
            _schedule_reminder_job(app, reminder)

    logger.info(f"Menjadwalkan ulang {len(pending)} reminder yang tertunda.")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN belum diatur di .env. "
            "Dapatkan token dari @BotFather di Telegram, lalu tambahkan ke .env"
        )

    db.init_db()

    logger.info(f"Konfigurasi LLM aktif -> base_url: {LLM_BASE_URL} | model: {MODEL_NAME}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(_on_startup).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("todo", todo_command))
    app.add_handler(CommandHandler("note", note_command))
    app.add_handler(CommandHandler("remind", remind_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_message))

    logger.info("Bot Telegram mulai berjalan...")
    app.run_polling()


if __name__ == "__main__":
    main()