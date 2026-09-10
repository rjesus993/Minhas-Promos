"""
Logging configuration with rotating files + secret redaction.

SecretFilter is idempotent: running it twice on the same message (once via
logger, once via handler) still produces exactly one [REDACTED] replacement.
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable, Optional, List


LOG_DIR = None  # overridden by setup_logging(log_dir=...)


class SecretFilter(logging.Filter):
    """Redact configured secret strings from log records before emit.

    Each secret is replaced with the token ``[REDACTED]``. To protect
    short/accidental secrets, anything shorter than 4 chars is ignored
    with a silent skip. Secrets are escaped as fixed strings (not regex).
    """

    REDACTED = "[REDACTED]"

    def __init__(self, secrets: Optional[Iterable[str]] = None, name: str = ""):
        super().__init__(name=name)
        self._secrets: List[str] = []
        self._replacement_pairs: List[tuple] = []
        self._rebuild_pairs = True
        if secrets:
            self.update_secrets(secrets)

    def update_secrets(self, secrets: Iterable[str]) -> None:
        """Reset the secret list with a new iterable.

        Accepts strings or None; empty strings and short strings (<= 3 chars)
        are silently ignored to avoid blanket replacements of common tokens.
        """
        cleaned: List[str] = []
        for s in secrets:
            if s is None:
                continue
            s = str(s)
            if len(s) < 4:
                continue
            if s in cleaned:
                continue
            cleaned.append(s)
        # Sort longest first so "abcdefgh" is replaced before substring "abcd"
        cleaned.sort(key=len, reverse=True)
        self._secrets = cleaned
        self._replacement_pairs = [
            (re.compile(re.escape(s)), self.REDACTED) for s in cleaned
        ]
        self._rebuild_pairs = False

    def filter(self, record: logging.LogRecord) -> bool:
        # Always pass the record through; we only redact, not drop.
        if self._rebuild_pairs or self._replacement_pairs:
            if isinstance(record.msg, str):
                new_msg = record.msg
                for pattern, repl in self._replacement_pairs:
                    new_msg = pattern.sub(repl, new_msg)
                if new_msg != record.msg:
                    record.msg = new_msg
            try:
                if record.args:
                    if isinstance(record.args, tuple):
                        new_args = []
                        changed = False
                        for a in record.args:
                            if isinstance(a, str):
                                na = a
                                for pattern, repl in self._replacement_pairs:
                                    na = pattern.sub(repl, na)
                                if na != a:
                                    changed = True
                                new_args.append(na)
                            else:
                                new_args.append(a)
                        if changed:
                            record.args = tuple(new_args)
                    elif isinstance(record.args, dict):
                        new_args = {}
                        changed = False
                        for k, v in record.args.items():
                            if isinstance(v, str):
                                nv = v
                                for pattern, repl in self._replacement_pairs:
                                    nv = pattern.sub(repl, nv)
                                if nv != v:
                                    changed = True
                                new_args[k] = nv
                            else:
                                new_args[k] = v
                        if changed:
                            record.args = new_args
            except Exception:
                # Never let a filter error break logging
                pass
        return True


def _collect_known_secrets() -> List[str]:
    """Best-effort secret collection at setup time.

    Tries to read env vars directly (config may not be loaded yet because
    config triggers module-level validation that depends on .env presence).
    Anything missed can be added later via ``SecretFilter.update_secrets``.
    """
    candidates = {
        os.getenv("API_HASH"),
        os.getenv("TELEGRAM_SESSION"),
        os.getenv("HEALTH_TOKEN"),
        os.getenv("SUPABASE_KEY"),
        os.getenv("SUPABASE_URL"),
        os.getenv("BOT_TOKEN"),
    }
    return [str(s) for s in candidates if s and len(str(s)) >= 4]


def setup_logging(log_dir: Optional[str] = None, extra_secrets: Optional[Iterable[str]] = None) -> tuple[logging.Logger, SecretFilter]:
    """Create rotating file + console loggers, attach SecretFilter everywhere.

    Returns ``(root_logger, secret_filter_instance)`` so the caller can
    later invoke ``secret_filter.update_secrets(new_list)`` after config
    is fully loaded.
    """
    global LOG_DIR

    if log_dir is None:
        # Place logs next to data/ by default (sibling of project root by convention)
        base = Path(os.path.dirname(os.path.abspath(__file__)))
        log_dir = base / "data"
    log_dir = Path(log_dir)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Worst case fall back to cwd
        log_dir = Path.cwd()
    LOG_DIR = str(log_dir)

    log_path = log_dir / "bot.log"

    logger = logging.getLogger()
    # Reset previous handlers so repeated setup_logging() calls in tests don't double-print
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    file_handler = RotatingFileHandler(
        str(log_path), maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    secrets: List[str] = list(_collect_known_secrets())
    if extra_secrets:
        for s in extra_secrets:
            if s and s not in secrets:
                secrets.append(s)
    secret_filter = SecretFilter(secrets=secrets)

    # Double-attach: logger filter runs first, then handler filters catch
    # records that may be submitted directly to a handler by legacy code.
    logger.addFilter(secret_filter)
    file_handler.addFilter(secret_filter)
    console_handler.addFilter(secret_filter)

    logger.debug("Logging configured; secrets=%d redacted via filter.", len(secrets))
    return logger, secret_filter
