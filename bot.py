import os
import logging
import unicodedata
import asyncio
import threading
import requests
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket

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

# --- Funções de persistência ---
def salvar_termos_arquivo(termos):
    """Salva os termos em um arquivo txt"""
    try:
        with open("termos_config.txt", "w", encoding="utf-8") as f:
            f.write(",".join(termos))
        print(f"Termos salvos no arquivo: {termos}")
        return True
    except Exception as e:
        print(f"Erro ao salvar termos no arquivo: {e}")
        return False

def carregar_termos_arquivo():
    """Carrega os termos do arquivo txt"""
    try:
        if os.path.exists("termos_config.txt"):
            with open("termos_config.txt", "r", encoding="utf-8") as f:
                conteudo = f.read().strip()
                if conteudo:
                    termos = [t.strip() for t in conteudo.split(",") if t.strip()]
                    print(f"Termos carregados do arquivo: {termos}")
                    return termos
                else:
                    print("Arquivo vazio, usando valores iniciais")
                    return []
        else:
            print("Arquivo termos_config.txt não encontrado")
            return []
    except Exception as e:
        print(f"Erro ao carregar termos do arquivo: {e}")
        return []

print("Conectando como usuário...")
client = TelegramClient("minha_sessao", API_ID, API_HASH)

# cache global para evitar processar a mesma mensagem várias vezes
mensagens_processadas = set()

# --- Carrega os termos ao iniciar ---
TERMOS = carregar_termos_arquivo()
if not TERMOS:
    # Se não tiver carregado do arquivo, usa os valores do .env
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    print("Usando termos do ambiente (.env):", TERMOS)
    salvar_termos_arquivo(TERMOS)  # Salva para criar o arquivo inicial

print(f"Termos carregados ao iniciar: {TERMOS}")

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

# --- Comandos do bot ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("Olá! Estou monitorando mensagens para os termos: " + ", ".join(TERMOS))

@client.on(events.NewMessage(pattern='/termos'))
async def list_termos_handler(event):
    if not TERMOS:
        await event.reply("Nenhum termo configurado.")
    else:
        await event.reply("Termos atuais:\n" + "\n".join(f"• {t}" for t in TERMOS))

@client.on(events.NewMessage(pattern='/settermos'))
async def set_termos_handler(event):
    try:
        # Extrai os termos após o comando
        text = event.message.message
        if len(text) < 10:  # "/settermos " tem 10 caracteres
            await event.reply("Use: /settermos termo1,termo2,termo3")
            return
            
        novos_termos = [t.strip() for t in text[10:].split(",") if t.strip()]
        
        if not novos_termos:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        global TERMOS
        TERMOS = novos_termos
        
        # Salva no arquivo
        if salvar_termos_arquivo(TERMOS):
            await event.reply(f"Termos atualizados com sucesso!\n\n{', '.join(TERMOS)}")
        else:
            await event.reply("Erro ao salvar os termos.")
            
    except Exception as e:
        print(f"Erro no comando /settermos: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    global TERMOS
    # Recarrega os termos do arquivo ou do .env
    TERMOS = carregar_termos_arquivo()
    if not TERMOS:
        TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    
    await event.reply(f"Termos resetados para: {', '.join(TERMOS)}")

async def main():
    await client.start(phone=PHONE_NUMBER)
    print("Userbot rodando! Monitorando tudo que você recebe...")

    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
