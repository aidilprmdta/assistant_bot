"""
Layer penyimpanan persistent menggunakan SQLite.
Tanggung jawab modul ini murni soal database - tidak tahu-menahu
soal LLM atau logic bot, supaya gampang diganti (misal ke Postgres)
di masa depan tanpa mengubah core/.
"""
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).parent / "data" / "assistant.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode + busy_timeout: penting kalau bot dijalankan sebagai beberapa
    # proses sekaligus (misal Telegram & Discord bareng lewat Docker Compose),
    # supaya tidak gampang kena "database is locked".
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    """Buat tabel kalau belum ada. Dipanggil sekali saat aplikasi start."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                task TEXT NOT NULL,
                is_done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL DEFAULT 'default',
                chat_id TEXT NOT NULL,
                message TEXT NOT NULL,
                remind_at_utc TEXT NOT NULL,
                is_sent INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_message(role: str, content: str, session_id: str = "default") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, role, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def load_messages(session_id: str = "default", limit: int = 100) -> list[dict]:
    """Ambil history terbaru untuk session tertentu, urut dari lama ke baru."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    # rows datang dari terbaru ke terlama karena DESC, balikin urutannya
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_history(session_id: str = "default") -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()


# --- Todo CRUD ---

def add_todo(task: str, session_id: str = "default") -> int:
    """Simpan todo baru, kembalikan id-nya."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO todos (session_id, task, created_at) VALUES (?, ?, ?)",
            (session_id, task, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def list_todos(session_id: str = "default", include_done: bool = True) -> list[dict]:
    query = "SELECT id, task, is_done, created_at, done_at FROM todos WHERE session_id = ?"
    params = [session_id]
    if not include_done:
        query += " AND is_done = 0"
    query += " ORDER BY id ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r["id"],
            "task": r["task"],
            "is_done": bool(r["is_done"]),
            "created_at": r["created_at"],
            "done_at": r["done_at"],
        }
        for r in rows
    ]


def mark_todo_done(todo_id: int, session_id: str = "default") -> bool:
    """Tandai todo selesai. Return True kalau berhasil (id ditemukan)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE todos SET is_done = 1, done_at = ? "
            "WHERE id = ? AND session_id = ?",
            (datetime.now(timezone.utc).isoformat(), todo_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_todo(todo_id: int, session_id: str = "default") -> bool:
    """Hapus todo. Return True kalau berhasil (id ditemukan)."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM todos WHERE id = ? AND session_id = ?",
            (todo_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# --- Note CRUD ---

def add_note(title: str, content: str, session_id: str = "default") -> int:
    """Simpan note baru, kembalikan id-nya."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (session_id, title, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, title, content, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def list_notes(session_id: str = "default") -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "content": r["content"], "created_at": r["created_at"]}
        for r in rows
    ]


def get_note(note_id: int, session_id: str = "default") -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE id = ? AND session_id = ?",
            (note_id, session_id),
        ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "title": row["title"], "content": row["content"], "created_at": row["created_at"]}


def search_notes(keyword: str, session_id: str = "default") -> list[dict]:
    """Cari note berdasarkan keyword di judul atau isi (case-insensitive)."""
    like_pattern = f"%{keyword}%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, title, content, created_at FROM notes "
            "WHERE session_id = ? AND (title LIKE ? COLLATE NOCASE OR content LIKE ? COLLATE NOCASE) "
            "ORDER BY id ASC",
            (session_id, like_pattern, like_pattern),
        ).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "content": r["content"], "created_at": r["created_at"]}
        for r in rows
    ]


def delete_note(note_id: int, session_id: str = "default") -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM notes WHERE id = ? AND session_id = ?",
            (note_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0


# --- Reminder CRUD ---

def add_reminder(chat_id: str, message: str, remind_at_utc: str, session_id: str = "default") -> int:
    """remind_at_utc harus string ISO 8601 dalam UTC."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO reminders (session_id, chat_id, message, remind_at_utc, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, chat_id, message, remind_at_utc, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cursor.lastrowid


def list_reminders(session_id: str = "default", include_sent: bool = False) -> list[dict]:
    query = "SELECT id, chat_id, message, remind_at_utc, is_sent FROM reminders WHERE session_id = ?"
    params = [session_id]
    if not include_sent:
        query += " AND is_sent = 0"
    query += " ORDER BY remind_at_utc ASC"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r["id"],
            "chat_id": r["chat_id"],
            "message": r["message"],
            "remind_at_utc": r["remind_at_utc"],
            "is_sent": bool(r["is_sent"]),
        }
        for r in rows
    ]


def get_all_pending_reminders() -> list[dict]:
    """Ambil SEMUA reminder yang belum terkirim, lintas session - dipakai saat
    bot baru start untuk menjadwalkan ulang reminder yang sempat tertunda."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, session_id, chat_id, message, remind_at_utc FROM reminders "
            "WHERE is_sent = 0 ORDER BY remind_at_utc ASC"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "chat_id": r["chat_id"],
            "message": r["message"],
            "remind_at_utc": r["remind_at_utc"],
        }
        for r in rows
    ]


def mark_reminder_sent(reminder_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (reminder_id,))
        conn.commit()


def delete_reminder(reminder_id: int, session_id: str = "default") -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND session_id = ?",
            (reminder_id, session_id),
        )
        conn.commit()
        return cursor.rowcount > 0