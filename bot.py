import os
import logging
import unicodedata
import asyncio
import threading
import requests
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket
import sqlite3
import re
import difflib
from datetime import datetime, timedelta

# Configuração de logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Teste de DNS (apenas informativo)
try:
    ip = socket.gethostbyname("api.telegram.org")
    print("api.telegram.org resolve para:", ip)
except Exception as e:
    logger.error(f"Erro ao resolver api.telegram.org: {e}")

# --- Servidor FastAPI para manter o Space "Running"
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

async def start_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=7860)
    server = uvicorn.Server(config)
    await server.serve()

# --- Configuração do Userbot
load_dotenv()

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
            timeout=10,
            verify=True
        )
        if r.status_code != 200:
            logger.warning(f"Falha ao enviar para Vercel: HTTP {r.status_code} → {r.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
    except requests.Timeout:
        logger.error("Tempo limite excedido ao enviar notificação")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar notificação")
    except Exception as e:
        logger.error(f"Erro ao enviar push para Vercel: {e}")

# --- Funções de persistência ---
def init_db():
    """Inicializa o banco de dados SQLite"""
    conn = sqlite3.connect('termos.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS termos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termo TEXT UNIQUE NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def salvar_termos_db(termos):
    """Salva os termos no banco de dados SQLite"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        for termo in termos:
            try:
                cursor.execute("INSERT OR IGNORE INTO termos (termo) VALUES (?)", (termo,))
            except sqlite3.IntegrityError:
                pass  # Termo já existe
        conn.commit()
        conn.close()
        logger.info(f"Termos salvos no banco: {termos}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar termos no banco: {e}")
        return False

def carregar_termos_db():
    """Carrega os termos do banco de dados SQLite"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute("SELECT termo FROM termos ORDER BY criado_em")
        termos = [row[0] for row in cursor.fetchall()]
        conn.close()
        logger.info(f"Termos carregados do banco: {termos}")
        return termos
    except Exception as e:
        logger.error(f"Erro ao carregar termos do banco: {e}")
        return []

def adicionar_termo_db(termo):
    """Adiciona um único termo ao banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO termos (termo) VALUES (?)", (termo,))
        conn.commit()
        conn.close()
        logger.info(f"Termo adicionado: {termo}")
        return True
    except Exception as e:
        logger.error(f"Erro ao adicionar termo: {e}")
        return False

def remover_termo_db(termo):
    """Remove um termo do banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM termos WHERE termo=?", (termo,))
        conn.commit()
        conn.close()
        logger.info(f"Termo removido: {termo}")
        return True
    except Exception as e:
        logger.error(f"Erro ao remover termo: {e}")
        return False

# Inicializa o banco de dados
init_db()

# --- Cache com TTL ---
mensagens_processadas = {}

def is_message_expired(msg_id, ttl_minutes=10):
    """Verifica se a mensagem expirou"""
    if msg_id not in mensagens_processadas:
        return True
    timestamp = mensagens_processadas[msg_id]
    return datetime.now() > timestamp + timedelta(minutes=ttl_minutes)

def add_message_to_cache(msg_id):
    """Adiciona mensagem ao cache"""
    mensagens_processadas[msg_id] = datetime.now()

# --- Carrega os termos ao iniciar ---
TERMOS = carregar_termos_db()
if not TERMOS:
    # Se não tiver carregado do banco, usa os valores do .env
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    logger.info("Usando termos do ambiente (.env): " + str(TERMOS))
    salvar_termos_db(TERMOS)  # Salva para criar o banco inicial

logger.info(f"Termos carregados ao iniciar: {TERMOS}")

# --- Funções auxiliares ---
def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def enviar_notificacao_push(html_texto):
    try:
        response = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10,
            verify=True
        )
        if response.status_code != 200:
            logger.warning(f"Falha ao enviar para Vercel: HTTP {response.status_code} → {response.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
    except requests.Timeout:
        logger.error("Timeout ao enviar notificação")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar notificação")
    except Exception as e:
        logger.error(f"Erro ao enviar push para Vercel: {e}")

# --- Handler principal ---
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
    if not is_message_expired(event.id):
        return
    add_message_to_cache(event.id)

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    msg_limpa = normalize(msg_original)

    # Usa regex para busca mais precisa
    for termo in TERMOS:
        termo_limpo = normalize(termo)
        if termo_limpo and re.search(r'\b' + re.escape(termo_limpo) + r'\b', msg_limpa):
            logger.info(f"--> Termo '{termo}' encontrado em: {chat_title}")

            # Link direto para a mensagem
            try:
                if hasattr(event.message, 'link') and event.message.link:
                    msg_link = event.message.link
                else:
                    msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
                    if getattr(chat, "username", None):
                        msg_link = f"https://t.me/{chat.username}/{event.message.id}"
            except Exception as e:
                logger.warning(f"Erro ao gerar link: {e}")
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
        logger.error(f"Erro no comando /settermos: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    try:
        text = event.message.message
        if len(text) < 9:  # "/addtermo " tem 9 caracteres
            await event.reply("Use: /addtermo termo")
            return
            
        novo_termo = text[9:].strip()
        if not novo_termo:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        global TERMOS
        if novo_termo not in TERMOS:
            TERMOS.append(novo_termo)
            if salvar_termos_arquivo(TERMOS):
                await event.reply(f"Termo '{novo_termo}' adicionado com sucesso!\n\n{', '.join(TERMOS)}")
            else:
                await event.reply("Erro ao salvar os termos.")
        else:
            await event.reply(f"Termo '{novo_termo}' já está na lista.")
            
    except Exception as e:
        logger.error(f"Erro no comando /addtermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/deltermo'))
async def del_termo_handler(event):
    try:
        text = event.message.message
        if len(text) < 9:  # "/deltermo " tem 9 caracteres
            await event.reply("Use: /deltermo termo")
            return
            
        termo_remover = text[9:].strip()
        if not termo_remover:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        global TERMOS
        if termo_remover in TERMOS:
            TERMOS.remove(termo_remover)
            if salvar_termos_arquivo(TERMOS):
                await event.reply(f"Termo '{termo_remover}' removido com sucesso!\n\n{', '.join(TERMOS)}")
            else:
                await event.reply("Erro ao salvar os termos.")
        else:
            await event.reply(f"Termo '{termo_remover}' não encontrado na lista.")
            
    except Exception as e:
        logger.error(f"Erro no comando /deltermo: {e}")
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
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.start(phone=PHONE_NUMBER)
        print("Userbot rodando! Monitorando tudo que você recebe...")

        # TESTE DIRETO: dispara uma notificação ao iniciar
        enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

        await client.run_until_disconnected()
    except ConnectionError:
        print("Falha na conexão com Telegram. Tentando reconectar...")
        await asyncio.sleep(5)
        await main()  # Tenta reconectar recursivamente
    except FloodWaitError as e:
        print(f"Rate limit excedido, aguardando {e.seconds} segundos...")
        await asyncio.sleep(e.seconds)
        await main()

if __name__ == "__main__":
    asyncio.run(main())
