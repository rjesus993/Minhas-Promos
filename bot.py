# bot.py
import os
import logging
import unicodedata
import asyncio
import threading
import requests
import sqlite3
import re
import difflib
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket
from fastapi import FastAPI
import uvicorn
from contextlib import asynccontextmanager

# Configuração do logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Teste de DNS (apenas informativo)
try:
    ip = socket.gethostbyname("api.telegram.org")
    logger.info("api.telegram.org resolve para: %s", ip)
except Exception as e:
    logger.error("Erro ao resolver api.telegram.org: %s", e)

# --- Gerenciamento de Sessão ---
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# --- Configurações de Notificação do Bot ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DESTINO_ID_ENV = os.getenv("DESTINO_ID", "").strip()
try:
    DESTINO_ID = int(DESTINO_ID_ENV)
except ValueError:
    DESTINO_ID = DESTINO_ID_ENV

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
        CREATE TABLE IF NOT EXISTS perfis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def salvar_termos_db(termos):
    """Salva os termos no banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM termos')  # Limpa todos os termos
        for termo in termos:
            cursor.execute('INSERT OR IGNORE INTO termos (termo) VALUES (?)', (termo,))
        conn.commit()
        conn.close()
        logger.info("Termos salvos no banco de dados: %s", termos)
        return True
    except Exception as e:
        logger.error("Erro ao salvar termos no banco de dados: %s", e)
        return False

def carregar_termos_db():
    """Carrega os termos do banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        cursor.execute('SELECT termo FROM termos ORDER BY created_at ASC')
        rows = cursor.fetchall()
        conn.close()
        termos = [row[0] for row in rows]
        logger.info("Termos carregados do banco de dados: %s", termos)
        return termos
    except Exception as e:
        logger.error("Erro ao carregar termos do banco de dados: %s", e)
        return []

# Inicializa o banco de dados
init_db()

# --- Normalização ---
def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def buscar_termo_regex(texto, termo):
    """Busca termo usando regex para evitar falsos positivos"""
    try:
        # Verifica se o termo é uma regex válida
        re.compile(termo)
        return bool(re.search(termo, texto, re.IGNORECASE))
    except re.error:
        # Se não for regex válido, faz busca normal
        return termo.lower() in texto.lower()

def buscar_termo_aproximado(texto, termo):
    """Busca termo com correspondência aproximada"""
    # Calcula similaridade usando difflib
    similarity = difflib.SequenceMatcher(None, termo.lower(), texto.lower()).ratio()
    return similarity > 0.7

# --- Links de Mensagens ---
def get_message_link(event, chat):
    """Gera link confiável para a mensagem"""
    try:
        # Tenta usar o método do Telethon para obter o link
        if hasattr(event.message, 'to_dict') and event.message.to_dict().get('id'):
            return f"https://t.me/c/{chat.id}/{event.message.id}"
        else:
            return "Link indisponível"
    except Exception as e:
        logger.warning("Erro ao gerar link da mensagem: %s", e)
        return "Link indisponível"

# --- Cache com TTL ---
class TTLCache:
    def __init__(self, ttl_minutes=5):
        self.cache = {}
        self.ttl = timedelta(minutes=ttl_minutes)
    
    def add(self, key):
        self.cache[key] = datetime.now()
    
    def is_valid(self, key):
        if key not in self.cache:
            return False
        return datetime.now() - self.cache[key] < self.ttl
    
    def cleanup(self):
        now = datetime.now()
        expired_keys = [key for key, timestamp in self.cache.items() 
                       if now - timestamp >= self.ttl]
        for key in expired_keys:
            del self.cache[key]

# Cache para mensagens processadas
mensagens_processadas = TTLCache(ttl_minutes=5)

# --- Servidor FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicialização do servidor
    logger.info("Iniciando servidor FastAPI...")
    
    # Inicia o servidor em background
    server_config = uvicorn.Config(app, host="0.0.0.0", port=7860)
    server = uvicorn.Server(server_config)
    
    # Cria thread para o servidor
    server_thread = threading.Thread(target=server.run)
    server_thread.daemon = True
    server_thread.start()
    
    yield
    
    # Limpeza ao encerrar
    logger.info("Servidor FastAPI encerrado.")

app = FastAPI(lifespan=lifespan)

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

# --- Configuração do Userbot ---
client = TelegramClient("minha_sessao", API_ID, API_HASH)

# --- Funções de notificação ---
def enviar_notificacao_push(html_texto):
    """Envia notificação via Vercel com tratamento de erros"""
    try:
        response = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10,
            verify=True  # Garante SSL
        )
        
        if response.status_code != 200:
            logger.warning(f"Falha ao enviar para Vercel: HTTP {response.status_code} → {response.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
            
    except requests.Timeout:
        logger.error("Timeout ao enviar notificação para Vercel")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar notificação para Vercel")
    except Exception as e:
        logger.error(f"Erro desconhecido ao enviar push para Vercel: {e}")

# --- Comandos do bot ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    await event.reply("Olá! Estou monitorando mensagens para os termos: " + ", ".join(carregar_termos_arquivo()))

@client.on(events.NewMessage(pattern='/termos'))
async def list_termos_handler(event):
    termos = carregar_termos_arquivo()
    if not termos:
        await event.reply("Nenhum termo configurado.")
    else:
        await event.reply("Termos atuais:\n" + "\n".join(f"• {t}" for t in termos))

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
            
        salvar_termos_arquivo(novos_termos)
        await event.reply(f"Termos atualizados com sucesso!\n\n{', '.join(novos_termos)}")
        
    except Exception as e:
        logger.error(f"Erro no comando /settermos: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    try:
        text = event.message.message
        if len(text) < 9:
            await event.reply("Use: /addtermo termo")
            return
            
        novo_termo = text[9:].strip()
        
        if not novo_termo:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        # Carrega termos atuais
        termos = carregar_termos_arquivo()
        
        # Adiciona se não existir
        if novo_termo not in termos:
            termos.append(novo_termo)
            salvar_termos_arquivo(termos)
            await event.reply(f"Termo '{novo_termo}' adicionado com sucesso!")
        else:
            await event.reply(f"Termo '{novo_termo}' já existe na lista.")
            
    except Exception as e:
        logger.error(f"Erro no comando /addtermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/deltermo'))
async def del_termo_handler(event):
    try:
        text = event.message.message
        if len(text) < 9:
            await event.reply("Use: /deltermo termo")
            return
            
        termo_remover = text[9:].strip()
        
        if not termo_remover:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        # Carrega termos atuais
        termos = carregar_termos_arquivo()
        
        # Remove se existir
        if termo_remover in termos:
            termos.remove(termo_remover)
            salvar_termos_arquivo(termos)
            await event.reply(f"Termo '{termo_remover}' removido com sucesso!")
        else:
            await event.reply(f"Termo '{termo_remover}' não encontrado na lista.")
            
    except Exception as e:
        logger.error(f"Erro no comando /deltermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    salvar_termos_arquivo([])
    await event.reply("Termos resetados para o padrão.")

# --- Funções auxiliares ---
def normalize(text):
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def salvar_termos_arquivo(termos):
    """Salva os termos em um arquivo txt"""
    try:
        with open("termos_config.txt", "w", encoding="utf-8") as f:
            f.write(",".join(termos))
        logger.info(f"Termos salvos no arquivo: {termos}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar termos no arquivo: {e}")
        return False

def carregar_termos_arquivo():
    """Carrega os termos do arquivo txt"""
    try:
        if os.path.exists("termos_config.txt"):
            with open("termos_config.txt", "r", encoding="utf-8") as f:
                conteudo = f.read().strip()
                if conteudo:
                    termos = [t.strip() for t in conteudo.split(",") if t.strip()]
                    logger.info(f"Termos carregados do arquivo: {termos}")
                    return termos
                else:
                    logger.info("Arquivo vazio, usando valores iniciais")
                    return []
        else:
            logger.info("Arquivo termos_config.txt não encontrado")
            return []
    except Exception as e:
        logger.error(f"Erro ao carregar termos do arquivo: {e}")
        return []

async def main():
    # Configura logging
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            logging.FileHandler("bot.log"),
            logging.StreamHandler()
        ]
    )
    
    logger = logging.getLogger(__name__)
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.start(phone=PHONE_NUMBER)
            
    except Exception as e:
        logger.error(f"Falha ao conectar ao Telegram: {e}")
        return
    
    # Inicia o servidor FastAPI
    loop = asyncio.get_event_loop()
    server_task = loop.create_task(start_server())
    
    logger.info("Userbot rodando! Monitorando tudo que você recebe...")
    
    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")
    
    await client.run_until_disconnected()

# --- Início do servidor FastAPI ---
def start_server():
    from fastapi import FastAPI
    import uvicorn
    
    app = FastAPI()
    
    @app.get("/")
    def read_root():
        return {"status": "running", "message": "Userbot ativo no Hugging Face"}
    
    uvicorn.run(app, host="0.0.0.0", port=7860)

if __name__ == "__main__":
    asyncio.run(main())
