import os
import logging
import unicodedata
import asyncio
import threading
import requests  # IMPORTANTE
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket
from datetime import datetime

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

# TERMOS com strip para evitar falhas por espaços
TERMOS_ENV = os.getenv("TERMOS", "")
TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]

# Configurações de Notificação do Bot (usadas pelo endpoint no Vercel)
BOT_TOKEN = os.getenv("BOT_TOKEN")
DESTINO_ID_ENV = os.getenv("DESTINO_ID", "").strip()
try:
    DESTINO_ID = int(DESTINO_ID_ENV)
except ValueError:
    DESTINO_ID = DESTINO_ID_ENV  # Permite usar @username

# --- Persistência de Termos ---
def salvar_termos():
    """Salva os termos em um arquivo para persistência"""
    try:
        with open("termos_config.txt", "w", encoding="utf-8") as f:
            f.write(",".join(TERMOS))
    except Exception as e:
        print(f"Erro ao salvar termos: {e}")

def carregar_termos_do_arquivo():
    """Carrega os termos do arquivo"""
    global TERMOS
    try:
        if os.path.exists("termos_config.txt"):
            with open("termos_config.txt", "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    TERMOS = [t.strip() for t in content.split(",") if t.strip()]
    except Exception as e:
        print(f"Erro ao carregar termos: {e}")

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
    # Ignora mensagens que você mesmo enviou (evita loop)
    if event.out:
        return

    # Ignora mensagens vindas especificamente do rjesus99_bot
    try:
        sender = await event.get_sender()
        if getattr(sender, "username", "") and sender.username.lower() == "rjesus99_bot":
            return
    except Exception:
        pass

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
                    msg_link = f"https://t.me/c/{str(chat.id)[4:]}/{event.message.id}"
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

            # Garante apenas uma notificação por mensagem
            break

# --- Comandos do Telegram para gerenciamento de termos ---
@client.on(events.NewMessage(pattern='/settermos'))
async def set_termos_handler(event):
    if event.out:
        return
    
    try:
        message_text = event.message.message
        novos_termos_str = message_text[10:].strip()  # Remove '/settermos '
        
        if not novos_termos_str:
            await event.reply("Por favor, envie os termos separados por vírgula. Exemplo: /settermos notebook,iphone,smartphone")
            return
            
        global TERMOS
        TERMOS = [t.strip() for t in novos_termos_str.split(",") if t.strip()]
        
        salvar_termos()
        
        await event.reply(f"Termos atualizados com sucesso! Termos atuais: {', '.join(TERMOS)}")
        
    except Exception as e:
        print(f"Erro ao processar /settermos: {e}")
        await event.reply("Erro ao atualizar termos. Verifique o formato.")

@client.on(events.NewMessage(pattern='/gettermos'))
async def get_termos_handler(event):
    if event.out:
        return
    
    try:
        termos_str = ", ".join(TERMOS) if TERMOS else "Nenhum termo configurado"
        await event.reply(f"Termos atuais: {termos_str}")
    except Exception as e:
        print(f"Erro ao processar /gettermos: {e}")
        await event.reply("Erro ao recuperar termos.")

@client.on(events.NewMessage(pattern='/resettermos'))
async def reset_termos_handler(event):
    if event.out:
        return
    
    try:
        global TERMOS
        TERMOS = []
        salvar_termos()
        await event.reply("Termos resetados com sucesso!")
    except Exception as e:
        print(f"Erro ao processar /resettermos: {e}")
        await event.reply("Erro ao resetar termos.")

async def main():
    await client.start(phone=PHONE_NUMBER)
    print("Userbot rodando! Monitorando tudo que você recebe...")

    # Carrega os termos do arquivo ao iniciar
    carregar_termos_do_arquivo()
    print(f"Termos carregados: {TERMOS}")

    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
