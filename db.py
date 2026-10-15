"""
Database abstraction layer for Minhas Promo.

Replaces Supabase cloud dependency with local SQLite3 (stdlib).
Implements migration system, atomic transactions, backup, and health checks.
"""

import os
import re
import functools
import threading
import glob
import gzip
import shutil
import sqlite3
import logging
import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join("data", "minhas_promo.db")
DEFAULT_MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def _synchronized(fn):
    """Serializa o acesso à conexão única (compartilhada entre a thread do
    event loop e threads de executor). Sem isso, transações de threads
    diferentes se misturam na mesma conexão (commit/rollback cruzados)."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


def _coerce_path(path: str) -> str:
    """Return absolute path, create parent directory if missing."""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    return abs_path


class SQLiteDatabase:
    """SQLite-backed implementation of the persistence layer."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        migrations_dir: str = DEFAULT_MIGRATIONS_DIR,
        wal: bool = True,
        foreign_keys: bool = True,
        busy_timeout_ms: int = 30000,
        synchronous: str = "NORMAL",
    ):
        self.db_path = _coerce_path(db_path)
        self.migrations_dir = migrations_dir
        self._wal = wal
        self._foreign_keys = foreign_keys
        self._busy_timeout_ms = busy_timeout_ms
        self._synchronous = synchronous.upper()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()
        self._connect()
        self.ensure_schema()

    # ----- Connection Management -----
    @_synchronized
    def _connect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        pragmas = [
            f"PRAGMA busy_timeout = {self._busy_timeout_ms}",
            f"PRAGMA foreign_keys = {'ON' if self._foreign_keys else 'OFF'}",
            f"PRAGMA synchronous = {self._synchronous}",
        ]
        if self._wal:
            pragmas.append("PRAGMA journal_mode = WAL")
        for p in pragmas:
            try:
                self._conn.execute(p)
            except Exception as e:
                logger.warning("Pragma failed (%s): %s", p, e)

    @_synchronized
    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ----- Migration System -----
    @_synchronized
    def _applied_migrations(self) -> List[int]:
        try:
            cur = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_migrations'"
            )
            if not cur.fetchone():
                return []
            rows = self._conn.execute("SELECT version FROM _schema_migrations ORDER BY version").fetchall()
            return [int(r["version"]) for r in rows]
        except Exception:
            return []

    @staticmethod
    def _extract_migration_version(filename: str) -> Optional[int]:
        base = os.path.basename(filename)
        m = re.match(r"^(\d+)", base)
        if m:
            return int(m.group(1))
        return None

    def _list_migration_files(self) -> List[tuple]:
        if not os.path.isdir(self.migrations_dir):
            logger.warning("Migrations dir not found: %s", self.migrations_dir)
            return []
        files = glob.glob(os.path.join(self.migrations_dir, "*.sql"))
        out = []
        for f in files:
            v = self._extract_migration_version(f)
            if v is not None:
                out.append((v, f))
        out.sort(key=lambda x: x[0])
        return out

    @_synchronized
    def ensure_schema(self, backup_before_migration: bool = True) -> List[int]:
        applied = self._applied_migrations()
        files = self._list_migration_files()
        newly_applied = []
        for version, filepath in files:
            if version in applied:
                continue
            if backup_before_migration and applied:
                try:
                    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_dir = os.path.join(os.path.dirname(self.db_path), "backups")
                    os.makedirs(backup_dir, exist_ok=True)
                    backup_path = os.path.join(backup_dir, f"pre-migration-v{version}_{stamp}.db")
                    self.backup(backup_path)
                except Exception as e:
                    logger.warning("Failed pre-migration backup for v%d: %s", version, e)
            with open(filepath, "r", encoding="utf-8") as fh:
                sql = fh.read()
            try:
                logger.info("Applying migration v%d from %s", version, os.path.basename(filepath))
                with self._conn:
                    self._conn.executescript(sql)
                newly_applied.append(version)
            except Exception as e:
                logger.error("FAILED migration v%d: %s. Stopping.", version, e)
                raise RuntimeError(f"Migration v{version} failed: {e}") from e
        return newly_applied

    # ----- Core Term Interface -----
    @_synchronized
    def load_terms(self) -> List[str]:
        try:
            rows = self._conn.execute(
                "SELECT term FROM terms ORDER BY created_at ASC, id ASC"
            ).fetchall()
            return [r["term"] for r in rows]
        except Exception as e:
            logger.error("load_terms failed: %s", e)
            return []

    @_synchronized
    def save_terms(self, terms: List[str]) -> bool:
        try:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("DELETE FROM terms")
                for t in terms:
                    t_clean = str(t).strip()
                    if not t_clean:
                        continue
                    self._conn.execute(
                        "INSERT INTO terms (term) VALUES (?)",
                        (t_clean,),
                    )
                self._conn.commit()
            return True
        except sqlite3.IntegrityError as e:
            logger.error("save_terms integrity error: %s", e)
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error("save_terms failed: %s", e)
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False

    @_synchronized
    def add_term(self, term: str) -> bool:
        t_clean = str(term).strip()
        if not t_clean:
            return False
        try:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "INSERT INTO terms (term) VALUES (?)",
                    (t_clean,),
                )
                self._conn.execute(
                    "INSERT INTO term_history (term, action) VALUES (?, 'added')",
                    (t_clean,),
                )
                self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            logger.warning("add_term duplicate: %s", t_clean)
            try:
                self._conn.rollback()
            except Exception:
                pass
            try:
                self._conn.execute(
                    "INSERT INTO term_history (term, action) VALUES (?, 'added')",
                    (t_clean,),
                )
                self._conn.commit()
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error("add_term failed: %s", e)
            try:
                self._conn.rollback()
            except Exception:
                pass
            return False

    @_synchronized
    def remove_term(self, term: str) -> bool:
        """Remove o termo (sem diferenciar maiúsculas) e registra histórico
        apenas se algo foi de fato removido."""
        t_clean = str(term).strip()
        if not t_clean:
            return False
        try:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM terms WHERE term = ? COLLATE NOCASE",
                    (t_clean,),
                )
                deleted = cur.rowcount > 0
                if deleted:
                    self._conn.execute(
                        "INSERT INTO term_history (term, action) VALUES (?, 'removed')",
                        (t_clean,),
                    )
            return deleted
        except Exception as e:
            logger.error("remove_term failed: %s", e)
            return False

    @_synchronized
    def get_term_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            rows = self._conn.execute(
                "SELECT id, term, action, timestamp FROM term_history "
                "ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_term_history failed: %s", e)
            return []

    # ----- Source Chats (Fase 3) -----
    @_synchronized
    def list_source_chats(self) -> List[Dict[str, Any]]:
        """Retorna todas as entradas de source_chats."""
        try:
            rows = self._conn.execute(
                "SELECT chat_id, title, mode, added_at FROM source_chats "
                "ORDER BY mode ASC, title ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("list_source_chats failed: %s", e)
            return []

    @_synchronized
    def source_chats_mode(self) -> str:
        """Retorna 'allow' se houver qualquer entrada com mode='allow',
        senão 'deny' se só houver 'deny', senão 'all' (tabela vazia)."""
        try:
            rows = self._conn.execute(
                "SELECT DISTINCT mode FROM source_chats"
            ).fetchall()
            modes = {r["mode"] for r in rows}
            if "allow" in modes:
                return "allow"
            if "deny" in modes:
                return "deny"
            return "all"
        except Exception:
            return "all"

    @_synchronized
    def add_source_chat(self, chat_id: int, title: str = "", mode: str = "allow") -> bool:
        """Adiciona ou atualiza um chat na whitelist/blacklist."""
        if mode not in ("allow", "deny"):
            logger.error("add_source_chat: mode inválido %r", mode)
            return False
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO source_chats (chat_id, title, mode) VALUES (?, ?, ?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, mode=excluded.mode",
                    (int(chat_id), str(title)[:200], mode),
                )
            return True
        except Exception as e:
            logger.error("add_source_chat failed: %s", e)
            return False

    @_synchronized
    def remove_source_chat(self, chat_id: int) -> bool:
        """Remove um chat da whitelist/blacklist."""
        try:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM source_chats WHERE chat_id = ?",
                    (int(chat_id),),
                )
                return cur.rowcount > 0
        except Exception as e:
            logger.error("remove_source_chat failed: %s", e)
            return False

    @_synchronized
    def clear_source_chats(self) -> int:
        """Limpa a tabela. Retorna quantas linhas foram removidas."""
        try:
            with self._conn:
                cur = self._conn.execute("DELETE FROM source_chats")
                return cur.rowcount
        except Exception as e:
            logger.error("clear_source_chats failed: %s", e)
            return 0

    @_synchronized
    def is_chat_allowed(self, chat_id: int) -> bool:
        """Decide se um chat deve ser monitorado, respeitando o mode atual."""
        mode = self.source_chats_mode()
        if mode == "all":
            return True
        try:
            row = self._conn.execute(
                "SELECT 1 FROM source_chats WHERE chat_id = ? AND mode = ? LIMIT 1",
                (int(chat_id), mode),
            ).fetchone()
            if mode == "allow":
                return row is not None
            else:
                return row is None
        except Exception as e:
            logger.error("is_chat_allowed failed: %s", e)
            return True

    # ----- Deduplicação (Fase 4) -----
    @_synchronized
    def was_hash_seen(self, h: str, window_seconds: int = 1800) -> bool:
        """Retorna True se o hash foi visto nos últimos N segundos."""
        try:
            row = self._conn.execute(
                "SELECT last_seen FROM seen_hashes WHERE hash = ?",
                (str(h),),
            ).fetchone()
            if not row:
                return False
            last_seen = row["last_seen"]
            try:
                dt = datetime.datetime.fromisoformat(
                    str(last_seen).replace("Z", "+00:00")
                )
                now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                delta = (now_utc - dt.replace(tzinfo=None)).total_seconds()
                return delta < window_seconds
            except Exception:
                return False
        except Exception as e:
            logger.error("was_hash_seen failed: %s", e)
            return False

    @_synchronized
    def remember_hash(self, h: str, term: str = "") -> None:
        """Grava (ou atualiza) o hash na tabela seen_hashes."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO seen_hashes (hash, term) VALUES (?, ?) "
                    "ON CONFLICT(hash) DO UPDATE SET "
                    "last_seen = CURRENT_TIMESTAMP, "
                    "hit_count = hit_count + 1",
                    (str(h), str(term)[:200]),
                )
        except Exception as e:
            logger.error("remember_hash failed: %s", e)

    @_synchronized
    def purge_seen_hashes(self, older_than_seconds: int = 86400) -> int:
        """Remove hashes mais antigos que N segundos. Retorna contagem."""
        try:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM seen_hashes WHERE "
                    "last_seen < datetime(CURRENT_TIMESTAMP, ?)",
                    (f"-{int(older_than_seconds)} seconds",),
                )
                return cur.rowcount
        except Exception as e:
            logger.error("purge_seen_hashes failed: %s", e)
            return 0

    @_synchronized
    def count_seen_hashes(self) -> int:
        try:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM seen_hashes").fetchone()
            return int(row["c"]) if row else 0
        except Exception as e:
            logger.error("count_seen_hashes failed: %s", e)
            return 0

    # ----- Push Queue Support -----
    @_synchronized
    def enqueue_push(self, html: str) -> int:
        try:
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO push_queue (html, next_attempt_at) VALUES (?, CURRENT_TIMESTAMP)",
                    (html,),
                )
                return int(cur.lastrowid)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                logger.warning("push_queue table missing; apply migration 002? (%s)", e)
            raise
        except Exception as e:
            logger.error("enqueue_push failed: %s", e)
            raise

    @_synchronized
    def push_queue_count(self) -> int:
        try:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM push_queue").fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            return 0

    @_synchronized
    def pop_due_pushes(self, limit: int = 100) -> List[Dict[str, Any]]:
        try:
            rows = self._conn.execute(
                "SELECT id, html, retry_count, next_attempt_at, last_error, created_at "
                "FROM push_queue WHERE next_attempt_at <= CURRENT_TIMESTAMP "
                "ORDER BY next_attempt_at ASC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                out.append({
                    "id": r["id"],
                    "html": r["html"],
                    "retry_count": r["retry_count"],
                    "next_attempt_at": r["next_attempt_at"],
                    "last_error": r["last_error"],
                    "created_at": r["created_at"],
                })
            return out
        except Exception as e:
            logger.error("pop_due_pushes failed: %s", e)
            return []

    @_synchronized
    def mark_push_success(self, push_id: int) -> None:
        try:
            with self._conn:
                self._conn.execute("DELETE FROM push_queue WHERE id = ?", (push_id,))
        except Exception as e:
            logger.error("mark_push_success failed: %s", e)

    @_synchronized
    def mark_push_failure(self, push_id: int, error: str, backoff_seconds: int, max_retries: int = 20) -> bool:
        try:
            with self._conn:
                row = self._conn.execute(
                    "SELECT retry_count FROM push_queue WHERE id = ?", (push_id,)
                ).fetchone()
                if not row:
                    return False
                rc = int(row["retry_count"]) + 1
                if rc > max_retries:
                    self._conn.execute("DELETE FROM push_queue WHERE id = ?", (push_id,))
                    logger.warning("Dropped push id=%d after %d retries. Last error: %s", push_id, rc, error)
                    return False
                self._conn.execute(
                    "UPDATE push_queue SET retry_count=?, last_error=?, "
                    "next_attempt_at = datetime(CURRENT_TIMESTAMP, ?) "
                    "WHERE id=?",
                    (rc, str(error)[:500], f"+{int(backoff_seconds)} seconds", push_id),
                )
                return True
        except Exception as e:
            logger.error("mark_push_failure failed: %s", e)
            return False

    # ----- Health -----
    @_synchronized
    def health(self, full: bool = True) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "connected": False,
            "db_file": self.db_path,
            "file_size_bytes": 0,
            "integrity_check": "unknown",
            "wal_mode": None,
            "migrations_applied": self._applied_migrations(),
        }
        if os.path.exists(self.db_path):
            try:
                result["file_size_bytes"] = int(os.path.getsize(self.db_path))
            except Exception:
                pass
        try:
            pragma = "integrity_check" if full else "quick_check"
            row = self._conn.execute(f"PRAGMA {pragma}").fetchone()
            result["integrity_check"] = str(row[0]) if row else "no-row"
        except Exception as e:
            result["integrity_check"] = f"error: {e}"
        try:
            row = self._conn.execute("PRAGMA journal_mode").fetchone()
            mode = str(row[0]).lower() if row else None
            result["wal_mode"] = (mode == "wal")
        except Exception:
            pass
        result["connected"] = result["integrity_check"] == "ok" or (
            isinstance(result["integrity_check"], str) and "ok" in result["integrity_check"].lower()
        )
        return result

    @_synchronized
    def terms_count(self) -> int:
        try:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM terms").fetchone()
            return int(row["c"]) if row else 0
        except Exception:
            return 0

    # ----- Backup / Restore -----
    @_synchronized
    def backup(self, dest_path: str, verify: bool = True, compress: bool = False,
               _attempt: int = 1) -> str:
        dest_abs = _coerce_path(dest_path)
        if compress and not dest_abs.endswith(".gz"):
            dest_abs = dest_abs + ".gz"
        tmp_uncompressed = dest_abs
        if compress:
            tmp_uncompressed = dest_abs[:-3] + ".tmp"
        try:
            dest_conn = sqlite3.connect(tmp_uncompressed)
            try:
                with dest_conn:
                    self._conn.backup(dest_conn, pages=1000, progress=None)
            finally:
                try:
                    dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                dest_conn.close()
            if compress:
                with open(tmp_uncompressed, "rb") as f_in:
                    with gzip.open(dest_abs, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                try:
                    os.remove(tmp_uncompressed)
                except Exception:
                    pass
            actual_final = dest_abs
        except Exception as e:
            logger.error("Backup copy failed: %s", e)
            raise
        if verify:
            try:
                ok = self._verify_backup_file(actual_final)
                if not ok:
                    raise RuntimeError(f"Backup integrity_check != ok on {actual_final}")
            except Exception:
                logger.warning("First backup verify failed, retrying once...")
                try:
                    if os.path.exists(actual_final):
                        os.remove(actual_final)
                except Exception:
                    pass
                if _attempt >= 3:
                    raise RuntimeError(
                        f"Backup falhou na verificação após {_attempt} tentativas"
                    )
                return self.backup(dest_path, verify=True, compress=compress,
                                   _attempt=_attempt + 1)
        return actual_final

    @staticmethod
    def _verify_backup_file(path: str) -> bool:
        db_to_open = path
        tmp_path = None
        if path.endswith(".gz"):
            tmp_path = path + ".verify_tmp.db"
            with gzip.open(path, "rb") as f_in:
                with open(tmp_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            db_to_open = tmp_path
        try:
            conn = sqlite3.connect(db_to_open)
            try:
                row = conn.execute("PRAGMA integrity_check").fetchone()
                ok = (row is not None) and (str(row[0]).lower() == "ok")
            finally:
                conn.close()
            return ok
        except Exception as e:
            logger.error("Verify backup %s failed: %s", path, e)
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @classmethod
    def restore_from_backup(cls, backup_path: str, dest_db_path: str, safety_copy: bool = True) -> str:
        dest_abs = _coerce_path(dest_db_path)
        safety_path = None
        if safety_copy and os.path.exists(dest_abs):
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safety_path = dest_abs + f".before-restore-{stamp}"
            shutil.copy2(dest_abs, safety_path)
            wal_ext = dest_abs + "-wal"
            shm_ext = dest_abs + "-shm"
            if os.path.exists(wal_ext):
                try:
                    shutil.copy2(wal_ext, safety_path + "-wal")
                except Exception:
                    pass
            if os.path.exists(shm_ext):
                try:
                    shutil.copy2(shm_ext, safety_path + "-shm")
                except Exception:
                    pass
        if not cls._verify_backup_file(backup_path):
            raise RuntimeError("Refusing restore: backup failed integrity check")
        tmp_path = dest_abs + ".restore-in-progress.tmp"
        if backup_path.endswith(".gz"):
            with gzip.open(backup_path, "rb") as f_in:
                with open(tmp_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
        else:
            shutil.copy2(backup_path, tmp_path)
        if os.path.exists(dest_abs):
            try:
                os.remove(dest_abs)
            except Exception:
                pass
        os.replace(tmp_path, dest_abs)
        for ext in ("-wal", "-shm", "-journal"):
            p = dest_abs + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        return safety_path or ""


    @_synchronized
    def restore(self, backup_path: str, safety_copy: bool = True) -> str:
        """Restaura este banco a partir de um backup, com a conexão
        fechada durante a troca de arquivo e reaberta depois.
        (restore_from_backup é classmethod e exige dest_db_path; esta é a
        forma segura de usar a partir de uma instância em execução.)"""
        self.close()
        try:
            safety = type(self).restore_from_backup(
                backup_path, self.db_path, safety_copy=safety_copy
            )
        finally:
            self._connect()
        self.ensure_schema(backup_before_migration=False)
        return safety


# -----------------------------------------------------------------------------
# Module-level singleton interface
# -----------------------------------------------------------------------------
_db_singleton: Optional[SQLiteDatabase] = None


def _get_default_db_path() -> str:
    try:
        from config import load_config
        cfg = load_config()
        return cfg.db_path
    except Exception:
        return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def _get_default_migrations_dir() -> str:
    try:
        from config import load_config
        cfg = load_config()
        return cfg.migrations_dir
    except Exception:
        return os.environ.get("MIGRATIONS_DIR", DEFAULT_MIGRATIONS_DIR)


def get_db() -> SQLiteDatabase:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = SQLiteDatabase(
            db_path=_get_default_db_path(),
            migrations_dir=_get_default_migrations_dir(),
        )
    return _db_singleton


def reset_db_singleton() -> None:
    global _db_singleton
    if _db_singleton is not None:
        try:
            _db_singleton.close()
        except Exception:
            pass
    _db_singleton = None


# ----- Original-style module-level functions -----

def save_terms(terms: List[str]) -> bool:
    return get_db().save_terms(terms)


def load_terms() -> List[str]:
    return get_db().load_terms()


def add_term(term: str) -> bool:
    return get_db().add_term(term)


def remove_term(term: str) -> bool:
    return get_db().remove_term(term)


def get_term_history(limit: int = 50):
    return get_db().get_term_history(limit)


def ensure_schema(backup_before_migration: bool = True) -> List[int]:
    """Module-level wrapper: ensure schema is applied on the singleton DB."""
    return get_db().ensure_schema(backup_before_migration=backup_before_migration)


def health() -> Dict[str, Any]:
    """Module-level wrapper for health check."""
    return get_db().health()


def terms_count() -> int:
    """Module-level wrapper for terms count."""
    return get_db().terms_count()


# ----- Source Chats (Fase 3) — wrappers de módulo -----

def list_source_chats() -> List[Dict[str, Any]]:
    return get_db().list_source_chats()


def source_chats_mode() -> str:
    return get_db().source_chats_mode()


def add_source_chat(chat_id: int, title: str = "", mode: str = "allow") -> bool:
    return get_db().add_source_chat(chat_id, title, mode)


def remove_source_chat(chat_id: int) -> bool:
    return get_db().remove_source_chat(chat_id)


def clear_source_chats() -> int:
    return get_db().clear_source_chats()


def is_chat_allowed(chat_id: int) -> bool:
    return get_db().is_chat_allowed(chat_id)


# ----- Deduplicação (Fase 4) — wrappers de módulo -----

def was_hash_seen(h: str, window_seconds: int = 1800) -> bool:
    return get_db().was_hash_seen(h, window_seconds)


def remember_hash(h: str, term: str = "") -> None:
    get_db().remember_hash(h, term)


def purge_seen_hashes(older_than_seconds: int = 86400) -> int:
    return get_db().purge_seen_hashes(older_than_seconds)


def count_seen_hashes() -> int:
    return get_db().count_seen_hashes()