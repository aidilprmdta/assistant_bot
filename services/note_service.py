"""
Service layer untuk fitur note-taking.
Sama seperti todo_service.py: membungkus akses database mentah
dan menyediakan output teks yang siap ditampilkan di chat.
"""
from storage import db


def _truncate(text: str, max_len: int = 60) -> str:
    text = text.replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "..."


class NoteService:
    def __init__(self, session_id: str = "default"):
        self.session_id = session_id

    def add(self, title: str, content: str) -> str:
        title = title.strip()
        content = content.strip()
        if not title or not content:
            return "Format salah. Contoh: /note add Judul Catatan | Isi catatannya di sini"
        note_id = db.add_note(title, content, session_id=self.session_id)
        return f"📝 Catatan disimpan (#{note_id}): {title}"

    def list(self) -> str:
        notes = db.list_notes(session_id=self.session_id)
        if not notes:
            return "Belum ada catatan. Tambahkan dengan: /note add <judul> | <isi>"

        lines = ["🗒️ Daftar Catatan:"]
        for n in notes:
            lines.append(f"  #{n['id']} - {n['title']} ({_truncate(n['content'])})")
        return "\n".join(lines)

    def view(self, note_id: int) -> str:
        note = db.get_note(note_id, session_id=self.session_id)
        if note is None:
            return f"Catatan #{note_id} tidak ditemukan."
        return f"📝 #{note['id']} - {note['title']}\n\n{note['content']}"

    def search(self, keyword: str) -> str:
        keyword = keyword.strip()
        if not keyword:
            return "Masukkan kata kunci pencarian. Contoh: /note search resep"
        results = db.search_notes(keyword, session_id=self.session_id)
        if not results:
            return f"Tidak ada catatan yang cocok dengan '{keyword}'."

        lines = [f"🔍 Hasil pencarian '{keyword}':"]
        for n in results:
            lines.append(f"  #{n['id']} - {n['title']} ({_truncate(n['content'])})")
        return "\n".join(lines)

    def delete(self, note_id: int) -> str:
        success = db.delete_note(note_id, session_id=self.session_id)
        if success:
            return f"🗑️ Catatan #{note_id} dihapus."
        return f"Catatan #{note_id} tidak ditemukan."
