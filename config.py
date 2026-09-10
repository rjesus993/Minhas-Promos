"""
Configuration loader for Minhas Promo.

Validates required environment variables, provides sensible defaults for
every tunable, and exposes a single frozen data object via load_config().
"""

import os
import logging
import warnings
from typing import Optional, Any
from dataclasses import dataclass, field, asdict
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CONFIG_CACHE: Optional["AppConfig"] = None


@dataclass(frozen=True)
class AppConfig:
    """Immutable validated configuration. All paths stored as absolute paths."""

    # ---- Telegram (required at startup for userbot operation) ----
    api_id: int
    api_hash: str
    telegram_session: str
    phone_number: str = ""
    termos_env: list[str] = field(default_factory=list)

    # ---- Dead config vars (reserved for future use) ----
    # BOT_TOKEN and DESTINO_ID were loaded by legacy code but never used.
    # Keep them loaded to avoid surprising users; log a warning if set.
    bot_token: Optional[str] = None
    destino_id: Any = None

    # ---- Database ----
    db_path: str = "./data/minhas_promo.db"
    migrations_dir: str = "./migrations"
    backup_dir: str = "./data/backups"
    data_dir: str = "./data"
    db_wal: bool = True
    db_busy_timeout_ms: int = 30000
    db_synchronous: str = "NORMAL"
    auto_restore_on_corruption: bool = False

    # ---- Backup ----
    backup_interval_seconds: int = 86400  # 24h
    backup_retention: int = 7
    backup_ratelimit_seconds: int = 3600  # max 1 backup/hour even if many commands trigger

    # ---- Push notifications ----
    push_endpoint: str = "https://monitor-automacao.vercel.app/api/notify"
    push_timeout_seconds: int = 10
    push_max_retries: int = 20
    push_backoff_base_seconds: int = 60
    push_queue_enabled: bool = True

    # ---- HTTP Server ----
    server_host: str = "0.0.0.0"
    server_port: int = 7860

    # ---- Security ----
    health_token: Optional[str] = None

    # ---- Crash recovery ----
    restart_delay_seconds: int = 30

    # ---- Telegram bot notifications target ID / channel future use ----
    # (legacy, reserved)

    def to_dict(self) -> dict:
        """Safe dict copy for logging (caller should redact secrets)."""
        return asdict(self)


def _abs(base_dir: str, path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(base_dir, path))


def _coerce_int(name: str, raw: Optional[str], default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError as e:
        raise ValueError(f"Variável de ambiente {name} deve ser um número inteiro (recebido: '{raw}')") from e


def _coerce_bool(name: str, raw: Optional[str], default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    r = str(raw).strip().lower()
    if r in ("1", "true", "yes", "sim", "on", "s"):
        return True
    if r in ("0", "false", "no", "não", "nao", "off", "n"):
        return False
    raise ValueError(f"Variável de ambiente {name} deve ser booleana (true/false), recebeu '{raw}'")


def _required(name: str, raw: Optional[str]) -> str:
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"Variável de ambiente {name} não definida")
    return str(raw).strip()


def load_config(force_reload: bool = False) -> AppConfig:
    """Return validated AppConfig. Result is cached; set force_reload=True to rescan env."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and not force_reload:
        return _CONFIG_CACHE

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # ----- Telegram (REQUIRED) -----
    api_id_raw = _required("API_ID", os.getenv("API_ID"))
    try:
        api_id = int(api_id_raw)
    except ValueError as e:
        raise ValueError(f"Variável de ambiente API_ID deve ser um número inteiro (recebido: '{api_id_raw}')") from e
    api_hash = _required("API_HASH", os.getenv("API_HASH"))
    telegram_session = _required("TELEGRAM_SESSION", os.getenv("TELEGRAM_SESSION"))

    phone_number = (os.getenv("PHONE_NUMBER") or "").strip()

    termos_raw = (os.getenv("TERMOS") or "").strip()
    termos_env = [t.strip() for t in termos_raw.split(",") if t.strip()]

    # ----- Legacy dead config -----
    bot_token = os.getenv("BOT_TOKEN")
    if bot_token and str(bot_token).strip():
        logger.info("BOT_TOKEN definido mas não usado atualmente (reservado para uso futuro).")
    destino_id_raw = (os.getenv("DESTINO_ID") or "").strip()
    destino_id: Any = destino_id_raw
    if destino_id_raw:
        try:
            destino_id = int(destino_id_raw)
        except ValueError:
            destino_id = destino_id_raw
        logger.info("DESTINO_ID definido mas não usado atualmente (reservado para uso futuro).")

    # ----- Database -----
    db_path = _abs(base_dir, os.getenv("DB_PATH") or "./data/minhas_promo.db")
    migrations_dir = _abs(base_dir, os.getenv("MIGRATIONS_DIR") or "./migrations")
    data_dir = os.path.dirname(db_path)
    backup_dir = _abs(base_dir, os.getenv("BACKUP_DIR") or os.path.join(data_dir, "backups"))
    db_wal = _coerce_bool("DB_WAL", os.getenv("DB_WAL"), True)
    db_busy_timeout_ms = _coerce_int("DB_BUSY_TIMEOUT_MS", os.getenv("DB_BUSY_TIMEOUT_MS"), 30000)
    sync_raw = (os.getenv("DB_SYNCHRONOUS") or "NORMAL").strip().upper()
    if sync_raw not in ("OFF", "NORMAL", "FULL", "EXTRA"):
        raise ValueError(f"DB_SYNCHRONOUS deve ser OFF|NORMAL|FULL|EXTRA (recebido: '{sync_raw}')")
    db_synchronous = sync_raw
    auto_restore_on_corruption = _coerce_bool(
        "AUTO_RESTORE_ON_CORRUPTION",
        os.getenv("AUTO_RESTORE_ON_CORRUPTION"),
        False,
    )

    # ----- Backup -----
    backup_interval_seconds = _coerce_int(
        "BACKUP_INTERVAL_SECONDS", os.getenv("BACKUP_INTERVAL_SECONDS"), 86400
    )
    backup_retention = _coerce_int("BACKUP_RETENTION", os.getenv("BACKUP_RETENTION"), 7)
    if backup_retention < 1:
        raise ValueError(f"BACKUP_RETENTION deve ser >= 1 (recebido: {backup_retention})")
    backup_ratelimit_seconds = _coerce_int(
        "BACKUP_RATELIMIT_SECONDS", os.getenv("BACKUP_RATELIMIT_SECONDS"), 3600
    )

    # ----- Push -----
    push_endpoint = (
        os.getenv("PUSH_ENDPOINT") or "https://monitor-automacao.vercel.app/api/notify"
    ).strip()
    push_timeout_seconds = _coerce_int(
        "PUSH_TIMEOUT_SECONDS", os.getenv("PUSH_TIMEOUT_SECONDS"), 10
    )
    push_max_retries = _coerce_int("PUSH_MAX_RETRIES", os.getenv("PUSH_MAX_RETRIES"), 20)
    push_backoff_base_seconds = _coerce_int(
        "PUSH_BACKOFF_BASE", os.getenv("PUSH_BACKOFF_BASE"), 60
    )
    push_queue_enabled = _coerce_bool("PUSH_QUEUE_ENABLED", os.getenv("PUSH_QUEUE_ENABLED"), True)

    # ----- Server -----
    server_host = (os.getenv("SERVER_HOST") or "0.0.0.0").strip()
    server_port = _coerce_int("SERVER_PORT", os.getenv("SERVER_PORT"), 7860)
    if not (1 <= server_port <= 65535):
        raise ValueError(f"SERVER_PORT deve estar entre 1 e 65535 (recebido: {server_port})")

    # ----- Security -----
    ht = os.getenv("HEALTH_TOKEN")
    health_token = str(ht).strip() if ht and str(ht).strip() else None

    # ----- Crash recovery -----
    restart_delay_seconds = _coerce_int(
        "RESTART_DELAY_SECONDS", os.getenv("RESTART_DELAY_SECONDS"), 30
    )

    # ----- Warning: Supabase vars no longer needed -----
    if os.getenv("SUPABASE_URL") or os.getenv("SUPABASE_KEY"):
        logger.warning(
            "SUPABASE_URL / SUPABASE_KEY detectados mas NÃO são mais necessários em tempo de execução. "
            "Se você quer migrar dados do Supabase antigo, execute: "
            "python scripts/import_supabase_to_sqlite.py --overwrite"
        )

    cfg = AppConfig(
        api_id=api_id,
        api_hash=api_hash,
        telegram_session=telegram_session,
        phone_number=phone_number,
        termos_env=termos_env,
        bot_token=bot_token or None,
        destino_id=destino_id if destino_id else None,
        db_path=db_path,
        migrations_dir=migrations_dir,
        backup_dir=backup_dir,
        data_dir=data_dir,
        db_wal=db_wal,
        db_busy_timeout_ms=db_busy_timeout_ms,
        db_synchronous=db_synchronous,
        auto_restore_on_corruption=auto_restore_on_corruption,
        backup_interval_seconds=backup_interval_seconds,
        backup_retention=backup_retention,
        backup_ratelimit_seconds=backup_ratelimit_seconds,
        push_endpoint=push_endpoint,
        push_timeout_seconds=push_timeout_seconds,
        push_max_retries=push_max_retries,
        push_backoff_base_seconds=push_backoff_base_seconds,
        push_queue_enabled=push_queue_enabled,
        server_host=server_host,
        server_port=server_port,
        health_token=health_token,
        restart_delay_seconds=restart_delay_seconds,
    )

    # Ensure directories exist (best-effort at load time)
    for d in (cfg.data_dir, cfg.backup_dir, os.path.dirname(cfg.db_path)):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

    _CONFIG_CACHE = cfg
    return cfg


# ----- Backward-compatible module-level names (legacy code references these) -----
# Keep the original symbols alive for bot.py imports that do:
#   from config import API_ID, API_HASH, SESSION, PHONE_NUMBER, ...
# This lets us migrate incrementally without rewriting bot.py all at once.

_CFG = None


def _ensure_legacy() -> AppConfig:
    global _CFG
    if _CFG is None:
        _CFG = load_config()
    return _CFG


# These module-level names are evaluated lazily via __getattr__ to avoid
# validation errors on import when someone just wants to read symbols.
# But for backward compat they must raise ValueError at import-time if missing,
# matching the old pre-migration behavior. So we validate eagerly here:

_VALIDATED_CFG = load_config()  # fails fast with Portuguese messages if required vars missing

API_ID = _VALIDATED_CFG.api_id
API_HASH = _VALIDATED_CFG.api_hash
SESSION = _VALIDATED_CFG.telegram_session
PHONE_NUMBER = _VALIDATED_CFG.phone_number
BOT_TOKEN = _VALIDATED_CFG.bot_token or ""
DESTINO_ID = _VALIDATED_CFG.destino_id or ""
TERMOS_ENV = ",".join(_VALIDATED_CFG.termos_env)
TERMOS = _VALIDATED_CFG.termos_env
