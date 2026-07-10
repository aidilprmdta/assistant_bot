"""
Konfigurasi aplikasi. Semua nilai sensitif diambil dari file .env
supaya tidak ter-hardcode di source code.

Catatan penting soal LLM provider:
Bot ini pakai library `openai` (bukan `anthropic`) supaya bisa dipakai
lewat gateway OpenAI-compatible seperti Agent Router (agentrouter.org),
yang menyediakan akses ke Claude/GPT/dll lewat satu API key & base URL.
Kalau nanti mau pakai API resmi Anthropic langsung, tinggal set
LLM_BASE_URL ke None/kosong dan pastikan model yang dipanggil memang
tersedia lewat endpoint OpenAI-compatible resmi Anthropic, atau kembali
pakai library `anthropic` (lihat versi lama core/brain.py di git history).
"""
import os
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://agentrouter.org/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "claude-opus-4-6")
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# Web search bawaan Anthropic TIDAK didukung lewat gateway OpenAI-compatible
# generik seperti Agent Router, jadi fitur ini nonaktif selama LLM_BASE_URL
# diarahkan ke gateway semacam itu. Variabel ini dibiarkan ada untuk
# kompatibilitas config lama, tapi tidak dipakai lagi di core/brain.py.
ENABLE_WEB_SEARCH = False
MAX_WEB_SEARCHES_PER_REPLY = int(os.getenv("MAX_WEB_SEARCHES_PER_REPLY", "3"))

if not LLM_API_KEY:
    raise ValueError(
        "LLM_API_KEY belum diatur. "
        "Copy .env.example jadi .env lalu isi API key kamu "
        "(misalnya dari agentrouter.org/console/token)."
    )