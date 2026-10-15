"""
Push notification queue: optimistic send, DB-backed retry with exponential backoff.

Public API:
- enqueue_push_html(html: str, optimistic: bool = True) -> bool
    Try synchronous HTTP send (if optimistic=True and queue enabled).
    On success: return True immediately, nothing queued.
    On failure: persist to push_queue, return False.

- async def process_due_queue_http() -> tuple[int, int]:
    Drain queue entries whose next_attempt_at is due. Returns
    (success_count, failed_count).

- async def push_queue_loop_async(interval_s: int = 60) -> None:
    Periodic loop intended for main.py asyncio.gather(). Cancels cleanly.

Backoff: backoff_seconds = min(base * (2 ** retry_count), 3600) + randint(0, 60)
Dead letter: after push_max_retries attempts the row is deleted with a WARNING
log (deliveries are best-effort; we don't want the queue to grow unbounded).
"""

import asyncio
import datetime
import logging
import random
import threading
import time
from typing import Optional, Tuple

from db import get_db
from config import AppConfig

logger = logging.getLogger(__name__)

# Module-level cached AppConfig reference (set via _ensure_bootstrapped or
# import-time; lazy so tests can mock).
_CONFIG_REF: Optional[AppConfig] = None

# Estado de observabilidade (lido por server.py em /health/external).
# Atualizado a cada tentativa real de envio, otimista ou da fila.
_STATE_LOCK = threading.Lock()
_LAST_PUSH_OK_TS: Optional[float] = None
_LAST_PUSH_FAIL_TS: Optional[float] = None

# Se o endpoint cair, não gastamos N x timeout tentando o lote inteiro:
# após X falhas seguidas o resto do lote fica para o próximo ciclo.
CONSECUTIVE_FAIL_LIMIT = 3


def _record_push_state(ok: int = 0, failed: int = 0) -> None:
    global _LAST_PUSH_OK_TS, _LAST_PUSH_FAIL_TS
    now = time.time()
    with _STATE_LOCK:
        if ok:
            _LAST_PUSH_OK_TS = now
        if failed:
            _LAST_PUSH_FAIL_TS = now


def get_push_state() -> dict:
    with _STATE_LOCK:
        return {"last_success_ts": _LAST_PUSH_OK_TS, "last_failure_ts": _LAST_PUSH_FAIL_TS}


def _row_age_seconds(created_at) -> Optional[float]:
    """Idade da linha da fila (created_at é CURRENT_TIMESTAMP, em UTC)."""
    if not created_at:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - dt).total_seconds()


def _ensure_config() -> AppConfig:
    global _CONFIG_REF
    if _CONFIG_REF is None:
        from config import load_config
        _CONFIG_REF = load_config()
    return _CONFIG_REF


def _calc_backoff_seconds(retry_count: int, base: int) -> int:
    """Exponential backoff with 1h cap + 0-60s jitter."""
    if retry_count < 0:
        retry_count = 0
    try:
        raw = min(base * (2 ** retry_count), 3600)
    except OverflowError:
        raw = 3600
    return int(raw) + random.randint(0, 60)


def _get_requests():
    """Lazy import of requests so import-time tests on restricted sandboxes don't crash."""
    import requests  # noqa: WPS433 - lazy import intentional
    return requests


def _do_push_sync(endpoint: str, html: str, timeout_s: int) -> None:
    """Fire the HTTP push synchronously. Raises on any error."""
    requests_lib = _get_requests()
    headers = {"Content-Type": "application/json"}
    token = getattr(_ensure_config(), "push_token", None)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests_lib.post(
            endpoint,
            json={"texto": html},
            timeout=timeout_s,
            headers=headers,
        )
        # 4xx/5xx become HTTPError
        resp.raise_for_status()
    except Exception:
        _record_push_state(failed=1)
        raise
    _record_push_state(ok=1)


def enqueue_push_html(html: str, optimistic: bool = True) -> bool:
    """Enqueue and optionally try sending immediately.

    Returns True if the push was delivered synchronously during this call.
    """
    if html is None or str(html).strip() == "":
        logger.debug("enqueue_push_html: empty html dropped")
        return False

    cfg = _ensure_config()
    db = get_db()

    if not cfg.push_queue_enabled:
        # Queue disabled: pure-fire-and-forget with no persistence, errors logged.
        try:
            _do_push_sync(cfg.push_endpoint, html, cfg.push_timeout_seconds)
            logger.debug("Push sent (queue disabled).")
            return True
        except Exception as e:
            logger.warning("Push failed and queue is disabled (dropped): %s", e)
            return False

    if optimistic:
        try:
            _do_push_sync(cfg.push_endpoint, html, cfg.push_timeout_seconds)
            logger.debug("Push sent optimistically.")
            return True
        except Exception as e:
            logger.info(
                "Optimistic push failed (%s). Queuing for retry.", e
            )
    db.enqueue_push(html)
    if not optimistic:
        logger.debug("Push queued (non-optimistic mode).")
    return False


def process_due_queue_http_sync() -> Tuple[int, int]:
    """Synchronous drain (used by tests and helpers). Returns (ok, failed)."""
    cfg = _ensure_config()
    db = get_db()
    base_backoff = max(1, int(cfg.push_backoff_base_seconds))
    max_retries = max(1, int(cfg.push_max_retries))

    rows = db.pop_due_pushes(limit=100)
    if not rows:
        return 0, 0

    max_age = int(getattr(cfg, "push_max_age_seconds", 0) or 0)
    success = 0
    failed = 0
    consecutive_fail = 0
    for row in rows:
        row_id = int(row["id"])
        html = row["html"]
        retry_count = int(row.get("retry_count") or 0)

        # Oferta velha demais não vale mais a notificação: descarta.
        age = _row_age_seconds(row.get("created_at"))
        if max_age and age is not None and age > max_age:
            logger.info("Push #%d descartado por idade (%.0fs > %ds).", row_id, age, max_age)
            db.mark_push_success(row_id)
            continue

        if consecutive_fail >= CONSECUTIVE_FAIL_LIMIT:
            logger.warning(
                "Push: %d falhas seguidas; adiando o restante do lote para o próximo ciclo.",
                consecutive_fail,
            )
            break

        try:
            _do_push_sync(cfg.push_endpoint, html, cfg.push_timeout_seconds)
            db.mark_push_success(row_id)
            success += 1
            consecutive_fail = 0
        except Exception as e:
            consecutive_fail += 1
            err_str = str(e)[:255] if e else "unknown"
            # Dead letter after N retries (retry_count here is pre-send; failure
            # means attempt #retry_count+1).
            attempted = retry_count + 1
            if attempted >= max_retries:
                logger.warning(
                    "Push #%d dead-lettered after %d retries (last error: %s).",
                    row_id,
                    attempted,
                    err_str,
                )
                # Treat dead-letter same as "success removal": call mark_push_success
                # because we don't want it to sit forever. It's effectively dropped.
                try:
                    db.mark_push_success(row_id)
                except Exception:
                    pass
            else:
                backoff = _calc_backoff_seconds(retry_count, base_backoff)
                db.mark_push_failure(row_id, err_str, backoff, max_retries)
            failed += 1

    logger.debug("Push queue drain: %d ok, %d failed.", success, failed)
    return success, failed


async def process_due_queue_http() -> Tuple[int, int]:
    """Async wrapper that runs sync HTTP in executor to avoid blocking the loop."""
    return await asyncio.to_thread(process_due_queue_http_sync)


async def push_queue_loop_async(interval_s: int = 60) -> None:
    """Periodic drain loop. Intended for main.py asyncio.gather()."""
    logger.info("push_queue_loop started (interval=%ds)", int(interval_s))
    iteration = 0
    while True:
        try:
            ok, bad = await process_due_queue_http()
            iteration += 1
            if iteration % 60 == 0 or ok > 0 or bad > 0:
                logger.debug(
                    "push_queue_loop iter=%d ok=%d failed=%d pending=%d",
                    iteration,
                    ok,
                    bad,
                    get_db().push_queue_count(),
                )
        except asyncio.CancelledError:
            logger.info("push_queue_loop cancelled.")
            raise
        except Exception as e:
            # Never let the loop die; log and continue.
            logger.exception("push_queue_loop unexpected error: %s", e)
        await asyncio.sleep(interval_s)  # cancelável a qualquer momento
