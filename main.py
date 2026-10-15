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
from db import get_db, reset_db_singleton, ensure_schema
from server import start_server_async
from push_queue import push_queue_loop_async, _ensure_config as _pq_cfg
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
# BACKUP SCHEDULER
# ============================================================================
async def _backup_scheduler(db, cfg) -> None:
    interval = max(60, int(cfg.backup_interval_seconds))
    retention = max(1, int(cfg.backup_retention))
    while True:
        try:
            await asyncio.sleep(interval)
            timestamp = int(time.time())
            dest = str(Path(cfg.backup_dir) / f'backup_{timestamp}.db')
            backup_path = db.backup(dest_path=dest, compress=False)
            logger.info(f'Backup created: {backup_path}')
            backups = sorted(Path(cfg.backup_dir).rglob('*.db'), key=lambda p: p.stat().st_mtime)
            while len(backups) > retention:
                oldest = backups.pop(0)
                try:
                    oldest.unlink()
                    logger.info(f'Removed old backup: {oldest}')
                except Exception as e:
                    logger.warning(f'Failed to remove old backup {oldest}: {e}')
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f'Error in backup scheduler: {e}')
            await asyncio.sleep(60)


# ============================================================================
# RUN MAIN
# ============================================================================
async def run_main() -> None:
    cfg = load_config()
    setup_logging()
    db = get_db()
    ensure_schema()

    integrity = db.health().get('integrity_check', '')
    if integrity != 'ok':
        if cfg.auto_restore_on_corruption:
            logger.info('Database integrity check failed. Attempting auto-restore...')
            try:
                backup_dir = Path(cfg.backup_dir)
                backups = sorted(
                    backup_dir.rglob('*.db'),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if backups:
                    db.restore_from_backup(str(backups[0]), safety_copy=True)
                    logger.info('Database restored from backup.')
                else:
                    logger.warning('No backup found for restoration.')
            except Exception as e:
                logger.error(f'Database restore failed: {e}')
        else:
            logger.error('Auto-restore disabled. Database corruption not recovered.')
            sys.exit(1)

    try:
        from push_queue import enqueue_push_html
        enqueue_push_html('<b>Bot iniciado no dispositivo Android</b>', optimistic=True)
    except Exception as e:
        logger.warning(f'Failed to enqueue startup push: {e}')

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