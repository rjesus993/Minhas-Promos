# config.py
import os
from dotenv import load_dotenv

load_dotenv()

required_vars = ['API_ID', 'API_HASH', 'TELEGRAM_SESSION']
for var in required_vars:
    if not os.getenv(var):
        raise ValueError(f"Variável de ambiente {var} não definida")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION = os.getenv("TELEGRAM_SESSION")

# Variáveis opcionais
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DESTINO_ID = os.getenv("DESTINO_ID", "").strip()

try:
    DESTINO_ID = int(DESTINO_ID)
except ValueError:
    pass

TERMOS_ENV = os.getenv("TERMOS", "")
TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]