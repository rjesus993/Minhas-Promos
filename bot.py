import os
import logging
import unicodedata
import asyncio
import threading
import requests
from telethon import TelegramClient, events, ConnectionError, FloodWaitError
from dotenv import load_dotenv
import socket
import sqlite3
import re
import time
from datetime import datetime, timedelta

# Configure logging
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
    logger.info(f"api.telegram.org resolve para: {ip}")
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

# --- Funções de persistência ---
def init_db():
    """Inicializa o banco de dados SQLite"""
    conn = sqlite3.connect('termos.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS termos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termo TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def salvar_termos_db(termos):
    """Salva os termos no banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        # Limpar termos existentes e inserir novos
        cursor.execute('DELETE FROM termos')
        for termo in termos:
            cursor.execute('INSERT OR IGNORE INTO termos (termo) VALUES (?)', (termo,))
        conn.commit()
        conn.close()
        logger.info(f"Termos salvos no banco: {termos}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar termos no banco: {e}")
        return False

def carregar_termos_db():
    """Carrega os termos do banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute('SELECT termo FROM termos ORDER BY created_at')
        resultados = cursor.fetchall()
        conn.close()
        termos = [row[0] for row in resultados]
        logger.info(f"Termos carregados do banco: {termos}")
        return termos
    except Exception as e:
        logger.error(f"Erro ao carregar termos do banco: {e}")
        return []

def add_termo_db(termo):
    """Adiciona um termo ao banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO termos (termo) VALUES (?)', (termo,))
        conn.commit()
        conn.close()
        logger.info(f"Termo adicionado: {termo}")
        return True
    except Exception as e:
        logger.error(f"Erro ao adicionar termo: {e}")
        return False

def remove_termo_db(termo):
    """Remove um termo do banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM termos WHERE termo = ?', (termo,))
        conn.commit()
        conn.close()
        logger.info(f"Termo removido: {termo}")
        return True
    except Exception as e:
        logger.error(f"Erro ao remover termo: {e}")
        return False

# Inicializar o banco de dados
init_db()

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
            verify=True  # Garante SSL
        )
        if r.status_code != 200:
            logger.warning(f"Falha ao enviar para Vercel: HTTP {r.status_code} → {r.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
    except requests.Timeout:
        logger.error("Timeout ao enviar push para Vercel")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar push para Vercel")
    except Exception as e:
        logger.error(f"Erro ao enviar push para Vercel: {e}")

# --- Funções auxiliares ---
def contains_termo(text, termo):
    """Verifica se o termo está presente no texto usando regex para precisão"""
    try:
        # Usar word boundary para evitar correspondências parciais
        pattern = r'\b' + re.escape(termo) + r'\b'
        return bool(re.search(pattern, text, re.IGNORECASE))
    except Exception:
        # Se regex falhar, usar busca normal
        return termo.lower() in text.lower()

def get_message_link(event):
    """Gera link confiável para a mensagem"""
    try:
        # Tenta usar o método nativo do Telethon para obter o link
        if hasattr(event.message, 'link'):
            return event.message.link
        
        # Se não tiver link, monta manualmente
        chat = event.chat
        if chat and hasattr(chat, 'id'):
            chat_id = str(chat.id)
            if chat_id.startswith('-100'):
                # Grupo público
                msg_link = f"https://t.me/c/{chat_id[4:]}/{event.message.id}"
            else:
                # Chat privado ou canal
                msg_link = f"https://t.me/{chat.username}/{event.message.id}" if getattr(chat, 'username', None) else "Link indisponível"
            return msg_link
    except Exception as e:
        logger.warning(f"Erro ao gerar link da mensagem: {e}")
        return "Link indisponível"

# --- Cache com TTL ---
import time
from collections import OrderedDict

class TTLCache:
    def __init__(self, ttl=300):  # 5 minutos por padrão
        self.cache = OrderedDict()
        self.ttl = ttl
    
    def get(self, key):
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                # Move para o final (mais recente)
                self.cache.move_to_end(key)
                return value
            else:
                # Remove expirado
                del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, time.time())
        self.cache.move_to_end(key)
    
    def cleanup_expired(self):
        now = time.time()
        expired_keys = [k for k, (_, t) in self.cache.items() if now - t >= self.ttl]
        for key in expired_keys:
            del self.cache[key]

# Cache global
mensagens_processadas = TTLCache(ttl=300)  # 5 minutos

# --- Configuração do Userbot ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Carrega os termos ao iniciar
TERMOS = carregar_termos_db()
if not TERMOS:
    # Se não tiver carregado do banco, usa os valores do .env
    TERMOS_ENV = os.getenv("TERMOS", "")
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    salvar_termos_arquivo(TERMOS)  # Salva para criar o arquivo inicial

logger.info(f"Termos carregados ao iniciar: {TERMOS}")

# --- Cliente do Telegram ---
client = TelegramClient("minha_sessao", API_ID, API_HASH)

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
        if len(text) < 10:
            await event.reply("Use: /settermos termo1,termo2,termo3")
            return
            
        novos_termos = [t.strip() for t in text[10:].split(",") if t.strip()]
        
        if not novos_termos:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        global TERMOS
        TERMOS = novos_termos
        
        # Salva no banco de dados
        if salvar_termos_db(TERMOS):
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
        if len(text) < 9:
            await event.reply("Use: /addtermo novo_termo")
            return
            
        novo_termo = text[9:].strip()
        
        if not novo_termo:
            await event.reply("Termo inválido.")
            return
            
        global TERMOS
        if novo_termo not in TERMOS:
            TERMOS.append(novo_termo)
            
            # Salva no banco de dados
            if salvar_termos_db(TERMOS):
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
        if len(text) < 9:
            await event.reply("Use: /deltermo termo_a_remover")
            return
            
        termo_a_remover = text[9:].strip()
        
        if not termo_a_remover:
            await event.reply("Termo inválido.")
            return
            
        global TERMOS
        if termo_a_remover in TERMOS:
            TERMOS.remove(termo_a_remover)
            
            # Salva no banco de dados
            if salvar_termos_db(TERMOS):
                await event.reply(f"Termo '{termo_a_remover}' removido com sucesso!\n\n{', '.join(TERMOS)}")
            else:
                await event.reply("Erro ao salvar os termos.")
        else:
            await event.reply(f"Termo '{termo_a_remover}' não encontrado na lista.")
            
    except Exception as e:
        logger.error(f"Erro no comando /deltermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    global TERMOS
    # Recarrega os termos do banco ou do .env
    TERMOS = carregar_termos_db()
    if not TERMOS:
        TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    
    await event.reply(f"Termos resetados para: {', '.join(TERMOS)}")

# --- Funções de persistência com SQLite ---
import sqlite3
from datetime import datetime

def init_db():
    conn = sqlite3.connect("termos.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS termos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termo TEXT NOT NULL,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def salvar_termos_db(termos):
    try:
        init_db()
        conn = sqlite3.connect("termos.db")
        cursor = conn.cursor()
        
        # Limpa os termos antigos e insere novos
        cursor.execute("DELETE FROM termos")
        for termo in termos:
            cursor.execute("INSERT INTO termos (termo) VALUES (?)", (termo,))
        
        conn.commit()
        conn.close()
        logger.info(f"Termos salvos no banco de dados: {termos}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar termos no banco: {e}")
        return False

def carregar_termos_db():
    try:
        init_db()
        conn = sqlite3.connect("termos.db")
        cursor = conn.cursor()
        cursor.execute("SELECT termo FROM termos ORDER BY data_criacao ASC")
        rows = cursor.fetchall()
        conn.close()
        
        termos = [row[0] for row in rows]
        logger.info(f"Termos carregados do banco: {termos}")
        return termos
    except Exception as e:
        logger.error(f"Erro ao carregar termos do banco: {e}")
        return []

# --- Funções auxiliares ---
import re
import difflib

def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def buscar_termo_regex(texto, termo):
    """Busca termo usando regex para evitar falsos positivos."""
    try:
        pattern = re.compile(re.escape(termo), re.IGNORECASE)
        return bool(pattern.search(texto))
    except Exception as e:
        logger.warning(f"Erro na busca regex: {e}")
        return False

def buscar_termo_fuzzy(texto, termo):
    """Busca termo com correspondência aproximada."""
    try:
        ratio = difflib.SequenceMatcher(None, texto.lower(), termo.lower()).ratio()
        return ratio >= 0.7  # Ajustar limite conforme necessário
    except Exception as e:
        logger.warning(f"Erro na busca fuzzy: {e}")
        return False

# --- Configuração de Logging ---
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

# --- Servidor FastAPI ---
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

# --- Client do Telegram ---
from telethon import TelegramClient, events
import asyncio

client = TelegramClient("minha_sessao", API_ID, API_HASH)

# --- Cache de mensagens com TTL ---
import time
from collections import defaultdict

mensagens_processadas = {}  # id_mensagem: timestamp
CACHE_TTL_MINUTES = 5

def limpar_cache():
    now = time.time()
    expired_keys = [k for k, v in mensagens_processadas.items() if now - v > CACHE_TTL_MINUTES * 60]
    for key in expired_keys:
        del mensagens_processadas[key]

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
    except Exception as e:
        logger.warning(f"Erro ao verificar remetente: {e}")

    # Evita repetir a mesma mensagem
    msg_id = event.id
    if msg_id in mensagens_processadas:
        return
    
    # Limpa cache antigo
    limpar_cache()
    mensagens_processadas[msg_id] = time.time()

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    msg_limpa = normalize(msg_original)

    for termo in TERMOS:
        termo_limpo = normalize(termo)
        if termo_limpo and (buscar_termo_regex(msg_limpa, termo) or buscar_termo_fuzzy(msg_limpa, termo)):
            logger.info(f"--> Termo '{termo}' encontrado em: {chat_title}")

            # Link direto para a mensagem
            try:
                msg_link = event.message.link  # Usar link direto se disponível
                if not msg_link:
                    msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
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
            await event.reply("Termo inválido.")
            return
            
        global TERMOS
        if novo_termo not in TERMOS:
            TERMOS.append(novo_termo)
            if salvar_termos_arquivo(TERMOS):
                await event.reply(f"Termo '{novo_termo}' adicionado com sucesso!\n\n{', '.join(TERMOS)}")
            else:
                await event.reply("Erro ao salvar os termos.")
        else:
            await event.reply(f"Termo '{novo_termo}' já existe.")
            
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
            await event.reply("Termo inválido.")
            return
            
        global TERMOS
        if termo_remover in TERMOS:
            TERMOS.remove(termo_remover)
            if salvar_termos_arquivo(TERMOS):
                await event.reply(f"Termo '{termo_remover}' removido com sucesso!\n\n{', '.join(TERMOS)}")
            else:
                await event.reply("Erro ao salvar os termos.")
        else:
            await event.reply(f"Termo '{termo_remover}' não encontrado.")
            
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
        await client.start(phone=PHONE_NUMBER)
        print("Userbot conectado com sucesso!")
    except Exception as e:
        print(f"Falha na conexão: {e}")
        return

    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
