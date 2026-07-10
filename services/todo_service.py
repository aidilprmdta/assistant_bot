"""
todo_service.py
================
Logic fitur to-do list untuk Asisten Bot.

Dipakai seperti ini (lihat main.py & interfaces/telegram_bot.py):

    todo_service = TodoService()                        # CLI, session default
    todo_service = TodoService(session_id=session_id)    # Telegram/Discord, per-user

    todo_service.add("Beli susu")          -> str (pesan konfirmasi)
    todo_service.list(only_pending=False)  -> str (daftar tugas, sudah diformat)
    todo_service.done(3)                   -> str (pesan konfirmasi)
    todo_service.delete(3)                 -> str (pesan konfirmasi)

Setiap instance terikat ke satu session_id, jadi tugas antar user (CLI,
Telegram, Discord) otomatis terpisah karena session_id berbeda-beda.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "storage" / "data" / "assistant.db"

DEFAULT_SESSION_ID = "cli"


@contextmanager
def _get_conn():
    """Context manager untuk koneksi SQLite."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db():
    """Buat tabel todos kalau belum ada."""
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                task TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_todos_session ON todos(session_id)"
        )


class TodoService:
    """Layer logic untuk fitur to-do list, terikat ke satu session_id."""

    def __init__(self, session_id: str = DEFAULT_SESSION_ID):
        self.session_id = session_id
        _init_db()

    # -- operasi dasar -----------------------------------------------------

    def add(self, task: str) -> str:
        """Tambah tugas baru. `task` adalah teks tugas (bagian setelah 'add ')."""
        task = task.strip()
        if not task:
            return "Isi tugas tidak boleh kosong. Contoh: /todo add Beli susu"

        created_at = datetime.now().isoformat(timespec="seconds")
        with _get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO todos (session_id, task, done, created_at) VALUES (?, ?, 0, ?)",
                (self.session_id, task, created_at),
            )
            todo_id = cur.lastrowid

        return f"✅ Tugas ditambahkan (#{todo_id}): {task}"

    def list(self, only_pending: bool = False) -> str:
        """Ambil & format daftar tugas. Kalau only_pending=True, hanya yang
        belum selesai."""
        query = "SELECT * FROM todos WHERE session_id = ?"
        params = [self.session_id]
        if only_pending:
            query += " AND done = 0"
        query += " ORDER BY done ASC, id ASC"

        with _get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

        if not rows:
            if only_pending:
                return "Tidak ada tugas yang pending. Mantap! 🎉"
            return "Belum ada tugas nih. Tambahkan dengan /todo add <tugas>"

        lines = []
        for row in rows:
            mark = "✅" if row["done"] else "⬜"
            lines.append(f"{mark} #{row['id']} — {row['task']}")
        return "\n".join(lines)

    def done(self, todo_id: int) -> str:
        """Tandai satu tugas sebagai selesai."""
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM todos WHERE id = ? AND session_id = ?",
                (todo_id, self.session_id),
            ).fetchone()
            if row is None:
                return f"Tugas #{todo_id} tidak ditemukan."

            if row["done"]:
                return f"Tugas #{todo_id} memang sudah selesai kok: {row['task']}"

            done_at = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE todos SET done = 1, done_at = ? WHERE id = ? AND session_id = ?",
                (done_at, todo_id, self.session_id),
            )

        return f"🎉 Tugas #{todo_id} ditandai selesai: {row['task']}"

    def delete(self, todo_id: int) -> str:
        """Hapus satu tugas."""
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM todos WHERE id = ? AND session_id = ?",
                (todo_id, self.session_id),
            ).fetchone()
            if row is None:
                return f"Tugas #{todo_id} tidak ditemukan."

            conn.execute(
                "DELETE FROM todos WHERE id = ? AND session_id = ?",
                (todo_id, self.session_id),
            )

        return f"🗑️ Tugas #{todo_id} dihapus: {row['task']}"