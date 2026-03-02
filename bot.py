# bot.py
import os
import logging
import unicodedata
import asyncio
import threading
import requests
from telethon import TelegramClient, events
from dotenv import load_dotenv
import socket
import json
import sqlite3
import re
import difflib
from datetime import datetime, timedelta
from collections import defaultdict
import time

# Configuração do logging
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

# --- Configuração do Userbot ---
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
    """Inicializa o banco de dados SQLite para armazenar termos"""
    conn = sqlite3.connect('termos.db')
    cursor = conn.cursor()
    
    # Tabela para armazenar os termos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS termos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            termo TEXT UNIQUE NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Tabela para histórico de alterações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_termos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            acao TEXT NOT NULL,
            termos_antigos TEXT,
            termos_novos TEXT,
            data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def salvar_termos_db(termos):
    """Salva os termos no banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        
        # Registrar histórico
        cursor.execute("INSERT INTO historico_termos (acao, termos_novos) VALUES (?, ?)", 
                      ("ATUALIZAR", ",".join(termos)))
        
        # Limpar termos antigos
        cursor.execute("DELETE FROM termos")
        
        # Inserir novos termos
        for termo in termos:
            try:
                cursor.execute("INSERT INTO termos (termo) VALUES (?)", (termo,))
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
    """Carrega os termos do banco de dados"""
    try:
        conn = sqlite3.connect('termos.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT termo FROM termos ORDER BY criado_em")
        resultados = cursor.fetchall()
        termos = [row[0] for row in resultados]
        
        conn.close()
        logger.info(f"Termos carregados do banco: {termos}")
        return termos
    except Exception as e:
        logger.error(f"Erro ao carregar termos do banco: {e}")
        return []

# Inicializar o banco de dados
init_db()

def normalize(text):
    """Normaliza texto para comparação"""
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join([c for c in text if not unicodedata.combining(c)])
    return text

def contains_termo(text, termo, use_regex=False, use_fuzzy=False):
    """Verifica se o texto contém o termo com diferentes estratégias"""
    text_norm = normalize(text)
    termo_norm = normalize(termo)
    
    if use_regex:
        try:
            return bool(re.search(re.escape(termo_norm), text_norm))
        except re.error:
            pass
    
    if use_fuzzy:
        # Usar difflib para correspondência aproximada
        similarity = difflib.SequenceMatcher(None, termo_norm, text_norm).ratio()
        return similarity > 0.7  # Limite de similaridade
    
    # Busca por substring padrão
    return termo_norm in text_norm

def enviar_notificacao_push(html_texto):
    """Envia notificação via Vercel com tratamento de erros"""
    try:
        response = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10,
            verify=True  # Garantir SSL
        )
        
        if response.status_code != 200:
            logger.warning(f"Falha ao enviar para Vercel: HTTP {response.status_code} → {response.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
            
    except requests.Timeout:
        logger.error("Timeout ao tentar enviar notificação")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar notificação")
    except Exception as e:
        logger.error(f"Erro ao enviar push para Vercel: {e}")

# --- Funções auxiliares ---
class MessageCache:
    """Cache com TTL para evitar processamento repetido"""
    def __init__(self, ttl_minutes=10):
        self.cache = {}
        self.ttl = ttl_minutes * 60  # Converter minutos para segundos
        
    def add(self, message_id):
        import time
        self.cache[message_id] = time.time()
        
    def contains(self, message_id):
        import time
        if message_id in self.cache:
            if time.time() - self.cache[message_id] > self.ttl:
                del self.cache[message_id]
                return False
            return True
        return False
        
    def clear_expired(self):
        import time
        current_time = time.time()
        expired_keys = [k for k, v in self.cache.items() if current_time - v > self.ttl]
        for key in expired_keys:
            del self.cache[key]

# Instância do cache global
mensagens_processadas = MessageCache()

# --- Configuração do Client ---
client = TelegramClient("minha_sessao", API_ID, API_HASH)

# --- Handlers e Lógica Principal ---
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

    # Evita repetir a mesma mensagem (com TTL)
    if mensagens_processadas.contains(event.id):
        return
    mensagens_processadas.add(event.id)

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    # Obter link mais confiável da mensagem
    try:
        msg_link = event.message.link
        if not msg_link:
            msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
    except Exception:
        msg_link = "Link indisponível"

    for termo in TERMOS:
        # Usar busca com regex para evitar falsos positivos
        if contains_termo(msg_original, termo, use_regex=True):
            logger.info(f"--> Termo '{termo}' encontrado em: {chat_title}")

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
        logger.error(f"Erro no comando /settermos: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    try:
        text = event.message.message
        if len(text) < 10:  # "/addtermo " tem 10 caracteres
            await event.reply("Use: /addtermo termo")
            return
            
        novo_termo = text[10:].strip()
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
        if len(text) < 10:  # "/deltermo " tem 10 caracteres
            await event.reply("Use: /deltermo termo")
            return
            
        termo_remover = text[10:].strip()
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
        await client.connect()
        if not await client.is_user_authorized():
            await client.start(phone=PHONE_NUMBER)
    except Exception as e:
        logger.error(f"Falha ao conectar ao Telegram: {e}")
        return

    logger.info("Userbot rodando! Monitorando tudo que você recebe...")

    # TESTE DIRETO: dispara uma notificação ao iniciar
    enviar_notificacao_push("<b>Teste direto do Hugging Face</b>")

    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
