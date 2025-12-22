import os
import logging
import unicodedata
import asyncio
import re
import requests
from telethon import TelegramClient, events
from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn
from collections import OrderedDict

# === Config ===
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("userbot")

required = ["API_ID", "API_HASH", "PHONE_NUMBER", "TERMOS"]
missing = [v for v in required if not os.getenv(v)]
if missing:
    raise ValueError(f"Faltando variáveis: {missing}")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
TERMOS = [t.strip() for t in os.getenv("TERMOS", "").split(",") if t.strip()]
BOT_TOKEN = os.getenv("BOT_TOKEN")  # se usar no futuro
DESTINO_ID = os.getenv("DESTINO_ID", "").strip()

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "running", "message": "Userbot ativo"}

# === Utils ===
class LimitedSet:
    def __init__(self, max_size=10000):
        self.dict = OrderedDict()
        self.max_size = max_size
    def add(self, item):
        if item in self.dict:
            self.dict.move_to_end(item)
            return
        if len(self.dict) >= self.max_size:
            self.dict.popitem(last=False)
        self.dict[item] = None
    def __contains__(self, item):
        return item in self.dict

mensagens_processadas = LimitedSet(10000)

def normalize(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()

def enviar_notificacao_push(html_texto: str):
    try:
        r = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10
        )
        if r.ok:
            logger.info("Notificação enviada")
        else:
            logger.warning("Falha notificação: %s", r.text)
    except Exception as e:
        logger.error("Erro ao enviar push: %s", e)

# === Cliente Telegram ===
client = TelegramClient("sessao_userbot", API_ID, API_HASH)

@client.on(events.NewMessage)
async def handler(event):
    if event.out or event.is_edit or event.message.action:
        return

    me = await client.get_me()
    if event.chat_id == me.id:
        return

    try:
        sender = await event.get_sender()
        if getattr(sender, "bot", False):
            return
    except:
        pass

    if event.id in mensagens_processadas:
        return
    mensagens_processadas.add(event.id)

    text = event.message.message or ""
    if not text:
        return

    norm_text = normalize(text)
    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Privado")

    for termo in TERMOS:
        if normalize(termo) in norm_text:
            logger.info("Termo encontrado: %s em %s", termo, chat_title)

            # Link correto
            if hasattr(chat, "username") and chat.username:
                link = f"https://t.me/{chat.username}/{event.id}"
            else:
                chat_id_clean = str(chat.id).replace("-100", "")
                link = f"https://t.me/c/{chat_id_clean}/{event.id}"

            alerta = (
                "🚨 <b>ACHEI UMA OFERTA!</b> 🚨\n\n"
                f"<b>Termo:</b> <code>{termo}</code>\n"
                f"<b>Onde:</b> {chat_title}\n"
                f"<b>Link:</b> <a href='{link}'>Abrir mensagem</a>\n\n"
                "-----------\n"
                f"{text[:300]}..."
            )

            await client.send_message("me", alerta, parse_mode="html")
            enviar_notificacao_push(alerta)
            break

async def run_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=7860, log_level="error")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    await client.start(phone=PHONE_NUMBER)
    logger.info("Userbot iniciado e monitorando...")
    enviar_notificacao_push("<b>Userbot iniciado com sucesso!</b>")
    asyncio.create_task(run_server())
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())