# bot.py
import asyncio
import hashlib
import html
import logging
import os
import re
import time

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel

from config import API_ID, API_HASH, SESSION
from utils import normalize, contains_term, extract_price, extract_product_name
from matcher import smart_match, format_debug, tokenize
from db import (
    save_terms, load_terms, add_term, remove_term, get_term_history, get_db,
    list_source_chats, source_chats_mode, add_source_chat, remove_source_chat,
    clear_source_chats, is_chat_allowed,
    was_hash_seen, remember_hash, purge_seen_hashes, count_seen_hashes,
)
from push_queue import enqueue_push_html

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURAÇÃO
# ============================================================
NOTIF_CHAT_ID = "me"
TERMOS_RELOAD_INTERVAL = 30
DEBOUNCE_PER_TERM = 60
MAX_TEXT_PREVIEW = 400
CACHE_TTL = 30 * 60
# Verbose por padrão desligado (loga cada mensagem). Ligue com DEBUG=1 ou /debug on.
DEBUG = os.getenv("DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

IGNORE_ALL_BOTS = True
ONLY_GROUPS = True
DELETE_COMMANDS = False
DELETE_COMMAND_DELAY = 5
MAX_ULTIMOS_ENVIOS = 500

ENFORCE_SOURCE_CHATS = True
SOURCE_CHATS_RELOAD_INTERVAL = 30

# Fase 4
DEDUP_WINDOW_SECONDS = 1800       # 30 min
DEDUP_PURGE_INTERVAL = 3600       # 1h
DEDUP_PURGE_AGE = 86400           # 24h

WATCHDOG_INTERVAL = 60
WATCHDOG_FAIL_THRESHOLD = 3

# ============================================================
# ESTADO GLOBAL
# ============================================================
_hashes_em_envio: set = set()   # hashes sendo notificados agora (evita corrida)
_startup_push_sent = False
_me_id = None
_me_username = None
_ultimo_reload = 0.0
_ultimos_envios = {}
_start_time = time.time()

_source_mode = "all"
_source_allow_ids = set()
_source_deny_ids = set()
_source_last_reload = 0.0

# Fase 4
_ultima_purge = 0.0
_last_event_ts = 0.0
_watchdog_fails = 0

# ============================================================
# CARREGAMENTO DE TERMOS
# ============================================================
TERMOS = load_terms()
if not TERMOS:
    from config import TERMOS_ENV
    TERMOS = [t.strip() for t in TERMOS_ENV.split(",") if t.strip()]
    save_terms(TERMOS)

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)


async def _db(fn, *args):
    """Executa chamada síncrona de banco fora do event loop.
    Com busy_timeout=30s, uma trava no SQLite não pode congelar o Telethon."""
    return await asyncio.to_thread(fn, *args)


# ============================================================
# PUSH EXTERNO
# ============================================================
async def send_notification_push(html_texto: str):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: enqueue_push_html(html_texto, optimistic=True),
        )
    except Exception as e:
        logger.error(f"Erro ao enfileirar push: {e}")


# ============================================================
# UTILITÁRIOS
# ============================================================
def _uptime() -> str:
    s = int(time.time() - _start_time)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def _is_command(text: str) -> bool:
    return bool(text) and text.lstrip().startswith("/")


def _deve_notificar(termo: str) -> bool:
    agora = time.time()
    ultimo = _ultimos_envios.get(termo)
    if ultimo is None:
        return True
    if agora - ultimo >= DEBOUNCE_PER_TERM:
        return True
    if DEBUG:
        logger.info("[DEBOUNCE] '%s' ignorado.", termo)
    return False


def _podar_cache_debounce():
    if len(_ultimos_envios) <= MAX_ULTIMOS_ENVIOS:
        return
    ordenado = sorted(_ultimos_envios.items(), key=lambda kv: kv[1])
    for termo, _ in ordenado[: len(_ultimos_envios) - MAX_ULTIMOS_ENVIOS]:
        _ultimos_envios.pop(termo, None)


def _compute_offer_hash(texto: str) -> str:
    if not texto:
        return ""
    t = texto.lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"r\$\s*\d+([.,]\d{2})?", " ", t)
    t = re.sub(r"[!?*~_\-]{2,}", " ", t)
    t = normalize(t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace(" ", "")
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


async def _talvez_purgar_hashes():
    global _ultima_purge
    agora = time.time()
    if agora - _ultima_purge < DEDUP_PURGE_INTERVAL:
        return
    try:
        loop = asyncio.get_running_loop()
        n = await loop.run_in_executor(
            None, lambda: purge_seen_hashes(DEDUP_PURGE_AGE)
        )
        if n:
            logger.info("[DEDUP] %d hashes antigos removidos", n)
        _ultima_purge = agora
    except Exception:
        logger.exception("[DEDUP] Erro na purga")


async def _reload_termos_se_preciso():
    global TERMOS, _ultimo_reload
    agora = time.time()
    if agora - _ultimo_reload < TERMOS_RELOAD_INTERVAL:
        return
    try:
        novos = await _db(load_terms)
        if novos != TERMOS:
            logger.info("[TERMOS] Atualizados: %s", novos)
            TERMOS = novos
        _ultimo_reload = agora
    except Exception:
        logger.exception("[TERMOS] Erro ao recarregar")


async def _reload_source_chats_se_preciso():
    global _source_mode, _source_allow_ids, _source_deny_ids, _source_last_reload
    agora = time.time()
    if agora - _source_last_reload < SOURCE_CHATS_RELOAD_INTERVAL:
        return
    try:
        entradas = await _db(list_source_chats)
        mode = await _db(source_chats_mode)
        allow = {int(e["chat_id"]) for e in entradas if e.get("mode") == "allow"}
        deny = {int(e["chat_id"]) for e in entradas if e.get("mode") == "deny"}
        if (mode != _source_mode) or (allow != _source_allow_ids) or (deny != _source_deny_ids):
            logger.info("[SOURCE] mode=%s allow=%d deny=%d", mode, len(allow), len(deny))
        _source_mode = mode
        _source_allow_ids = allow
        _source_deny_ids = deny
        _source_last_reload = agora
    except Exception:
        logger.exception("[SOURCE] Erro ao recarregar")


def _chat_passou_filtro(chat_id: int) -> bool:
    if not ENFORCE_SOURCE_CHATS:
        return True
    if _source_mode == "all":
        return True
    if _source_mode == "allow":
        return chat_id in _source_allow_ids
    if _source_mode == "deny":
        return chat_id not in _source_deny_ids
    return True


async def _enviar_notificacao(mensagem: str) -> bool:
    try:
        await client.send_message(NOTIF_CHAT_ID, mensagem, parse_mode="html")
        return True
    except FloodWaitError as e:
        logger.warning("[TELEGRAM] FloodWait %ss", e.seconds)
        await asyncio.sleep(e.seconds)
        try:
            await client.send_message(NOTIF_CHAT_ID, mensagem, parse_mode="html")
            return True
        except Exception:
            logger.exception("[TELEGRAM] Falha pós-FloodWait")
            return False
    except Exception:
        logger.exception("[TELEGRAM] Erro ao enviar")
        return False


async def _responder(event, texto: str):
    try:
        await client.send_message(event.chat_id, texto, parse_mode="html")
        return
    except Exception:
        logger.exception("[CMD] Erro com HTML")
    try:
        limpo = (texto
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<code>", "").replace("</code>", "")
                 .replace("<i>", "").replace("</i>", ""))
        await client.send_message(event.chat_id, limpo)
    except Exception:
        logger.exception("[CMD] Falha total")


async def _auto_delete(event):
    if not DELETE_COMMANDS:
        return
    try:
        await asyncio.sleep(DELETE_COMMAND_DELAY)
        await event.delete()
    except Exception:
        pass


# ============================================================
# WATCHDOG
# ============================================================
async def _watchdog_loop():
    global _watchdog_fails, _last_event_ts
    logger.info("[WATCHDOG] iniciado (intervalo=%ds, threshold=%d)",
                WATCHDOG_INTERVAL, WATCHDOG_FAIL_THRESHOLD)
    while True:
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            conectado = client.is_connected()
            if conectado:
                _watchdog_fails = 0
                if DEBUG:
                    logger.info(
                        "[WATCHDOG] OK | uptime=%s | ultimo evento há %.0fs",
                        _uptime(),
                        time.time() - _last_event_ts if _last_event_ts else -1,
                    )
                continue
            _watchdog_fails += 1
            logger.warning(
                "[WATCHDOG] desconectado (falha %d/%d)",
                _watchdog_fails, WATCHDOG_FAIL_THRESHOLD,
            )
            if _watchdog_fails >= WATCHDOG_FAIL_THRESHOLD:
                logger.error("[WATCHDOG] threshold atingido — solicitando restart do bot")
                raise RuntimeError("watchdog: cliente desconectado")
        except asyncio.CancelledError:
            logger.info("[WATCHDOG] cancelado")
            raise
        except Exception as e:
            logger.exception("[WATCHDOG] erro inesperado: %s", e)
            raise


# ============================================================
# PROCESSAMENTO
# ============================================================
@client.on(events.NewMessage)
async def handler(event):
    global _last_event_ts
    _last_event_ts = time.time()
    try:
        await process_message(event)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logger.error(f"Erro no handler: {e}", exc_info=True)


async def process_message(event):
    if event.out:
        return
    if _me_id is not None and event.sender_id == _me_id:
        return

    raw_text = event.raw_text or ""
    if _is_command(raw_text):
        return

    chat = None
    if ONLY_GROUPS:
        chat = await event.get_chat()
        if isinstance(chat, User):
            return

    try:
        sender = await event.get_sender()
        sender_username = (getattr(sender, "username", "") or "").lower()
        if _me_username and sender_username == _me_username:
            return
        if IGNORE_ALL_BOTS and getattr(sender, "bot", False):
            return
    except Exception:
        pass

    await _reload_source_chats_se_preciso()
    if not _chat_passou_filtro(event.chat_id):
        if DEBUG:
            logger.info("[SOURCE] chat %s ignorado (mode=%s)", event.chat_id, _source_mode)
        return

    msg_original = raw_text.strip()
    if not msg_original:
        return

    await _reload_termos_se_preciso()
    await _talvez_purgar_hashes()

    chat = chat or await event.get_chat()
    chat_title_raw = getattr(chat, "title", None) or "Chat Privado"
    chat_title = html.escape(str(chat_title_raw))
    msg_limpa = normalize(msg_original)

    if DEBUG:
        logger.info("[CHECK] TERMOS=%s | msg=%r", TERMOS, msg_limpa[:100])

    msg_tokens = tokenize(msg_limpa)   # uma vez por mensagem, não por termo
    h = None                           # hash calculado sob demanda

    for termo in TERMOS:
        # 1) tentativa exata — comportamento original preservado
        exact_match = contains_term(msg_limpa, termo)
        smart_result = None

        # 2) smart matching só entra se o exato falhou
        if not exact_match:
            smart_result = smart_match(
                msg_limpa, termo, debug=DEBUG, msg_tokens=msg_tokens
            )
            if not smart_result["matched"]:
                if DEBUG:
                    logger.info(
                        "[SMART MATCH] termo='%s' score=%d resultado=NO_MATCH",
                        termo, smart_result["score"],
                    )
                continue

        if not _deve_notificar(termo):
            continue

        if h is None:
            h = _compute_offer_hash(msg_original)

        # Reserva o hash ANTES de qualquer await: a mesma oferta postada em
        # dois grupos ao mesmo tempo gera dois handlers concorrentes, e sem
        # isso ambos passariam pelo was_hash_seen e notificariam em dobro.
        if h:
            if h in _hashes_em_envio:
                logger.info("[DEDUP] oferta em envio por outro handler (hash=%s). Ignorado.", h)
                break
            _hashes_em_envio.add(h)

        try:
            if h and await _db(was_hash_seen, h, DEDUP_WINDOW_SECONDS):
                logger.info("[DEDUP] conteúdo repetido (hash=%s, termo=%s). Ignorado.", h, termo)
                break

            try:
                msg_link = event.message.get_url() or "Link indisponível"
            except Exception:
                msg_link = "Link indisponível"

            preco = html.escape(str(extract_price(msg_original) or "Não identificado"))
            produto = html.escape(str(extract_product_name(msg_original)))
            preview = html.escape(msg_original[:MAX_TEXT_PREVIEW])
            termo_esc = html.escape(str(termo))
            link_esc = html.escape(str(msg_link), quote=False)

            alerta_html = (
                "<b>🔥 Oferta encontrada!</b>\n\n"
                f"<b>🧩 Termo:</b> <code>{termo_esc}</code>\n"
                f"<b>💬 Grupo:</b> {chat_title}\n"
                f"<b>💰 Preço:</b> {preco}\n"
                f"<b>📦 Produto:</b> {produto}\n"
                f"<b>🔗 Link:</b> {link_esc}\n\n"
                "<b>📝</b>\n"
                f"{preview}"
            )

            if exact_match:
                logger.info("[MATCH] '%s' em '%s'", termo, chat_title)
            else:
                logger.info(
                    "[SMART MATCH] termo='%s' score=%d resultado=MATCH tipo=%s",
                    termo, smart_result["score"], smart_result["match_type"],
                )
                if DEBUG:
                    logger.info("\n%s", format_debug(termo, msg_original, smart_result))

            enviado = await _enviar_notificacao(alerta_html)

            if enviado:
                _ultimos_envios[termo] = time.time()
                _podar_cache_debounce()
                if h:
                    try:
                        await _db(remember_hash, h, termo)
                    except Exception:
                        logger.exception("[DEDUP] Erro ao gravar hash")
                await send_notification_push(alerta_html)
        finally:
            if h:
                _hashes_em_envio.discard(h)
        break


# ============================================================
# COMANDOS
# ============================================================

@client.on(events.NewMessage(pattern=r'^/start(?:@\w+)?\b', outgoing=True))
async def start_handler(event):
    asyncio.create_task(_auto_delete(event))
    await _responder(
        event,
        "🤖 <b>Minhas Promos</b>\n\n"
        f"Termos: <b>{len(TERMOS)}</b> | Filtro: <b>{_source_mode}</b>\n\n"
        "<b>Termos</b>\n"
        "/termos /addtermo /deltermo /settermos /delall /historico\n\n"
        "<b>Grupos</b>\n"
        "/listgroups /chats /allowchat ID /denychat ID /clearchats\n\n"
        "<b>Sistema</b>\n"
        "/status /ping /debug /chatid /dedup",
    )


@client.on(events.NewMessage(pattern=r'^/ping(?:@\w+)?\b', outgoing=True))
async def ping_handler(event):
    asyncio.create_task(_auto_delete(event))
    conectado = client.is_connected()
    await _responder(
        event,
        "🏓 <b>Pong!</b>\n"
        f"• Uptime: <code>{_uptime()}</code>\n"
        f"• Conectado: <b>{'✅' if conectado else '❌'}</b>\n"
        f"• Último evento: há <b>{int(time.time() - _last_event_ts) if _last_event_ts else '∞'}s</b>",
    )


@client.on(events.NewMessage(pattern=r'^/termos(?:@\w+)?\b', outgoing=True))
async def list_termos_handler(event):
    asyncio.create_task(_auto_delete(event))
    termos = load_terms()
    if not termos:
        await _responder(event, "📭 Nenhum termo cadastrado.")
        return
    linhas = "\n".join(f"• <code>{html.escape(str(t))}</code>" for t in termos)
    await _responder(event, f"<b>Termos ({len(termos)}):</b>\n{linhas}")


@client.on(events.NewMessage(pattern=r'^/settermos(?:@\w+)?\s+(.+)', outgoing=True))
async def set_termos_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        novos = [t.strip() for t in event.pattern_match.group(1).split(",") if t.strip()]
        if novos:
            global TERMOS
            TERMOS = novos
            save_terms(TERMOS)
            await _responder(event, "✅ Termos atualizados:\n" + ", ".join(TERMOS))
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/addtermo(?:@\w+)?\s+(.+)', outgoing=True))
async def add_termo_handler(event):
    asyncio.create_task(_auto_delete(event))
    termo = event.pattern_match.group(1).strip()
    if not termo:
        await _responder(event, "Uso: /addtermo <termo>")
        return
    if termo.lower() in {t.lower() for t in TERMOS}:
        await _responder(event, f"⚠️ Termo '{html.escape(termo)}' já existe.")
        return
    if add_term(termo):
        TERMOS.append(termo)
        await _responder(event, f"✅ Termo '{html.escape(termo)}' adicionado.")
    else:
        await _responder(event, "❌ Erro ao adicionar.")


@client.on(events.NewMessage(pattern=r'^/deltermo(?:@\w+)?\s+(.+)', outgoing=True))
async def del_termo_handler(event):
    asyncio.create_task(_auto_delete(event))
    termo = event.pattern_match.group(1).strip()
    if not termo:
        await _responder(event, "Uso: /deltermo <termo>")
        return
    if remove_term(termo):
        global TERMOS
        TERMOS = load_terms()
        await _responder(event, f"🗑️ Termo '{html.escape(termo)}' removido.")
    else:
        await _responder(event, f"❌ Termo '{html.escape(termo)}' não encontrado.")


@client.on(events.NewMessage(pattern=r'^/delall(?:@\w+)?\b', outgoing=True))
async def delall_handler(event):
    asyncio.create_task(_auto_delete(event))
    global TERMOS
    save_terms([])
    TERMOS = []
    await _responder(event, "🗑️ Todos os termos removidos.")


@client.on(events.NewMessage(pattern=r'^/status(?:@\w+)?\b', outgoing=True))
async def status_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        db = get_db()
        h = await _db(lambda: db.health(full=False))
        await _responder(
            event,
            "🩺 <b>Status</b>\n"
            f"• Uptime: <code>{_uptime()}</code>\n"
            f"• Termos: <b>{len(TERMOS)}</b>\n"
            f"• Filtro: <b>{_source_mode}</b> "
            f"(allow={len(_source_allow_ids)} deny={len(_source_deny_ids)})\n"
            f"• DB: <b>{h['connected']}</b>\n"
            f"• WAL: <b>{h['wal_mode']}</b>\n"
            f"• Fila push: <b>{db.push_queue_count()}</b>\n"
            f"• Debug: <b>{DEBUG}</b>\n"
            f"• Watchdog fails: <b>{_watchdog_fails}</b>\n"
            f"• Último evento: há <b>{int(time.time() - _last_event_ts) if _last_event_ts else '∞'}s</b>",
        )
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/dedup(?:@\w+)?\b', outgoing=True))
async def dedup_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        total = await _db(count_seen_hashes)
        await _responder(
            event,
            "🔒 <b>Deduplicação</b>\n"
            f"• Janela: <b>{DEDUP_WINDOW_SECONDS}s</b>\n"
            f"• Hashes gravados: <b>{total}</b>\n"
            f"• Purga a cada: <b>{DEDUP_PURGE_INTERVAL}s</b>\n"
            f"• Idade máxima: <b>{DEDUP_PURGE_AGE}s</b>",
        )
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/historico(?:@\w+)?\b', outgoing=True))
async def historico_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        hist = get_term_history(limit=20)
        if not hist:
            await _responder(event, "📭 Sem histórico.")
            return
        linhas = []
        for item in hist:
            term = html.escape(str(item.get("term", "")))
            action = item.get("action", "")
            ts = item.get("timestamp", "")
            label = "A" if action == "added" else ("D" if action == "removed" else "?")
            linhas.append(f"[{label}] <code>{term}</code> — {ts}")
        await _responder(event, "<b>Últimas 20:</b>\n" + "\n".join(linhas))
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/chatid(?:@\w+)?\b', outgoing=True))
async def chatid_handler(event):
    asyncio.create_task(_auto_delete(event))
    chat = await event.get_chat()
    tipo = "Desconhecido"
    if isinstance(chat, User):
        tipo = "Usuário (DM)"
    elif isinstance(chat, Chat):
        tipo = "Grupo"
    elif isinstance(chat, Channel):
        tipo = "Canal" if getattr(chat, "broadcast", False) else "Supergrupo"
    await _responder(
        event,
        "📍 <b>Chat atual</b>\n"
        f"• Tipo: <b>{tipo}</b>\n"
        f"• chat_id: <code>{event.chat_id}</code>\n"
        f"• Filtro: <b>{'✅ monitorado' if _chat_passou_filtro(event.chat_id) else '🚫 ignorado'}</b>",
    )


@client.on(events.NewMessage(pattern=r'^/debug(?:\s+(on|off))?(?:@\w+)?\b', outgoing=True))
async def debug_handler(event):
    asyncio.create_task(_auto_delete(event))
    global DEBUG
    arg = event.pattern_match.group(1)
    if arg is None:
        await _responder(event, f"🔧 Debug: <b>{'ON' if DEBUG else 'OFF'}</b>")
        return
    if arg.lower() == "on":
        DEBUG = True
        await _responder(event, "✅ Debug <b>ON</b>")
    elif arg.lower() == "off":
        DEBUG = False
        await _responder(event, "🔇 Debug <b>OFF</b>")
    else:
        await _responder(event, "Uso: /debug on|off")


@client.on(events.NewMessage(pattern=r'^/listgroups(?:@\w+)?\b', outgoing=True))
async def listgroups_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        linhas = []
        total = 0
        async for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel):
                continue
            if isinstance(dialog.entity, User):
                continue
            total += 1
            if total > 50:
                break
            estado = "✅" if _chat_passou_filtro(dialog.id) else "🚫"
            titulo = html.escape(str(dialog.name or "Sem nome")[:60])
            linhas.append(f"{estado} <code>{dialog.id}</code> — {titulo}")
        if not linhas:
            await _responder(event, "📭 Nenhum grupo encontrado.")
            return
        header = f"<b>Grupos ({total}):</b>\n<code>✅ monitorado | 🚫 ignorado</code>\n\n"
        corpo = "\n".join(linhas)
        if total > 50:
            corpo += "\n\n<i>…truncado em 50.</i>"
        await _responder(event, header + corpo)
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/chats(?:@\w+)?\b', outgoing=True))
async def chats_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        entradas = list_source_chats()
        mode = source_chats_mode()
        if not entradas:
            await _responder(
                event,
                f"📭 Filtro vazio (<b>{mode}</b>). O bot monitora <b>todos</b>.\n\n"
                "Use /allowchat ID para restringir ou /denychat ID para excluir.",
            )
            return
        allow = [e for e in entradas if e["mode"] == "allow"]
        deny = [e for e in entradas if e["mode"] == "deny"]
        partes = [f"<b>Filtro:</b> <code>{mode}</code>"]
        if allow:
            partes.append("\n<b>✅ Allow:</b>")
            for e in allow:
                titulo = html.escape(str(e.get("title") or "")[:50])
                partes.append(f"• <code>{e['chat_id']}</code> {titulo}")
        if deny:
            partes.append("\n<b>🚫 Deny:</b>")
            for e in deny:
                titulo = html.escape(str(e.get("title") or "")[:50])
                partes.append(f"• <code>{e['chat_id']}</code> {titulo}")
        await _responder(event, "\n".join(partes))
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/allowchat\s+(-?\d+)(?:@\w+)?\b', outgoing=True))
async def allowchat_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        chat_id = int(event.pattern_match.group(1))
        titulo = ""
        try:
            ent = await client.get_entity(chat_id)
            titulo = getattr(ent, "title", "") or ""
        except Exception:
            pass
        if add_source_chat(chat_id, titulo, "allow"):
            global _source_last_reload
            _source_last_reload = 0
            await _responder(
                event,
                f"✅ <code>{chat_id}</code> adicionado à <b>allow</b>.\n"
                f"Filtro agora é <b>allow</b>: só esse(s) grupo(s) monitorado(s).",
            )
        else:
            await _responder(event, "❌ Erro ao adicionar.")
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/denychat\s+(-?\d+)(?:@\w+)?\b', outgoing=True))
async def denychat_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        chat_id = int(event.pattern_match.group(1))
        titulo = ""
        try:
            ent = await client.get_entity(chat_id)
            titulo = getattr(ent, "title", "") or ""
        except Exception:
            pass
        if add_source_chat(chat_id, titulo, "deny"):
            global _source_last_reload
            _source_last_reload = 0
            await _responder(
                event,
                f"🚫 <code>{chat_id}</code> adicionado à <b>deny</b>.\n"
                f"Filtro agora é <b>deny</b>: todos monitorados, exceto esse(s).",
            )
        else:
            await _responder(event, "❌ Erro ao adicionar.")
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


@client.on(events.NewMessage(pattern=r'^/clearchats(?:@\w+)?\b', outgoing=True))
async def clearchats_handler(event):
    asyncio.create_task(_auto_delete(event))
    try:
        n = clear_source_chats()
        global _source_last_reload
        _source_last_reload = 0
        await _responder(
            event,
            f"🗑️ {n} entradas removidas. Filtro agora é <b>all</b> (monitora tudo).",
        )
    except Exception as e:
        await _responder(event, f"❌ Erro: {html.escape(str(e))}")


# ============================================================
# MAIN
# ============================================================
async def main():
    global _me_id, _me_username, _startup_push_sent
    watchdog = None
    run_task = None
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Sessão do Telegram não autorizada. Gere uma nova SESSION (gerar_sessao.py)."
            )
        me = await client.get_me()
        _me_id = me.id
        _me_username = (getattr(me, "username", "") or "").lower() or None

        logger.info("=" * 60)
        logger.info("Logado: id=%s username=%s bot=%s", me.id, me.username, me.bot)
        logger.info("TERMOS carregados: %d", len(TERMOS))
        logger.info("NOTIF_CHAT_ID: %s", NOTIF_CHAT_ID)
        logger.info("DEBUG: %s | ONLY_GROUPS: %s | IGNORE_ALL_BOTS: %s | ENFORCE_SOURCE_CHATS: %s",
                    DEBUG, ONLY_GROUPS, IGNORE_ALL_BOTS, ENFORCE_SOURCE_CHATS)
        logger.info("DEDUP: janela=%ss purga=%ss idade=%ss",
                    DEDUP_WINDOW_SECONDS, DEDUP_PURGE_INTERVAL, DEDUP_PURGE_AGE)
        logger.info("WATCHDOG: intervalo=%ss threshold=%d",
                    WATCHDOG_INTERVAL, WATCHDOG_FAIL_THRESHOLD)
        logger.info("=" * 60)

        if me.bot:
            logger.warning("⚠️ Sessão é de BOT. Regenere a SESSION com login por telefone.")

        # Carrega o filtro no boot
        await _reload_source_chats_se_preciso()
        logger.info("[SOURCE] mode inicial=%s", _source_mode)

        # Watchdog como task acompanhada: se ele falhar, o erro chega aqui e o
        # supervisor reinicia o bot (antes a exceção morria numa task órfã).
        watchdog = asyncio.create_task(_watchdog_loop(), name="watchdog")

        # Push de boot só na primeira vez do processo (sem spam a cada restart)
        if not _startup_push_sent:
            await send_notification_push("<b>Bot iniciado</b>")
            _startup_push_sent = True

        run_task = asyncio.ensure_future(client.run_until_disconnected())
        done, _ = await asyncio.wait({run_task, watchdog}, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()  # propaga a exceção do watchdog, se houver
        logger.warning("[BOT] cliente desconectou; solicitando restart")

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Erro no main")
        raise  # o supervisor aplica backoff e reinicia
    finally:
        tasks = [t for t in (watchdog, run_task) if t is not None]
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())