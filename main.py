"""
Entry point utama. Jalankan: python main.py
Fitur:
- Chat via terminal dengan persistent memory
- Command dasar: /reset, /help, /exit
- To-do list: /todo add|list|pending|done|delete
- Note-taking: /note add|list|view|search|delete
"""
from core.brain import Brain
from core.memory import ConversationMemory
from services.todo_service import TodoService
from services.note_service import NoteService
from config import LLM_BASE_URL, MODEL_NAME

HELP_TEXT = """
Semua fitur di bawah juga bisa diminta lewat obrolan biasa, tidak harus
pakai command - misal ketik "tolong catetin tugas: beli susu" juga jalan.
Command ini cuma jalur cepat/pasti kalau kamu maunya eksplisit.

Perintah yang tersedia:
  /help              - tampilkan bantuan ini
  /reset             - hapus riwayat percakapan (permanen)
  /exit              - keluar dari program

  To-do list:
  /todo add <tugas>  - tambah tugas baru
  /todo list         - lihat semua tugas
  /todo pending      - lihat tugas yang belum selesai saja
  /todo done <id>    - tandai tugas selesai
  /todo delete <id>  - hapus tugas

  Catatan:
  /note add <judul> | <isi>  - simpan catatan baru
  /note list                 - lihat semua catatan (ringkas)
  /note view <id>            - lihat isi lengkap satu catatan
  /note search <kata kunci>  - cari catatan
  /note delete <id>          - hapus catatan

  Reminder (khusus versi Telegram):
  /remind <waktu> | <pesan>  - atur reminder baru
                               waktu: relatif (10m, 2h, 1d) atau absolut (2026-07-11 09:00, WIB)
  /remind list               - lihat reminder aktif
  /remind delete <id>        - batalkan reminder
"""


def handle_todo_command(args: str, todo_service: TodoService) -> str:
    """Parse dan jalankan sub-command /todo. Contoh args: 'add Beli susu', 'done 3'."""
    parts = args.strip().split(maxsplit=1)
    if not parts:
        return "Format salah. Contoh: /todo add Beli susu"

    sub_command = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub_command == "add":
        return todo_service.add(rest)
    elif sub_command == "list":
        return todo_service.list(only_pending=False)
    elif sub_command == "pending":
        return todo_service.list(only_pending=True)
    elif sub_command in ("done", "delete"):
        if not rest.strip().isdigit():
            return f"Butuh nomor id yang valid. Contoh: /todo {sub_command} 3"
        todo_id = int(rest.strip())
        return todo_service.done(todo_id) if sub_command == "done" else todo_service.delete(todo_id)
    else:
        return f"Sub-command '{sub_command}' tidak dikenal. Ketik /help untuk lihat opsi."


def handle_note_command(args: str, note_service: NoteService) -> str:
    """Parse dan jalankan sub-command /note. Contoh args: 'add Judul | isi catatan', 'view 2'."""
    parts = args.strip().split(maxsplit=1)
    if not parts:
        return "Format salah. Contoh: /note add Judul | Isi catatan"

    sub_command = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if sub_command == "add":
        if "|" not in rest:
            return "Format salah. Pisahkan judul dan isi dengan '|'. Contoh: /note add Resep Nasi Goreng | Bawang, nasi, kecap..."
        title, content = rest.split("|", maxsplit=1)
        return note_service.add(title, content)
    elif sub_command == "list":
        return note_service.list()
    elif sub_command == "search":
        return note_service.search(rest)
    elif sub_command in ("view", "delete"):
        if not rest.strip().isdigit():
            return f"Butuh nomor id yang valid. Contoh: /note {sub_command} 2"
        note_id = int(rest.strip())
        return note_service.view(note_id) if sub_command == "view" else note_service.delete(note_id)
    else:
        return f"Sub-command '{sub_command}' tidak dikenal. Ketik /help untuk lihat opsi."


def main():
    memory = ConversationMemory()
    todo_service = TodoService()
    note_service = NoteService()
    # Reminder sengaja TIDAK di-expose ke Brain di CLI, karena CLI tidak
    # bisa push notification - kalau user minta reminder lewat obrolan,
    # Brain akan otomatis kasih tahu supaya pakai versi Telegram/Discord.
    brain = Brain(memory, services={"todo": todo_service, "note": note_service})

    print("=" * 50)
    print(" Asisten Bot siap membantu! Ketik /help untuk bantuan.")
    print(f" Model aktif: {MODEL_NAME} (base_url: {LLM_BASE_URL})")
    if memory.get_history():
        jumlah = len(memory.get_history())
        print(f" (Melanjutkan sesi sebelumnya - {jumlah} pesan dimuat dari riwayat)")
    print("=" * 50)

    while True:
        try:
            user_input = input("\nKamu: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSampai jumpa! 👋")
            break

        if not user_input:
            continue

        if user_input == "/exit":
            print("Sampai jumpa! 👋")
            break
        elif user_input == "/help":
            print(HELP_TEXT)
            continue
        elif user_input == "/reset":
            memory.reset()
            print("Riwayat percakapan sudah dihapus permanen dari database.")
            continue
        elif user_input.startswith("/todo"):
            args = user_input[len("/todo"):].strip()
            print(handle_todo_command(args, todo_service))
            continue
        elif user_input.startswith("/note"):
            args = user_input[len("/note"):].strip()
            print(handle_note_command(args, note_service))
            continue
        elif user_input.startswith("/remind"):
            print(
                "Fitur reminder cuma tersedia di versi Telegram (butuh push notification).\n"
                "Jalankan: python -m interfaces.telegram_bot"
            )
            continue

        reply = brain.think(user_input)
        print(f"\nBot: {reply}")


if __name__ == "__main__":
    main()