#!/usr/bin/env python3
"""Main entry point for Minhas Promo: orchestrates bot, FastAPI server, push queue, and backup scheduler with graceful shutdown and crash restart."""

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


def _register_signal_handlers() -> None:
    def _handle_signal(signum: int, frame: Any) -> None:
        logger.info(f'Received signal {signum}, initiating graceful shutdown...')
        for task in asyncio.all_tasks():
            if not task.done():
                task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)
        signal.signal(sig, lambda s, f: _handle_signal(s, f))


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


async def run_main() -> None:
    cfg = load_config()
    setup_logging()
    logger = logging.getLogger(__name__)
    db = get_db()
    ensure_schema()
    integrity = db.health().get('integrity_check', '')
    if integrity != 'ok':
        if cfg.auto_restore_on_corruption:
            logger.info('Database integrity check failed. Attempting auto-restore...')
            try:
                backup_dir = Path(cfg.backup_dir)
                backups = sorted(backup_dir.rglob('*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
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
    bot_task = asyncio.create_task(bot_main_within_gather(), name='bot')
    server_task = asyncio.create_task(start_server_async(host=cfg.server_host, port=cfg.server_port, health_token=cfg.health_token), name='server')
    push_task = asyncio.create_task(push_queue_loop_async(interval_s=60), name='push_queue')
    backup_task = asyncio.create_task(_backup_scheduler(db, cfg), name='backup_scheduler')
    results = await asyncio.gather(bot_task, server_task, push_task, backup_task, return_exceptions=True)
    for task, result in zip([bot_task, server_task, push_task, backup_task], results):
        if isinstance(result, Exception):
            logger.error(f'Task {task.get_name()} failed: {result}', exc_info=result)
    logger.info('Cleaning up resources...')
    try:
        db.close()
    except Exception:
        pass
    logger.info('Shutdown complete.')


async def bot_main_within_gather() -> None:
    from bot import main as bot_main_coro
    await bot_main_coro()


async def main() -> None:
    _register_signal_handlers()
    try:
        await run_main()
    except KeyboardInterrupt:
        logger.info('Interrupted by user.')
    except Exception:
        logger.exception('Fatal error in main loop.')
        raise


if __name__ == '__main__':
    while True:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info('Interrupted by user, exiting.')
            break
        except Exception as e:
            logger.exception(f'Main loop crashed: {e}')
            cfg = load_config()
            delay = getattr(cfg, 'restart_delay_seconds', 30)
            logger.info(f'Restarting in {delay} seconds...')
            time.sleep(delay)