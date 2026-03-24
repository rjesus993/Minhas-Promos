# bot.py
import asyncio
import logging
import requests
import time

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from config import API_ID, API_HASH, SESSION, PHONE_NUMBER
from utils import normalize, contains_term
from db import save_terms, load_terms, add_term, remove_term
from server import start_server

# Logging
logger = logging.getLogger(__name__)

# Cache de mensagens
mensagens_processadas = {}
CACHE_TTL = 30 * 60  # 30 minutos

# Carrega termos
TERMOS = load_terms()
if not TERMOS:
    from config import TERMOS_ENV
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    save_terms(TERMOS)

# Cliente Telegram
client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)


async def send_notification_push(html_texto: str):
    try:
        r = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10
        )
        if r.status_code != 200:
            logger.error(f"Falha ao enviar push: {r.status_code} → {r.text}")
    except Exception as e:
        logger.error(f"Erro ao enviar push: {e}")


async def process_message(event):
    if event.out:
        return

    try:
        sender = await event.get_sender()
        if getattr(sender, "username", "").lower() == "rjesus99_bot":
            return
    except:
        pass

    now = time.time()
    mensagens_processadas[event.id] = now

    for msg_id, ts in list(mensagens_processadas.items()):
        if now - ts > CACHE_TTL:
            mensagens_processadas.pop(msg_id, None)

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")
    msg_limpa = normalize(msg_original)

    for termo in TERMOS:
        if contains_term(msg_limpa, termo):

            try:
                msg_link = event.message.get_url()
            except:
                msg_link = "Link indisponível"

            alerta_html = (
                "🚨 <b>ACHEI UMA OFERTA!</b> 🚨\n\n"
                f"<b>Termo:</b> <code>{termo}</code>\n"
                f"<b>Onde:</b> {chat_title}\n"
                f"<b>Link:</b> {msg_link}\n\n"
                "-----------\n"
                f"{msg_original[:200]}..."
            )

            try:
                await client.send_message("me", alerta_html, parse_mode="html")
            except Exception as e:
                logger.error(f"Erro ao enviar para Saved Messages: {e}")

            await send_notification_push(alerta_html)
            break


@client.on(events.NewMessage)
async def handler(event):
    try:
        await process_message(event)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logger.error(f"Erro no handler: {e}")


@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("Monitorando termos: " + ", ".join(TERMOS))


@client.on(events.NewMessage(pattern='/termos'))
async def list_termos_handler(event):
    if not TERMOS:
        await event.reply("Nenhum termo configurado.")
    else:
        await event.reply("Termos atuais:\n" + "\n".join(f"• {t}" for t in TERMOS))


@client.on(events.NewMessage(pattern='/settermos'))
async def set_termos_handler(event):
    try:
        novos = [t.strip() for t in event.message.message[10:].split(",") if t.strip()]
        if novos:
            global TERMOS
            TERMOS = novos
            save_terms(TERMOS)
            await event.reply("Termos atualizados:\n" + ", ".join(TERMOS))
    except Exception as e:
        logger.error(e)


@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    termo = event.message.message[9:].strip()
    if termo:
        if add_term(termo):
            TERMOS.append(termo)
            await event.reply(f"Termo '{termo}' adicionado.")
        else:
            await event.reply("Erro ao adicionar termo.")


@client.on(events.NewMessage(pattern='/deltermo'))
async def del_termo_handler(event):
    termo = event.message.message[9:].strip()
    if termo:
        if remove_term(termo):
            global TERMOS
            TERMOS = load_terms()
            await event.reply(f"Termo '{termo}' removido.")
        else:
            await event.reply("Erro ao remover termo.")


@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    global TERMOS
    TERMOS = load_terms()
    await event.reply("Termos resetados.")


async def main():
    try:
        await client.connect()
        logger.info("Userbot conectado com sucesso!")

        start_server()

        await send_notification_push("<b>Bot iniciado no Hugging Face</b>")

        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Erro no main: {e}")


if __name__ == "__main__":
    asyncio.run(main())