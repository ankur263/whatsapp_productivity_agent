from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
MEMORY_DIR = DATA_DIR / "memory_db"
FALLBACK_DB = DATA_DIR / "memory_fallback.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_user(user_id: str) -> str:
    val = re.sub(r"[^a-zA-Z0-9_]+", "_", user_id)
    return val.strip("_")[:64] or "default_user"


class _SimpleMemoryBackend:
    """SQLite fallback memory backend for environments without Chroma."""

    def __init__(self, db_path: Path = FALLBACK_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_mem_user ON memory(user_id)"
            )

    def remember(self, user_id: str, text: str, metadata: dict | None = None) -> None:
        metadata = metadata or {}
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO memory(user_id, text, metadata, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, text, json.dumps(metadata), _utc_now_iso()),
            )

    def recall(self, user_id: str, query: str, k: int = 3) -> list[str]:
        terms = [t.lower() for t in re.findall(r"\w+", query)]
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT text
                FROM memory
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 100
                """,
                (user_id,),
            ).fetchall()
        scored: list[tuple[int, str]] = []
        for row in rows:
            txt = row["text"]
            score = sum(txt.lower().count(t) for t in terms) if terms else 1
            if score > 0:
                scored.append((score, txt))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:k]]

    def get_memory_count(self, user_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS c FROM memory WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return int(row["c"])


class MemoryStore:
    """Persistent per-user memory with ChromaDB primary and SQLite fallback."""

    def __init__(self, backend: str = "chroma") -> None:
        self.backend_name = backend
        self.simple = _SimpleMemoryBackend()
        self.chroma_client = None
        self._disable_chroma_runtime = False
        self._init_chroma_if_available()

    def _init_chroma_if_available(self) -> None:
        if self.backend_name != "chroma":
            return
        try:
            import chromadb  # type: ignore

            MEMORY_DIR.mkdir(parents=True, exist_ok=True)
            self.chroma_client = chromadb.PersistentClient(path=str(MEMORY_DIR))
            logger.info("Memory backend initialized: chroma")
        except Exception as exc:  # pragma: no cover - environment dependent
            self.chroma_client = None
            logger.warning(
                "Chroma unavailable, falling back to SQLite memory backend: %s", exc
            )

    def _collection_name(self, user_id: str) -> str:
        return f"user_{_sanitize_user(user_id)}"

    def remember(self, user_id: str, text: str, metadata: dict | None = None) -> None:
        metadata = metadata or {}
        if self.chroma_client is not None and not self._disable_chroma_runtime:
            try:
                collection = self.chroma_client.get_or_create_collection(
                    name=self._collection_name(user_id)
                )
                collection.add(
                    ids=[f"{_utc_now_iso()}_{abs(hash(text)) % 10_000_000}"],
                    documents=[text],
                    metadatas=[metadata],
                )
                return
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning("Chroma write failed; fallback used: %s", exc)
                self._disable_chroma_runtime = True
        self.simple.remember(user_id, text, metadata)

    def recall(self, user_id: str, query: str, k: int = 3) -> list[str]:
        if self.chroma_client is not None and not self._disable_chroma_runtime:
            try:
                collection = self.chroma_client.get_or_create_collection(
                    name=self._collection_name(user_id)
                )
                res = collection.query(query_texts=[query], n_results=k)
                docs = res.get("documents", [[]])[0]
                return [d for d in docs if isinstance(d, str)]
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning("Chroma query failed; fallback used: %s", exc)
                self._disable_chroma_runtime = True
        return self.simple.recall(user_id, query, k)

    def get_memory_count(self, user_id: str) -> int:
        if self.chroma_client is not None and not self._disable_chroma_runtime:
            try:
                collection = self.chroma_client.get_or_create_collection(
                    name=self._collection_name(user_id)
                )
                return int(collection.count())
            except Exception:  # pragma: no cover - environment dependent
                pass
        return self.simple.get_memory_count(user_id)
