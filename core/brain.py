"""
"Otak" bot: bertanggung jawab mengirim pesan ke LLM (lewat API
OpenAI-compatible, misalnya Agent Router) dan mengembalikan respons teks.

Pakai library `openai` (bukan `anthropic`) supaya kompatibel dengan
gateway seperti Agent Router (agentrouter.org) yang meneruskan request
ke Claude/GPT/dll lewat format OpenAI. Fungsi todo/note/reminder tetap
bisa dipanggil lewat obrolan natural pakai function calling versi OpenAI
(tools dengan "type": "function").

Catatan: fitur web search bawaan Anthropic TIDAK dipakai di sini karena
tidak didukung lewat gateway OpenAI-compatible generik.
"""
import json

from openai import OpenAI

from config import LLM_API_KEY, LLM_BASE_URL, MODEL_NAME
from core.memory import ConversationMemory
from core.personality import SYSTEM_PROMPT

client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

MAX_TOOL_ITERATIONS = 5

# --- Skema tool (format function calling OpenAI) untuk masing-masing fitur ---

def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TODO_TOOLS = [
    _tool("add_todo", "Tambahkan tugas baru ke daftar to-do user.",
          {"task": {"type": "string", "description": "Deskripsi tugasnya"}}, ["task"]),
    _tool("list_todos", "Tampilkan daftar tugas user. Set only_pending true kalau user cuma mau lihat yang belum selesai.",
          {"only_pending": {"type": "boolean"}}, []),
    _tool("complete_todo", "Tandai satu tugas selesai berdasarkan id.",
          {"todo_id": {"type": "integer"}}, ["todo_id"]),
    _tool("delete_todo", "Hapus satu tugas dari daftar to-do berdasarkan id.",
          {"todo_id": {"type": "integer"}}, ["todo_id"]),
]

NOTE_TOOLS = [
    _tool("add_note", "Simpan catatan baru dengan judul dan isi.",
          {"title": {"type": "string"}, "content": {"type": "string"}}, ["title", "content"]),
    _tool("list_notes", "Tampilkan daftar semua catatan user (ringkas).", {}, []),
    _tool("view_note", "Lihat isi lengkap satu catatan berdasarkan id.",
          {"note_id": {"type": "integer"}}, ["note_id"]),
    _tool("search_notes", "Cari catatan berdasarkan kata kunci di judul atau isi.",
          {"keyword": {"type": "string"}}, ["keyword"]),
    _tool("delete_note", "Hapus satu catatan berdasarkan id.",
          {"note_id": {"type": "integer"}}, ["note_id"]),
]

REMINDER_TOOLS = [
    _tool(
        "add_reminder",
        "Atur reminder/pengingat baru yang akan dikirim ke user di waktu tertentu. "
        "time_text mendukung format relatif ('10m','2h','1d','30s') atau absolut "
        "('YYYY-MM-DD HH:MM', dalam WIB).",
        {"time_text": {"type": "string"}, "message": {"type": "string", "description": "Isi pesan pengingatnya"}},
        ["time_text", "message"],
    ),
    _tool("list_reminders", "Tampilkan semua reminder aktif milik user.", {}, []),
    _tool("delete_reminder", "Batalkan satu reminder berdasarkan id.",
          {"reminder_id": {"type": "integer"}}, ["reminder_id"]),
]


class Brain:
    def __init__(self, memory: ConversationMemory, services: dict | None = None):
        """
        services: dict opsional berisi service yang mau di-expose sebagai
        tool ke model, contoh: {"todo": TodoService(...), "note": NoteService(...),
        "reminder": ReminderService(...)}. Kalau "reminder" tidak disertakan
        (misal di CLI), tool reminder tidak akan ditawarkan ke model sama sekali.
        """
        self.memory = memory
        self.services = services or {}

    def _build_tools(self) -> list:
        tools = []
        if "todo" in self.services:
            tools += TODO_TOOLS
        if "note" in self.services:
            tools += NOTE_TOOLS
        if "reminder" in self.services:
            tools += REMINDER_TOOLS
        return tools

    def _execute_tool(self, name: str, tool_input: dict, chat_id: str | None, on_reminder_created) -> str:
        """Jalankan satu tool call dan kembalikan hasilnya sebagai teks."""
        todo = self.services.get("todo")
        note = self.services.get("note")
        reminder = self.services.get("reminder")

        try:
            if name == "add_todo":
                return todo.add(tool_input["task"])
            elif name == "list_todos":
                return todo.list(only_pending=tool_input.get("only_pending", False))
            elif name == "complete_todo":
                return todo.done(int(tool_input["todo_id"]))
            elif name == "delete_todo":
                return todo.delete(int(tool_input["todo_id"]))

            elif name == "add_note":
                return note.add(tool_input["title"], tool_input["content"])
            elif name == "list_notes":
                return note.list()
            elif name == "view_note":
                return note.view(int(tool_input["note_id"]))
            elif name == "search_notes":
                return note.search(tool_input["keyword"])
            elif name == "delete_note":
                return note.delete(int(tool_input["note_id"]))

            elif name == "add_reminder":
                if not chat_id or not on_reminder_created:
                    return (
                        "Reminder cuma bisa diatur lewat Telegram atau Discord "
                        "(butuh kemampuan push notification yang tidak ada di versi CLI)."
                    )
                reply, created = reminder.add(
                    chat_id=chat_id,
                    time_text=tool_input["time_text"],
                    message=tool_input["message"],
                )
                if created is not None:
                    on_reminder_created(created)
                return reply
            elif name == "list_reminders":
                return reminder.list()
            elif name == "delete_reminder":
                return reminder.delete(int(tool_input["reminder_id"]))

            else:
                return f"Tool '{name}' tidak dikenali."
        except Exception as e:
            return f"Gagal menjalankan aksi: {e}"

    def think(self, user_input: str, chat_id: str | None = None, on_reminder_created=None) -> str:
        """
        Kirim input user + history ke LLM, dapatkan respons teks.

        chat_id & on_reminder_created cuma relevan kalau tool reminder
        di-trigger oleh model - diisi oleh interfaces/telegram_bot.py atau
        discord_bot.py supaya reminder yang dibuat lewat obrolan natural
        tetap bisa langsung dijadwalkan ke APScheduler.
        """
        self.memory.add_user_message(user_input)
        working_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(self.memory.get_history())
        tools = self._build_tools()

        reply = None
        try:
            for _ in range(MAX_TOOL_ITERATIONS):
                response = client.chat.completions.create(
                    model=MODEL_NAME,
                    max_tokens=1536,
                    messages=working_messages,
                    tools=tools if tools else None,
                )
                message = response.choices[0].message
                tool_calls = message.tool_calls or []

                if not tool_calls:
                    reply = (message.content or "").strip()
                    if not reply:
                        reply = "Hmm, aku tidak dapat balasan yang bisa ditampilkan. Coba tanya lagi ya."
                    break

                # Model minta eksekusi satu atau lebih tool - jalankan semua,
                # lalu kirim hasilnya balik supaya model bisa lanjut merespons.
                working_messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    result_text = self._execute_tool(tc.function.name, args, chat_id, on_reminder_created)
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })
            else:
                reply = "Maaf, permintaan ini butuh terlalu banyak langkah untuk diproses. Coba dipecah jadi beberapa pesan ya."
        except Exception as e:
            return f"Waduh, ada error waktu menghubungi API: {e}"

        self.memory.add_assistant_message(reply)
        return reply