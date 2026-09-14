# teste_minimo.py
import asyncio
import logging

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from config import API_ID, API_HASH, SESSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("teste")


async def main():
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()
    me = await client.get_me()
    logger.info("Logado: id=%s username=%s bot=%s", me.id, me.username, me.bot)

    @client.on(events.NewMessage)
    async def catch_all(event):
        logger.info(
            "[MSG] chat=%s sender=%s out=%s texto=%r",
            event.chat_id, event.sender_id, event.out,
            (event.message.message or "")[:100],
        )
        # Se for /ping, responde no mesmo chat
        if (event.message.message or "").strip() == "/ping":
            await client.send_message(event.chat_id, "pong!")

    logger.info("Escutando. Mande /ping em Saved Messages...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())