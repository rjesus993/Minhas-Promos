import os
import logging
import unicodedata
import asyncio
import threading
import requests  # IMPORTANTE
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket

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

# TERMOS com strip para evitar falhas por espaços
TERMOS_ENV = os.getenv("TERMOS", "")
TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]

# Configurações de Notificação do Bot
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

print("Conectando como usuário...")
client = TelegramClient("minha_sessao", API_ID, API_HASH)  # sessão exclusiva no minha_sessao

# cache global para evitar processar a mesma mensagem várias vezes
mensagens_processadas = set()

@client.on(events.NewMessage)
async def handler(event):
    if event.out:
        return

# ignora mensagens enviadas para o próprio usuário (Saved Messages) 
    if event.is_private: 
        me = await client.get_me() 
        if event.chat_id == me.id: 
            return
    
    # evita repetir a mesma mensagem
    if event.id in mensagens_processadas:
        return
    mensagens_processadas.add(event.id)

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

            # Link direto para a mensagem
            try:
                msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
                if getattr(chat, "username", None):
                    msg_link = f"https://t.me/{chat.username}/{event.message.id}"
            except Exception:
                msg_link = "Link indisponível"

            # HTML seguro para Bot API
            alerta_html = (
                "🚨 <b>ACHEI UMA OFERTA!</b> 🚨\n\n"
                f"<b>Termo:</b> <code>{termo}</code>\n"
                f"<b>Onde:</b> {chat_title}\n"
                f"<b>Link:</b> {msg_link}\n\n"
                "-----------\n"
                f"{msg_original[:200]}..."
            )

            # 1. Log no seu próprio Saved Messages (via userbot)
            await client.send_message("me", alerta_html, parse_mode="html")

            # 2. Bot te avisar (Push Notification)
            enviar_notificacao_push(alerta_html)

            break

async def main():
    await client.start(phone=PHONE_NUMBER)
    print("Userbot rodando! Monitorando tudo que você recebe...")

    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
