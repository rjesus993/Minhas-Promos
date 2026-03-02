# bot.py
import asyncio
import logging
import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from config import API_ID, API_HASH, PHONE_NUMBER, BOT_TOKEN, DESTINO_ID
from utils import normalize, contains_term, save_terms_to_db, load_terms_from_db
from db import init_db, save_terms, load_terms, add_term, remove_term
from server import start_server
import time

# Inicializa o banco de dados
init_db()

# Configura logging
logger = logging.getLogger(__name__)

# Cache para mensagens processadas
mensagens_processadas = set()
CACHE_TTL = 30 * 60  # 30 minutos

# Carrega termos iniciais
TERMOS = load_terms_from_db()
if not TERMOS:
    from config import TERMOS_ENV
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    save_terms(TERMOS)

# Cria cliente Telegram
client = TelegramClient("minha_sessao", API_ID, API_HASH)

async def send_notification_push(html_texto: str):
    """Envia notificação push via Vercel"""
    try:
        r = requests.post(
            "https://monitor-automacao.vercel.app/api/notify",
            json={"texto": html_texto},
            timeout=10,
            verify=True
        )
        if r.status_code != 200:
            logger.error(f"Falha ao enviar para Vercel: HTTP {r.status_code} → {r.text}")
        else:
            logger.info("Notificação enviada via Vercel com sucesso.")
    except requests.Timeout:
        logger.error("Timeout ao enviar notificação push")
    except requests.ConnectionError:
        logger.error("Erro de conexão ao enviar notificação push")
    except Exception as e:
        logger.error(f"Erro ao enviar push para Vercel: {e}")

async def process_message(event):
    """Processa uma mensagem nova"""
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

    # Evita repetir a mesma mensagem
    if event.id in mensagens_processadas:
        return
    
    mensagens_processadas.add(event.id)
    
    # Remove mensagens antigas do cache
    current_time = time.time()
    expired = [msg_id for msg_id in mensagens_processadas 
               if current_time - msg_id > CACHE_TTL]
    for msg_id in expired:
        mensagens_processadas.discard(msg_id)

    msg_original = event.message.message or ""
    if not msg_original:
        return

    chat = await event.get_chat()
    chat_title = getattr(chat, "title", "Chat Privado")

    msg_limpa = normalize(msg_original)

    for termo in TERMOS:
        if contains_term(msg_limpa, termo):
            logger.info(f"--> Termo '{termo}' encontrado em: {chat_title}")

            # Link direto para a mensagem
            try:
                msg_link = event.message.get_url()
                if not msg_link:
                    # Fallback para link manual
                    if getattr(chat, "username", None):
                        msg_link = f"https://t.me/{chat.username}/{event.message.id}"
                    else:
                        msg_link = f"https://t.me/c/{chat.id}/{event.message.id}"
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
            try:
                await client.send_message("me", alerta_html, parse_mode="html")
            except Exception as e:
                logger.error(f"Erro ao enviar mensagem para Saved Messages: {e}")

            # 2. Bot te avisar (Push Notification)
            await send_notification_push(alerta_html)

            # Garante apenas uma notificação por mensagem
            break

@client.on(events.NewMessage)
async def handler(event):
    """Handler para novas mensagens"""
    try:
        await process_message(event)
    except FloodWaitError as e:
        logger.warning(f"FloodWait: {e.seconds} segundos")
        await asyncio.sleep(e.seconds)
    except ConnectionError:
        logger.error("Erro de conexão com Telegram")
    except Exception as e:
        logger.error(f"Erro ao processar mensagem: {e}")

# --- Comandos do bot ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Comando /start"""
    await event.reply("Olá! Estou monitorando mensagens para os termos: " + ", ".join(TERMOS))

@client.on(events.NewMessage(pattern='/termos'))
async def list_termos_handler(event):
    """Comando /termos"""
    if not TERMOS:
        await event.reply("Nenhum termo configurado.")
    else:
        await event.reply("Termos atuais:\n" + "\n".join(f"• {t}" for t in TERMOS))

@client.on(events.NewMessage(pattern='/settermos'))
async def set_termos_handler(event):
    """Comando /settermos"""
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
        
        # Salva no banco
        if save_terms(TERMOS):
            await event.reply(f"Termos atualizados com sucesso!\n\n{', '.join(TERMOS)}")
        else:
            await event.reply("Erro ao salvar os termos.")
            
    except Exception as e:
        logger.error(f"Erro no comando /settermos: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/addtermo'))
async def add_termo_handler(event):
    """Comando /addtermo"""
    try:
        text = event.message.message
        if len(text) < 9:  # "/addtermo " tem 9 caracteres
            await event.reply("Use: /addtermo termo")
            return
            
        termo = text[9:].strip()
        
        if not termo:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        if add_term(termo):
            TERMOS.append(termo)
            await event.reply(f"Termo '{termo}' adicionado com sucesso!")
        else:
            await event.reply("Erro ao adicionar o termo.")
            
    except Exception as e:
        logger.error(f"Erro no comando /addtermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/deltermo'))
async def del_termo_handler(event):
    """Comando /deltermo"""
    try:
        text = event.message.message
        if len(text) < 9:  # "/deltermo " tem 9 caracteres
            await event.reply("Use: /deltermo termo")
            return
            
        termo = text[9:].strip()
        
        if not termo:
            await event.reply("Nenhum termo válido fornecido.")
            return
            
        if remove_term(termo):
            global TERMOS
            TERMOS = load_terms_from_db()
            await event.reply(f"Termo '{termo}' removido com sucesso!")
        else:
            await event.reply("Erro ao remover o termo.")
            
    except Exception as e:
        logger.error(f"Erro no comando /deltermo: {e}")
        await event.reply("Erro ao processar o comando.")

@client.on(events.NewMessage(pattern='/reset'))
async def reset_handler(event):
    """Comando /reset"""
    global TERMOS
    # Recarrega os termos do banco
    TERMOS = load_terms()
    if not TERMOS:
        from config import TERMOS_ENV
        TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    
    await event.reply(f"Termos resetados para: {', '.join(TERMOS)}")

async def main():
    """Função principal"""
    try:
        # Conecta ao Telegram
        await client.start(phone=PHONE_NUMBER)
        logger.info("Userbot conectado com sucesso!")
        
        # Inicia o servidor FastAPI em background
        server_task = asyncio.create_task(start_server())
        
        # Teste direto: dispara uma notificação ao iniciar
        await send_notification_push("<b>Teste direto do Hugging Face</b>")
        
        # Mantém o bot rodando
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Erro na execução principal: {e}")

if __name__ == "__main__":
    asyncio.run(main())

