import os
import logging
import unicodedata
import asyncio
import threading
import requests
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket
import json
import time

# Teste de DNS (apenas informativo)
try:
    ip = socket.gethostbyname("api.telegram.org")
    print("api.telegram.org resolve para:", ip)
except Exception as e:
    print("Erro ao resolver api.telegram.org:", e)

# --- Servidor FastAPI para manter o Space "Running"
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

def start_server():
    uvicorn.run(app, host="0.0.0.0", port=7860)

threading.Thread(target=start_server, daemon=True).start()

# --- Configuração do Userbot
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# --- Persistência de TERMOS ---
TERMOS_FILE = "termos_cache.json"

def load_terms():
    if os.path.exists(TERMOS_FILE):
        with open(TERMOS_FILE, "r") as f:
            return json.load(f)
    return []

def save_terms(terms):
    with open(TERMOS_FILE, "w") as f:
        json.dump(terms, f)

TERMOS_ENV = os.getenv("TERMOS", "")
if not TERMOS_ENV.strip():
    TERMOS = load_terms()
else:
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    save_terms(TERMOS)

# Configurações de Notificação do Bot (usadas pelo endpoint no Vercel)
BOT_TOKEN = os.getenv("BOT_TOKEN")
DESTINO_ID_ENV = os.getenv("DESTINO_ID", "").strip()
try:
    DESTINO_ID = int(DESTINO_ID_ENV)
except ValueError:
    DESTINO_ID = DESTINO_ID_ENV  # Permite usar @username

def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def enviar_notificacao_push(html_texto):
    try:
        r = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10
        )
        if r.status_code != 200:
            print(f"Falha ao enviar para Vercel: HTTP {r.status_code} → {r.text}")
        else:
            print("Notificação enviada via Vercel com sucesso.")
    except Exception as e:
        print(f"Erro ao enviar push para Vercel: {e}")

# --- Persistência de mensagens processadas ---
MESSAGES_FILE = "mensagens_processadas.json"

def load_processed():
    if os.path.exists(MESSAGES_FILE):
        with open(MESSAGES_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_processed(processed):
    with open(MESSAGES_FILE, "w") as f:
        json.dump(list(processed), f)

mensagens_processadas = load_processed()

print("Conectando como usuário...")
client = TelegramClient("minha_sessao", API_ID, API_HASH)

@client.on(events.NewMessage)
async def handler(event):
    if event.out:
        return

    try:
        sender = await event.get_sender()
        if getattr(sender, "username", "") and sender.username.lower() == "rjesus99_bot":
            return
    except Exception:
        pass

    if event.id in mensagens_processadas:
        return
    mensagens_processadas.add(event.id)
    save_processed(mensagens_processadas)

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    msg_limpa = normalize(msg_original)

    for termo in TERMOS:
        termo_limpo = normalize(termo)
        if termo_limpo and termo_limpo in msg_limpa:
            print(f"--> Termo '{termo}' encontrado em: {chat_title}")

            try:
                msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
                if getattr(chat, "username", None):
                    msg_link = f"https://t.me/c/{str(chat.id)[4:]}/{event.message.id}"
            except Exception:
                msg_link = "Link indisponível"

            alerta_html = (
                "🚨 <b>ACHEI UMA OFERTA!</b> 🚨\n\n"
                f"<b>Termo:</b> <code>{termo}</code>\n"
                f"<b>Onde:</b> {chat_title}\n"
                f"<b>Link:</b> {msg_link}\n\n"
                "-----------\n"
                f"{msg_original[:200]}..."
            )

            await client.send_message("me", alerta_html, parse_mode="html")
            enviar_notificacao_push(alerta_html)
            break

async def main():
    await client.start(phone=PHONE_NUMBER)
    print("Userbot rodando! Monitorando tudo que você recebe...")

    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    # Reinicialização automática a cada 6 horas (21600 segundos)
    def restart():
        print("Reiniciando o bot automaticamente...")
        os.execv(sys.executable, ['python'] + sys.argv)

    asyncio.create_task(asyncio.sleep(21600))
    restart()

    await client.run_until_disconnected()

if __name__ == "__main__":
    import sys
    asyncio.run(main())
