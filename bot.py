import os
import logging
import unicodedata
import asyncio
import threading
from telethon import TelegramClient, events
from dotenv import load_dotenv

# --- Servidor FastAPI para manter o Space "Running"
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

def start_server():
    uvicorn.run(app, host="0.0.0.0", port=7860)

# Inicia o servidor em paralelo (thread separada)
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
TERMOS = os.getenv("TERMOS").split(",")

def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

print("Conectando como usuário...")
client = TelegramClient("minha_sessao", API_ID, API_HASH)

@client.on(events.NewMessage)
async def handler(event):
    if event.out:  # ignora mensagens enviadas por você mesmo
        return

    msg_original = event.message.message
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    msg_limpa = normalize(msg_original)

    for termo in TERMOS:
        termo_limpo = normalize(termo)
        if termo_limpo in msg_limpa:
            print(f"--> Termo '{termo}' encontrado em: {chat_title}")

            try:
                msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
                if hasattr(chat, "username") and chat.username:
                    msg_link = f"https://t.me/{chat.username}/{event.message.id}"
            except Exception:
                msg_link = "Link indisponível"

            alerta = (
                f"🚨 **ACHEI UMA OFERTA!** 🚨\n\n"
                f"**Termo:** `{termo}`\n"
                f"**Onde:** {chat_title}\n"
                f"**Link:** [Ir para mensagem]({msg_link})\n\n"
                f"-----------\n"
                f"{msg_original[:200]}..."
            )

            await client.send_message("me", alerta)
            break

async def main():
    await client.start(phone=PHONE_NUMBER)
    print("Userbot rodando! Monitorando tudo que você recebe...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
