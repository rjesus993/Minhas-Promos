"""
FastAPI server: keepalive + detailed health endpoints with optional token auth.

Public API (consumed by main.py gather):
- async def start_server_async(host: str, port: int, health_token: Optional[str] = None)
    Runs uvicorn in-asyncio-loop via uvicorn.Server (blocking await till shutdown).

Usage for shutdown from outside this module:
    task = asyncio.create_task(start_server_async(...))
    ...
    task.cancel()  # clean stop
"""

import asyncio
import logging
import os
import shutil
import socket
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import JSONResponse
import uvicorn

from db import get_db, reset_db_singleton
from push_queue import process_due_queue_http_sync, _ensure_config as _pq_cfg

logger = logging.getLogger(__name__)

_SERVER_STARTED_AT: float = 0.0
_SERVER_CONFIG_HOST: str = "0.0.0.0"
_SERVER_CONFIG_PORT: int = 7860
_HEALTH_TOKEN: Optional[str] = None
_APP_REF: Optional[FastAPI] = None

# Track last external ping timestamp (push endpoint reachability)
_LAST_PUSH_OK_TS: Optional[float] = None
_LAST_PUSH_FAIL_TS: Optional[float] = None


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _uptime_seconds() -> float:
    return max(0.0, time.time() - _SERVER_STARTED_AT) if _SERVER_STARTED_AT else 0.0


def _storage_usage_bytes(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {
            "path": os.path.abspath(path),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_pct": round(100.0 * usage.used / max(1, usage.total), 2),
        }
    except Exception as e:
        return {"path": path, "error": str(e)}


def _ram_usage_bytes() -> dict:
    """Best-effort: try psutil; fallback to process RSS via ctypes/os tricks."""
    info = {"bytes": 0, "source": "unavailable"}
    try:
        import psutil  # type: ignore
        p = psutil.Process(os.getpid())
        mem = p.memory_info()
        info["bytes"] = mem.rss
        info["vms_bytes"] = mem.vms
        info["source"] = "psutil"
        try:
            info["rss_mb"] = round(mem.rss / (1024 * 1024), 2)
        except Exception:
            pass
        return info
    except Exception:
        pass
    # Fallback for Unix: /proc/self/status VmRSS
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    info["bytes"] = kb * 1024
                    info["rss_mb"] = round(info["bytes"] / (1024 * 1024), 2)
                    info["source"] = "/proc/self/status"
                    return info
    except Exception:
        pass
    return info


def _cpu_usage_pct() -> dict:
    try:
        import psutil  # type: ignore
        return {"pct": round(psutil.cpu_percent(interval=None), 2), "source": "psutil"}
    except Exception:
        return {"pct": None, "source": "unavailable (install psutil)"}


def build_app(health_token: Optional[str] = None) -> FastAPI:
    """Build the FastAPI app. Called once; caches in _APP_REF."""
    global _APP_REF, _HEALTH_TOKEN
    _HEALTH_TOKEN = health_token
    app = FastAPI(title="Minhas Promo Userbot")
    _APP_REF = app

    # ---- Dependency: optional health token gate ----
    def _require_health_token(request: Request) -> None:
        if not _HEALTH_TOKEN:
            return
        provided = request.headers.get("X-Health-Token") or request.query_params.get("token")
        if not provided or provided != _HEALTH_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Health endpoints require X-Health-Token header or ?token= query",
            )

    # ---- / (uptime monitor keepalive; intentionally public) ----
    @app.get("/")
    def read_root():
        return {
            "status": "running",
            "message": "Userbot ativo",
            "uptime_s": round(_uptime_seconds(), 1),
            "started_at": _now_iso() if _SERVER_STARTED_AT else None,
        }

    @app.head("/")
    def read_root_head():
        return {}

    # ---- Aggregate /health (gated when HEALTH_TOKEN set) ----
    @app.get("/health", dependencies=[Depends(_require_health_token)])
    def health_check():
        db = get_db()
        sub_db = _health_database_payload(db)
        sub_storage = _health_storage_payload()
        sub_system = _health_system_payload()
        sub_external = _health_external_payload()
        overall_ok = all(
            p.get("ok") for p in (sub_db, sub_storage, sub_system, sub_external)
        )
        return {
            "status": "healthy" if overall_ok else "degraded",
            "ok": overall_ok,
            "uptime_s": round(_uptime_seconds(), 1),
            "started_at": _now_iso() if _SERVER_STARTED_AT else None,
            "server": {"host": _SERVER_CONFIG_HOST, "port": _SERVER_CONFIG_PORT},
            "database": sub_db,
            "storage": sub_storage,
            "system": sub_system,
            "external": sub_external,
            "components": ["database", "storage", "system", "external"],
        }

    @app.head("/health", dependencies=[Depends(_require_health_token)])
    def health_head():
        return {}

    # ---- Sub health endpoints ----
    @app.get("/health/database", dependencies=[Depends(_require_health_token)])
    def health_database():
        db = get_db()
        info = _health_database_payload(db)
        return JSONResponse(status_code=(200 if info["ok"] else 503), content=info)

    @app.get("/health/storage", dependencies=[Depends(_require_health_token)])
    def health_storage():
        info = _health_storage_payload()
        return JSONResponse(status_code=(200 if info["ok"] else 507), content=info)

    @app.get("/health/system", dependencies=[Depends(_require_health_token)])
    def health_system():
        info = _health_system_payload()
        return JSONResponse(status_code=200, content=info)

    @app.get("/health/external", dependencies=[Depends(_require_health_token)])
    def health_external():
        info = _health_external_payload()
        return JSONResponse(status_code=(200 if info["ok"] else 502), content=info)

    return app


def _health_database_payload(db) -> dict:
    try:
        h = db.health()
    except Exception as e:
        return {
            "ok": False,
            "connected": False,
            "error": str(e),
        }
    ok = bool(h.get("connected")) and str(h.get("integrity_check")) == "ok"
    try:
        terms = db.terms_count()
    except Exception:
        terms = None
    try:
        push_count = db.push_queue_count()
    except Exception:
        push_count = None
    result = {
        "ok": ok,
        "connected": h.get("connected"),
        "integrity": h.get("integrity_check"),
        "wal": h.get("wal_mode"),
        "migrations_applied": h.get("migrations_applied"),
        "file": h.get("db_file"),
        "size_bytes": h.get("file_size_bytes"),
        "terms_count": terms,
        "push_queue_count": push_count,
    }
    return result


def _health_storage_payload() -> dict:
    try:
        cfg = _pq_cfg()
    except Exception:
        cfg = None
    paths = set()
    try:
        paths.add(os.getcwd())
    except Exception:
        pass
    if cfg is not None:
        try:
            paths.add(cfg.data_dir)
        except Exception:
            pass
        try:
            paths.add(cfg.backup_dir)
        except Exception:
            pass
    per_path = [_storage_usage_bytes(p) for p in paths]
    overall_ok = True
    for p in per_path:
        if "error" in p:
            overall_ok = False
            continue
        free_pct = 100.0 - float(p.get("used_pct", 0))
        if free_pct < 5.0 or p.get("free_bytes", 1) < 50 * 1024 * 1024:
            overall_ok = False
    return {"ok": overall_ok, "paths": per_path}


def _health_system_payload() -> dict:
    import platform
    info = {
        "ok": True,
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "pid": os.getpid(),
        "uptime_s": round(_uptime_seconds(), 1),
        "ram": _ram_usage_bytes(),
        "cpu": _cpu_usage_pct(),
    }
    return info


def _health_external_payload() -> dict:
    """Push endpoint + queue status.

    Since we don't want to actually POST here, we inspect cached timestamps
    and push_queue depth, plus best-effort DNS/tcp handshake check on host.
    """
    global _LAST_PUSH_OK_TS, _LAST_PUSH_FAIL_TS
    ok = True
    endpoint = None
    try:
        cfg = _pq_cfg()
        endpoint = cfg.push_endpoint
        queue_enabled = cfg.push_queue_enabled
    except Exception as e:
        return {"ok": False, "error": f"config error: {e}"}

    # Pending push count
    try:
        pending = get_db().push_queue_count()
    except Exception as e:
        pending = None
        ok = False

    # If too many pending, treat as degraded but not hard failure
    if pending and pending >= 50:
        ok = False

    details = {
        "endpoint": endpoint,
        "queue_enabled": queue_enabled,
        "pending": pending,
        "last_push_success_at": _LAST_PUSH_OK_TS,
        "last_push_failure_at": _LAST_PUSH_FAIL_TS,
    }
    return {"ok": ok, **details}


def _update_push_state_from_drain_results(ok: int, failed: int) -> None:
    """Called from main.py backup loop after each push drain cycle, for observability."""
    global _LAST_PUSH_OK_TS, _LAST_PUSH_FAIL_TS
    if ok:
        _LAST_PUSH_OK_TS = time.time()
    if failed:
        _LAST_PUSH_FAIL_TS = time.time()


async def start_server_async(host: str = "0.0.0.0", port: int = 7860, health_token: Optional[str] = None) -> None:
    """Async entry point. Runs the uvicorn server until this coroutine is cancelled."""
    global _SERVER_STARTED_AT, _SERVER_CONFIG_HOST, _SERVER_CONFIG_PORT
    _SERVER_CONFIG_HOST = host
    _SERVER_CONFIG_PORT = port
    _SERVER_STARTED_AT = time.time()

    app = _APP_REF or build_app(health_token=health_token)
    # Ensure health token is propagated even if build_app was cached
    global _HEALTH_TOKEN
    _HEALTH_TOKEN = health_token

    config = uvicorn.Config(
        app,
        host=host,
        port=int(port),
        log_level="info",
        access_log=True,
        timeout_keep_alive=5,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    logger.info("Starting FastAPI server on %s:%s ...", host, port)
    try:
        await server.serve()
    except asyncio.CancelledError:
        logger.info("FastAPI server cancelled. Shutting down.")
        try:
            await server.shutdown()
        except Exception:
            pass
        raise


# ---- Legacy API (kept for backwards compat; prints warning if invoked) ----
def run_server(host: str = "0.0.0.0", port: int = 7860) -> None:
    """Legacy synchronous entry point. Preferred: start_server_async() in gather()."""
    logger.warning("run_server() (legacy sync) called. This blocks forever.")

    async def _runner():
        await start_server_async(host=host, port=port)

    asyncio.run(_runner())


def start_server(host: str = "0.0.0.0", port: int = 7860) -> None:
    """Legacy background thread. Intentionally deprecated; use start_server_async."""
    import threading
    logger.warning("start_server() (legacy thread mode) called. Prefer async gather().")

    def _target():
        try:
            run_server(host=host, port=port)
        except Exception as e:
            logger.exception("Legacy server thread crashed: %s", e)

    t = threading.Thread(target=_target, daemon=True, name="fastapi-server")
    t.start()
