import os
import logging
import unicodedata
import asyncio
import threading
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
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

# Leitura de env/secrets
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "")
TERMOS = os.getenv("TERMOS", "").split(",") if os.getenv("TERMOS") else []
TELEGRAM_PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")  # se você tiver 2FA
PHONE_CODE = os.getenv("PHONE_CODE")  # só na primeira autenticação

# Nome de sessão exclusivo para o Hugging Face
SESSION_NAME = "sessao_hf.session"

# Validação mínima
if not API_ID or not API_HASH or not PHONE_NUMBER:
    raise ValueError("API_ID, API_HASH e PHONE_NUMBER devem estar definidos nos Secrets.")

# Função de limpeza de texto
def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

print("Conectando como usuário...")
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

@client.on(events.NewMessage)
async def handler(event):
    if event.out:
        return

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

# Função de code_callback que lê o código dos Secrets
def code_callback():
    code = os.getenv("PHONE_CODE")
    if not code:
        # Se PHONE_CODE não estiver definido, falamos claramente no log
        raise RuntimeError(
            "PHONE_CODE não definido. Coloque o código recebido por SMS/Telegram como Secret 'PHONE_CODE' e reinicie."
        )
    # Opcional: remover o código após usar (apenas em memória; para remover do Secret, faça manualmente no painel)
    return code

async def main():
    # Fluxo de autenticação
    try:
        # Tenta iniciar com code_callback; só será usado na primeira vez
        await client.start(phone=PHONE_NUMBER, code_callback=code_callback)
    except SessionPasswordNeededError:
        # Caso você tenha 2FA (senha do Telegram)
        if not TELEGRAM_PASSWORD:
            raise RuntimeError("Sua conta tem 2FA. Defina o Secret 'TELEGRAM_PASSWORD' para completar o login.")
        await client.sign_in(password=TELEGRAM_PASSWORD)
    except RuntimeError as e:
        # Falha de code_callback (provavelmente PHONE_CODE ausente)
        print(f"Falha no code_callback: {e}")
        # Tentativa de conectar sem code_callback, útil se a sessão já existe
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("Sessão não autorizada e PHONE_CODE ausente. Configure o Secret 'PHONE_CODE' e reinicie.")

    # Se chegou aqui, sessão está criada/valida — persiste no filesystem
    print("Userbot autenticado. Monitorando mensagens...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
