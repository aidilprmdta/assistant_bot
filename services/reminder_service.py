"""
Service layer untuk fitur reminder.
Menangani parsing input waktu dari user (relatif atau absolut) dan
operasi CRUD ke database. Penjadwalan aktual (siapa yang benar-benar
"membangunkan" reminder di waktu yang tepat) dilakukan oleh APScheduler
di interfaces/telegram_bot.py - modul ini sengaja tidak bergantung ke
APScheduler supaya gampang di-test secara terpisah.
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from storage import db

# Timezone default untuk input waktu absolut dari user (WIB).
# Semua waktu tetap DISIMPAN dalam UTC di database.
LOCAL_TZ = ZoneInfo("Asia/Jakarta")

_RELATIVE_PATTERN = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)


class ReminderParseError(ValueError):
    """Dilempar kalau format waktu yang diinput user tidak bisa dipahami."""


def parse_time_to_utc(time_text: str, now_utc: datetime | None = None) -> datetime:
    """
    Ubah input waktu dari user jadi objek datetime UTC.

    Mendukung dua format:
    - Relatif: "10m" (10 menit lagi), "2h" (2 jam lagi), "1d" (1 hari lagi), "30s"
    - Absolut: "2026-07-11 09:00" (dianggap dalam timezone WIB / Asia/Jakarta)

    Raises ReminderParseError kalau format tidak dikenali atau waktu di masa lalu.
    """
    time_text = time_text.strip()
    now_utc = now_utc or datetime.now(timezone.utc)

    relative_match = _RELATIVE_PATTERN.match(time_text)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        delta_map = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
        }
        target_utc = now_utc + delta_map[unit]
        return target_utc

    # Coba parse sebagai format absolut "YYYY-MM-DD HH:MM"
    try:
        naive_dt = datetime.strptime(time_text, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ReminderParseError(
            "Format waktu tidak dikenali. Gunakan salah satu:\n"
            "  - Relatif: 10m, 2h, 1d, 30s\n"
            "  - Absolut: 2026-07-11 09:00 (waktu WIB)"
        )

    local_dt = naive_dt.replace(tzinfo=LOCAL_TZ)
    target_utc = local_dt.astimezone(timezone.utc)

    if target_utc <= now_utc:
        raise ReminderParseError("Waktu yang diinput sudah lewat. Pilih waktu di masa depan.")

    return target_utc


def _format_local(remind_at_utc_str: str) -> str:
    """Format waktu UTC dari database jadi tampilan WIB yang enak dibaca."""
    dt_utc = datetime.fromisoformat(remind_at_utc_str)
    dt_local = dt_utc.astimezone(LOCAL_TZ)
    return dt_local.strftime("%Y-%m-%d %H:%M WIB")


class ReminderService:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id

    def add(self, chat_id: str, time_text: str, message: str) -> tuple[str, dict | None]:
        """
        Return tuple (teks_balasan, reminder_dict_atau_None).
        reminder_dict diisi kalau berhasil, supaya caller (telegram_bot.py)
        bisa langsung menjadwalkannya ke APScheduler tanpa query ulang.
        """
        message = message.strip()
        if not message:
            return "Isi pesan reminder tidak boleh kosong.", None

        try:
            target_utc = parse_time_to_utc(time_text)
        except ReminderParseError as e:
            return str(e), None

        reminder_id = db.add_reminder(
            chat_id=chat_id,
            message=message,
            remind_at_utc=target_utc.isoformat(),
            session_id=self.session_id,
        )
        waktu_tampil = _format_local(target_utc.isoformat())
        reply = f"⏰ Reminder #{reminder_id} diatur untuk {waktu_tampil}: {message}"
        reminder = {
            "id": reminder_id,
            "chat_id": chat_id,
            "message": message,
            "remind_at_utc": target_utc.isoformat(),
        }
        return reply, reminder

    def list(self) -> str:
        reminders = db.list_reminders(session_id=self.session_id, include_sent=False)
        if not reminders:
            return "Belum ada reminder aktif. Tambahkan dengan: /remind <waktu> | <pesan>"

        lines = ["⏰ Reminder Aktif:"]
        for r in reminders:
            waktu_tampil = _format_local(r["remind_at_utc"])
            lines.append(f"  #{r['id']} - {waktu_tampil} - {r['message']}")
        return "\n".join(lines)

    def delete(self, reminder_id: int) -> str:
        success = db.delete_reminder(reminder_id, session_id=self.session_id)
        if success:
            return f"🗑️ Reminder #{reminder_id} dibatalkan."
        return f"Reminder #{reminder_id} tidak ditemukan."
