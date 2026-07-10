"""
Manajemen memory/context percakapan.
Sekarang sudah persistent: history dimuat dari SQLite saat start,
dan setiap pesan baru langsung disimpan ke database.
"""
from config import MAX_HISTORY_MESSAGES
from storage import db


class ConversationMemory:
    def __init__(self, session_id: str = "default", max_messages: int = MAX_HISTORY_MESSAGES):
        self.session_id = session_id
        self.max_messages = max_messages
        db.init_db()
        # Muat history lama dari database saat bot dijalankan
        self.messages: list[dict] = db.load_messages(
            session_id=self.session_id, limit=max_messages
        )

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        db.save_message("user", content, session_id=self.session_id)
        self._trim()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        db.save_message("assistant", content, session_id=self.session_id)
        self._trim()

    def get_history(self) -> list[dict]:
        return self.messages

    def reset(self) -> None:
        """Hapus history di memory RAM sekaligus di database."""
        self.messages = []
        db.clear_history(session_id=self.session_id)

    def _trim(self) -> None:
        """Batasi jumlah pesan yang dikirim ke API supaya hemat token.
        Data lengkap tetap tersimpan aman di database, ini cuma
        membatasi apa yang dikirim sebagai context ke LLM."""
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]