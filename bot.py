# bot.py
import asyncio
import logging
import time
from datetime import datetime

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from config import API_ID, API_HASH, SESSION, PHONE_NUMBER
from utils import normalize, contains_term, extract_price, extract_product_name
from db import save_terms, load_terms, add_term, remove_term, get_term_history, get_db
from push_queue import enqueue_push_html

logger = logging.getLogger(__name__)

mensagens_processadas = {}
CACHE_TTL = 30 * 60

_last_backup_ts = 0.0

TERMOS = load_terms()
if not TERMOS:
    from config import TERMOS_ENV
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    save_terms(TERMOS)

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)


def _maybe_backup() -> None:
    global _last_backup_ts
    now = time.time()
    if now - _last_backup_ts >= 3600:
        try:
            db = get_db()
            dest = f"data/backups/auto_{int(now)}.db"
            db.backup(dest_path=dest, compress=False)
            _last_backup_ts = now
        except Exception as e:
            logger.error(f"Auto-backup failed: {e}")


async def send_notification_push(html_texto: str):
    try:
        enqueue_push_html(html_texto, optimistic=True)
    except Exception as e:
        logger.error(f"Erro ao enfileirar push: {e}")


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
            preco = extract_price(msg_original)
            preco_formatado = preco if preco else "Não identificado"
            produto = extract_product_name(msg_original)
            alerta_html = (
                "<b>🔥 Oferta encontrada!</b>\n\n"
                f"<b>🧩 Termo:</b> <code>{termo}</code>\n"
                f"<b>💰 Preço:</b> {preco_formatado}\n"
                f"<b>📦 Produto:</b> {produto}\n"
                f"<b>🔗 Link:</b> {msg_link}\n\n"
                "<b>📝</b>\n"
                f"{msg_original[:400]}"
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
            _maybe_backup()
            await event.reply("Termos atualizados:\n" + ", ".join(TERMOS))
    except Exception as e:
        logger.error(e)


@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    termo = event.message.message[9:].strip()
    if termo:
        if add_term(termo):
            TERMOS.append(termo)
            _maybe_backup()
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
            _maybe_backup()
            await event.reply(f"Termo '{termo}' removido.")
        else:
            await event.reply("Erro ao remover termo.")


@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    global TERMOS
    TERMOS = load_terms()
    await event.reply("Termos resetados.")
@client.on(events.NewMessage(pattern='/historico'))
async def historico_handler(event):
    history = get_term_history(limit=20)
    if not history:
        await event.reply("Sem histórico.")
        return
    lines = []
    for entry in history:
        term = entry.get("term", "")
        action = entry.get("action", "")
        ts = entry.get("timestamp", "")
        action_label = "A" if action == "added" else ("D" if action == "removed" else "?")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                ts_fmt = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts_fmt = str(ts)
        else:
            ts_fmt = "???"
        lines.append(f"{ts_fmt} | [{action_label}] | {term}")
    reply = "Últimas 20 entradas:\n" + "\n".join(lines)
    await event.reply(reply)


async def main():
    try:
        await client.connect()
        logger.info("Userbot conectado com sucesso!")
        await send_notification_push("<b>Bot iniciado no dispositivo Android</b>")
        await client.run_until_disconnected()
    except Exception as e:
        logger.error(f"Erro no main: {e}")


if __name__ == "__main__":
    asyncio.run(main())