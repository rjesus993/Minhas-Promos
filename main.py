#!/usr/bin/env python3
"""Main entry point for Minhas Promo: orchestrates bot, FastAPI server,
push queue, and backup scheduler with graceful shutdown, crash restart,
and supervision of long-running tasks."""

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from config import load_config
from db import SQLiteDatabase, get_db, reset_db_singleton
from server import start_server_async
from push_queue import push_queue_loop_async
from logging_config import setup_logging

logger = logging.getLogger(__name__)


# ============================================================================
# SUPERVISOR GENÉRICO
# ============================================================================
async def supervised(
    name: str,
    factory,
    initial_backoff: int = 5,
    max_backoff: int = 300,
) -> None:
    """
    Executa factory() em loop. Sempre que ela cair (por erro OU por retorno
    silencioso), reinicia com backoff exponencial.

    - name: rótulo usado nos logs
    - factory: callable assíncrono (coroutine function ou lambda)
    - max_backoff: teto do backoff em segundos
    - initial_backoff: backoff inicial em segundos
    """
    backoff = initial_backoff

    while True:
        started = time.time()
        logger.info(f"[{name}] Iniciando (backoff atual={backoff}s)...")

        try:
            await factory()
            uptime = time.time() - started
            logger.warning(
                f"[{name}] retornou sem exceção (uptime={uptime:.1f}s); reiniciando..."
            )
        except asyncio.CancelledError:
            logger.info(f"[{name}] Cancelado. Encerrando supervisor.")
            raise
        except Exception:
            uptime = time.time() - started
            logger.exception(f"[{name}] crashou após {uptime:.1f}s")

        # Se ficou de pé por bastante tempo, reseta o backoff
        if time.time() - started > 60:
            backoff = initial_backoff
            logger.info(f"[{name}] Uptime > 60s, backoff resetado.")

        logger.info(f"[{name}] Aguardando {backoff}s antes de reiniciar...")
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            logger.info(f"[{name}] Cancelado durante sleep. Encerrando.")
            raise

        backoff = min(backoff * 2, max_backoff)


# ============================================================================
# BACKUPS
# ============================================================================
def _list_backups(backup_dir) -> list:
    """Só os backups periódicos (backup_*.db), do mais antigo ao mais novo.
    Não mistura com pre-migration-*.db nem com outros .db da pasta."""
    return sorted(Path(backup_dir).glob("backup_*.db"), key=lambda p: p.stat().st_mtime)


async def _backup_scheduler(db, cfg) -> None:
    interval = max(60, int(cfg.backup_interval_seconds))
    retention = max(1, int(cfg.backup_retention))
    while True:
        try:
            # Agenda pelo backup mais recente em disco. Antes dormia `interval`
            # inteiro a cada start: em host que reinicia mais de 1x/dia
            # (ex.: Spaces) o primeiro backup nunca acontecia.
            existing = _list_backups(cfg.backup_dir)
            if existing:
                age = time.time() - existing[-1].stat().st_mtime
                wait = max(0.0, interval - age)
            else:
                wait = 0.0
            if wait:
                await asyncio.sleep(wait)

            dest = str(Path(cfg.backup_dir) / f"backup_{int(time.time())}.db")
            # db.backup é síncrono e pode levar segundos: fora do event loop.
            backup_path = await asyncio.to_thread(db.backup, dest_path=dest, compress=False)
            logger.info(f"Backup created: {backup_path}")

            backups = _list_backups(cfg.backup_dir)
            while len(backups) > retention:
                oldest = backups.pop(0)
                try:
                    oldest.unlink()
                    logger.info(f"Removed old backup: {oldest}")
                except Exception as e:
                    logger.warning(f"Failed to remove old backup {oldest}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Error in backup scheduler: {e}")
            await asyncio.sleep(60)


_CORRUPTION_MARKERS = ("malformed", "not a database", "corrupt")


def _looks_like_corruption(exc: BaseException) -> bool:
    """Corrupção de verdade, não 'database is locked' nem migration quebrada."""
    for e in (exc, exc.__cause__):
        if e is not None and any(m in str(e).lower() for m in _CORRUPTION_MARKERS):
            return True
    return False


def _open_db_or_restore(cfg):
    """Abre o banco e garante integridade. Se estiver corrompido e
    AUTO_RESTORE_ON_CORRUPTION=true, tenta os backups do mais novo ao mais
    antigo. Se nada der certo, encerra com código 1 (nunca segue com banco ruim)."""
    reason = None
    try:
        db = get_db()
        if db.health().get("integrity_check") == "ok":
            return db
        reason = "integrity_check falhou"
    except Exception as e:
        if not _looks_like_corruption(e):
            raise  # erro de migration/permissão etc.: não é motivo para sobrescrever dados
        reason = f"erro ao abrir: {e}"

    logger.error(f"Banco corrompido ({reason}).")
    if not cfg.auto_restore_on_corruption:
        logger.error("Auto-restore desativado (AUTO_RESTORE_ON_CORRUPTION=false). Encerrando.")
        sys.exit(1)

    reset_db_singleton()  # fecha a conexão antes de trocar o arquivo
    for candidate in reversed(_list_backups(cfg.backup_dir)):
        try:
            safety = SQLiteDatabase.restore_from_backup(
                str(candidate), cfg.db_path, safety_copy=True
            )
            reset_db_singleton()
            db = get_db()
            if db.health().get("integrity_check") == "ok":
                logger.info(f"Banco restaurado de {candidate.name} (cópia do estado anterior: {safety}).")
                return db
            logger.error(f"Backup {candidate.name} restaurado mas ainda inválido; tentando o anterior.")
        except Exception:
            logger.exception(f"Falha ao restaurar {candidate.name}; tentando o anterior.")
            reset_db_singleton()

    logger.error("Nenhum backup válido para restaurar. Encerrando.")
    sys.exit(1)


# ============================================================================
# RUN MAIN
# ============================================================================
async def run_main() -> None:
    setup_logging()          # antes de qualquer log (inclusive avisos do load_config)
    cfg = load_config()
    db = _open_db_or_restore(cfg)

    # O push "Bot iniciado" é enviado pelo bot.py (uma vez por processo, fora
    # do event loop). Aqui ele era duplicado e fazia HTTP bloqueante no loop.

    # ------------------------------------------------------------------
    # Shutdown coordenado via asyncio.Event
    # ------------------------------------------------------------------
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_stop(*_args: Any) -> None:
        if not stop.is_set():
            logger.info("Sinal de shutdown recebido. Solicitando encerramento...")
            stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_a: loop.call_soon_threadsafe(_request_stop))

    # ------------------------------------------------------------------
    # Tasks supervisionadas
    # ------------------------------------------------------------------
    from bot import main as bot_main_coro

    tasks = [
        asyncio.create_task(
            supervised("bot", bot_main_coro),
            name="bot",
        ),
        asyncio.create_task(
            supervised(
                "server",
                lambda: start_server_async(
                    host=cfg.server_host,
                    port=cfg.server_port,
                    health_token=cfg.health_token,
                ),
            ),
            name="server",
        ),
        asyncio.create_task(
            supervised(
                "push",
                lambda: push_queue_loop_async(interval_s=60),
            ),
            name="push",
        ),
        asyncio.create_task(_backup_scheduler(db, cfg), name="backup"),
    ]

    await stop.wait()
    logger.info("Encerrando tarefas...")

    for t in tasks:
        t.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for task, result in zip(tasks, results):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            logger.error(
                f'Task {task.get_name()} failed: {result}',
                exc_info=result,
            )

    logger.info('Cleaning up resources...')
    try:
        db.close()
    except Exception:
        pass
    logger.info('Shutdown complete.')


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    try:
        asyncio.run(run_main())
    except KeyboardInterrupt:
        # Fallback caso o handler de sinal não tenha sido registrado a tempo
        pass