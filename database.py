from __future__ import annotations

import re
import sqlite3
import uuid
import random
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "tasks.db"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Reminder:
    id: int
    user_id: str
    message: str
    remind_at: str


@dataclass
class GroceryItem:
    id: int
    user_id: str
    item_name: str
    normalized_name: str
    qty: float
    unit: str
    unit_price: float | None
    category: str
    status: str
    created_at: str
    updated_at: str


class TaskDatabase:
    """SQLite persistence with strict user scoping."""

    def __init__(self, db_path: Path | str = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_user_status
                ON tasks(user_id, status)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                ON reminders(sent, remind_at)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notes_user
                ON notes(user_id)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS grocery_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    qty REAL NOT NULL DEFAULT 1,
                    unit TEXT NOT NULL DEFAULT 'unit',
                    unit_price REAL,
                    category TEXT NOT NULL DEFAULT 'other',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_grocery_user_status
                ON grocery_items(user_id, status)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_grocery_user_name_status
                ON grocery_items(user_id, normalized_name, status)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    qty_on_hand REAL NOT NULL DEFAULT 0,
                    unit TEXT NOT NULL DEFAULT 'unit',
                    threshold_qty REAL NOT NULL DEFAULT 0,
                    auto_replenish INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_unique_item
                ON inventory_items(user_id, normalized_name, unit)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    qty_delta REAL NOT NULL,
                    note TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_inventory_events_item
                ON inventory_events(user_id, item_id, created_at)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS store_prices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    store TEXT NOT NULL,
                    unit_price REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'SGD',
                    source TEXT NOT NULL DEFAULT 'manual',
                    observed_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_store_prices_lookup
                ON store_prices(user_id, normalized_name, store, observed_at)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    default_currency TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    recurrence TEXT,
                    remind_lead_days INTEGER DEFAULT 1,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    provider_message_id TEXT
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_user_date
                ON events(user_id, event_date)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    amount_home_minor INTEGER,
                    fx_rate REAL,
                    merchant TEXT,
                    category TEXT NOT NULL,
                    method TEXT,
                    card_id INTEGER,
                    occurred_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(card_id) REFERENCES cards(id)
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_user_date
                ON expenses(user_id, occurred_at)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_user_cat
                ON expenses(user_id, category)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS budgets (
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    monthly_cap_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    PRIMARY KEY (user_id, category)
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    card_name TEXT NOT NULL,
                    last4 TEXT,
                    network TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, card_name)
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    agent TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_user_created
                ON conversations(user_id, created_at)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload_path TEXT,
                    payload_inline TEXT,
                    result TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    lease_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_pickup
                ON jobs(status, lease_until, created_at)
                """
            )
            c.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_user
                ON jobs(user_id)
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS routing_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent TEXT,
                    route_decision TEXT,
                    fallback_used INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL,
                    llm_calls INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

            self._ensure_grocery_schema(c)
            self._ensure_v2_schema(c)
            self._ensure_v3_schema(c)

    def _ensure_grocery_schema(self, conn: sqlite3.Connection) -> None:
        """Apply safe, additive migrations for grocery schema."""
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(grocery_items)").fetchall()
        }
        if "unit_price" not in cols:
            conn.execute("ALTER TABLE grocery_items ADD COLUMN unit_price REAL")

    def _ensure_v2_schema(self, conn: sqlite3.Connection) -> None:
        """Apply safe, additive migrations for V2 schema."""
        # 1. user_settings columns
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(user_settings)").fetchall()
        }
        for col in ["timezone", "locale", "quiet_hours_start", "quiet_hours_end", "onboarding_state", "family_size"]:
            if col not in cols:
                conn.execute(f"ALTER TABLE user_settings ADD COLUMN {col} TEXT")

        # 2. reminders columns
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(reminders)").fetchall()
        }
        if "provider_message_id" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN provider_message_id TEXT")

    def _ensure_v3_schema(self, conn: sqlite3.Connection) -> None:
        """Apply safe additive migrations for Households and Auth."""
        c = conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS households (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL
            )
        """)
        hh_cols = {row["name"] for row in c.execute("PRAGMA table_info(households)").fetchall()}
        if "last_digest_sent_at" not in hh_cols:
            c.execute("ALTER TABLE households ADD COLUMN last_digest_sent_at TEXT")
            
        c.execute("""
            CREATE TABLE IF NOT EXISTS household_members (
                household_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (household_id, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS invites (
                code TEXT PRIMARY KEY,
                household_id TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_by TEXT
            )
        """)
        cols = {row["name"] for row in c.execute("PRAGMA table_info(user_settings)").fetchall()}
        if "is_allowed" not in cols:
            c.execute("ALTER TABLE user_settings ADD COLUMN is_allowed INTEGER DEFAULT 0")
        if "active_household_id" not in cols:
            c.execute("ALTER TABLE user_settings ADD COLUMN active_household_id TEXT")

        cols = {row["name"] for row in c.execute("PRAGMA table_info(grocery_items)").fetchall()}
        if "household_id" not in cols:
            c.execute("ALTER TABLE grocery_items ADD COLUMN household_id TEXT")
        if "added_by_user_id" not in cols:
            c.execute("ALTER TABLE grocery_items ADD COLUMN added_by_user_id TEXT")

        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_clarifications (
                user_id TEXT PRIMARY KEY,
                original_message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        cols = {row["name"] for row in c.execute("PRAGMA table_info(inventory_items)").fetchall()}
        if "household_id" not in cols:
            c.execute("ALTER TABLE inventory_items ADD COLUMN household_id TEXT")
        
        c.execute("DROP INDEX IF EXISTS idx_inventory_unique_item")
        try:
            c.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_unique_item_v3
                ON inventory_items(IFNULL(household_id, user_id), normalized_name, unit)
                """
            )
        except Exception:
            pass

    @staticmethod
    def _normalize_grocery_name(item_name: str) -> str:
        return " ".join(item_name.strip().lower().split())

    def create_task(self, user_id: str, title: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO tasks(user_id, title, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (user_id, title.strip(), _utc_now_iso()),
            )
            return int(cur.lastrowid)

    def list_tasks(self, user_id: str, status: str | None = None) -> list[dict]:
        with self._conn() as c:
            if status:
                rows = c.execute(
                    """
                    SELECT id, title, status, created_at
                    FROM tasks
                    WHERE user_id = ? AND status = ?
                    ORDER BY id ASC
                    """,
                    (user_id, status),
                ).fetchall()
            else:
                rows = c.execute(
                    """
                    SELECT id, title, status, created_at
                    FROM tasks
                    WHERE user_id = ?
                    ORDER BY id ASC
                    """,
                    (user_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def complete_task(self, user_id: str, task_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE tasks
                SET status = 'done'
                WHERE id = ? AND user_id = ?
                """,
                (task_id, user_id),
            )
            return cur.rowcount > 0

    def delete_task(self, user_id: str, task_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?",
                (task_id, user_id),
            )
            return cur.rowcount > 0

    def get_active_household(self, user_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT active_household_id FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        return row["active_household_id"] if row and row["active_household_id"] else None

    def create_household(self, user_id: str, name: str) -> str:
        hh_id = "hh_" + uuid.uuid4().hex[:8]
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute("INSERT INTO households(id, name, created_at, created_by) VALUES (?, ?, ?, ?)", (hh_id, name, now, user_id))
            c.execute("INSERT INTO household_members(household_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)", (hh_id, user_id, now))
            c.execute(
                "INSERT INTO user_settings(user_id, created_at, updated_at, active_household_id) VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET active_household_id = ?",
                (user_id, now, now, hh_id, hh_id)
            )
        return hh_id

    def get_user_households(self, user_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT h.id, h.name, m.role FROM households h JOIN household_members m ON h.id = m.household_id WHERE m.user_id = ?", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_all_households(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM households").fetchall()
        return [dict(r) for r in rows]

    def get_household_owner(self, household_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT user_id FROM household_members WHERE household_id = ? AND role = 'owner' LIMIT 1", (household_id,)).fetchone()
        return row["user_id"] if row else None

    def get_recent_bought_events(self, household_id: str, days: int = 60) -> list[str]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn() as c:
            rows = c.execute("SELECT updated_at FROM grocery_items WHERE household_id = ? AND status = 'bought' AND updated_at >= ?", (household_id, since)).fetchall()
        return [r["updated_at"] for r in rows]

    def mark_digest_sent(self, household_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE households SET last_digest_sent_at = ? WHERE id = ?", (_utc_now_iso(), household_id))

    def leave_household(self, user_id: str, household_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM household_members WHERE household_id = ? AND user_id = ?", (household_id, user_id))
            if cur.rowcount > 0:
                row = c.execute("SELECT active_household_id FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
                if row and row["active_household_id"] == household_id:
                    c.execute("UPDATE user_settings SET active_household_id = NULL WHERE user_id = ?", (user_id,))
                return True
        return False

    def create_invite(self, household_id: str, user_id: str) -> str:
        code = str(random.randint(100000, 999999))
        now = _utc_now_iso()
        exp = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with self._conn() as c:
            c.execute("INSERT INTO invites(code, household_id, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)", (code, household_id, user_id, now, exp))
        return code

    def consume_invite(self, user_id: str, code: str) -> dict | None:
        now = _utc_now_iso()
        with self._conn() as c:
            inv = c.execute("SELECT household_id, expires_at, used_by FROM invites WHERE code = ?", (code,)).fetchone()
            if not inv or inv["used_by"] or inv["expires_at"] < now:
                return None
            hh_id = inv["household_id"]
            
            existing = c.execute("SELECT 1 FROM household_members WHERE household_id = ? AND user_id = ?", (hh_id, user_id)).fetchone()
            if not existing:
                c.execute("INSERT INTO household_members(household_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)", (hh_id, user_id, now))
            
            c.execute("UPDATE invites SET used_by = ? WHERE code = ?", (user_id, code))
            c.execute(
                "INSERT INTO user_settings(user_id, created_at, updated_at, is_allowed, active_household_id) VALUES (?, ?, ?, 1, ?) ON CONFLICT(user_id) DO UPDATE SET is_allowed = 1, active_household_id = ?",
                (user_id, now, now, hh_id, hh_id)
            )
            hh = c.execute("SELECT name FROM households WHERE id = ?", (hh_id,)).fetchone()
            return {"household_id": hh_id, "name": hh["name"]}

    def set_pending_clarification(self, user_id: str, original_message: str) -> None:
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO pending_clarifications(user_id, original_message, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET original_message = excluded.original_message, created_at = excluded.created_at
                """,
                (user_id, original_message, now)
            )

    def get_and_clear_pending_clarification(self, user_id: str, max_age_minutes: int = 5) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT original_message, created_at FROM pending_clarifications WHERE user_id = ?", (user_id,)).fetchone()
            if row:
                c.execute("DELETE FROM pending_clarifications WHERE user_id = ?", (user_id,))
                created_at = datetime.fromisoformat(row["created_at"])
                if (datetime.now(timezone.utc) - created_at).total_seconds() < max_age_minutes * 60:
                    return str(row["original_message"])
        return None

    def _household_scope(self, user_id: str, override_hh_id: str | None = None) -> tuple[str, tuple, str | None]:
        if override_hh_id:
            return "household_id = ?", (override_hh_id,), override_hh_id
        hh = self.get_active_household(user_id)
        if hh:
            return "household_id = ?", (hh,), hh
        return "user_id = ? AND household_id IS NULL", (user_id,), None

    def _grocery_scope(self, user_id: str, override_hh_id: str | None = None) -> tuple[str, tuple, str | None]:
        return self._household_scope(user_id, override_hh_id)

    def add_grocery_item(
        self,
        user_id: str,
        item_name: str,
        qty: float = 1.0,
        unit: str = "unit",
        unit_price: float | None = None,
        category: str = "other",
    ) -> int:
        clean_name = item_name.strip()
        if not clean_name:
            raise ValueError("Grocery item name cannot be empty.")
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if unit_price is not None and unit_price <= 0:
            raise ValueError("Unit price must be greater than zero.")
        normalized_name = self._normalize_grocery_name(clean_name)
        now = _utc_now_iso()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO grocery_items(
                    user_id, item_name, normalized_name, qty, unit, unit_price, category, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    user_id,
                    clean_name,
                    normalized_name,
                    float(qty),
                    unit.strip() or "unit",
                    float(unit_price) if unit_price is not None else None,
                    category.strip() or "other",
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def upsert_pending_grocery_item(
        self,
        user_id: str,
        item_name: str,
        qty: float = 1.0,
        unit: str = "unit",
        unit_price: float | None = None,
        category: str = "other",
    ) -> dict:
        clean_name = item_name.strip()
        if not clean_name:
            raise ValueError("Grocery item name cannot be empty.")
        if qty <= 0:
            raise ValueError("Quantity must be greater than zero.")
        if unit_price is not None and unit_price <= 0:
            raise ValueError("Unit price must be greater than zero.")

        normalized_name = self._normalize_grocery_name(clean_name)
        clean_unit = unit.strip() or "unit"
        clean_category = category.strip() or "other"
        now = _utc_now_iso()
        cond, p, hh_id = self._grocery_scope(user_id)

        with self._conn() as c:
            row = c.execute(
                f"""
                SELECT id, qty, category, unit_price
                FROM grocery_items
                WHERE {cond} AND normalized_name = ? AND status = 'pending' AND unit = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (*p, normalized_name, clean_unit),
            ).fetchone()

            if row:
                new_qty = float(row["qty"]) + float(qty)
                category_to_set = (
                    clean_category if row["category"] == "other" and clean_category != "other" else row["category"]
                )
                if unit_price is not None:
                    price_to_set = float(unit_price)
                elif row["unit_price"] is not None:
                    price_to_set = float(row["unit_price"])
                else:
                    price_to_set = None
                c.execute(
                    f"""
                    UPDATE grocery_items
                    SET qty = ?, category = ?, unit_price = ?, updated_at = ?
                    WHERE id = ? AND {cond}
                    """,
                    (new_qty, category_to_set, price_to_set, now, int(row["id"]), *p),
                )
                return {
                    "id": int(row["id"]),
                    "qty": new_qty,
                    "unit_price": price_to_set,
                    "merged": True,
                    "status": "pending",
                }

            cur = c.execute(
                """
                INSERT INTO grocery_items(
                    user_id, household_id, added_by_user_id, item_name, normalized_name, qty, unit, unit_price, category, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    user_id,
                    hh_id,
                    user_id,
                    clean_name,
                    normalized_name,
                    float(qty),
                    clean_unit,
                    float(unit_price) if unit_price is not None else None,
                    clean_category,
                    now,
                    now,
                ),
            )
            return {
                "id": int(cur.lastrowid),
                "qty": float(qty),
                "unit_price": float(unit_price) if unit_price is not None else None,
                "merged": False,
                "status": "pending",
            }

    def list_grocery_items(self, user_id: str, status: str | None = None, override_hh_id: str | None = None) -> list[dict]:
        status_filter = (status or "").strip().lower() or None
        cond, p, _ = self._household_scope(user_id, override_hh_id)
        with self._conn() as c:
            if status_filter and status_filter != "all":
                rows = c.execute(
                    f"""
                    SELECT id, item_name, qty, unit, unit_price, category, status, created_at, updated_at
                    FROM grocery_items
                    WHERE {cond} AND status = ?
                    ORDER BY id ASC
                    """,
                    (*p, status_filter),
                ).fetchall()
            else:
                rows = c.execute(
                    f"""
                    SELECT id, item_name, qty, unit, unit_price, category, status, created_at, updated_at
                    FROM grocery_items
                    WHERE {cond}
                    ORDER BY id ASC
                    """,
                    (*p,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_grocery_item(self, user_id: str, item_id: int) -> dict | None:
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            row = c.execute(
                f"""
                SELECT id, item_name, normalized_name, qty, unit, unit_price, category, status, created_at, updated_at
                FROM grocery_items
                WHERE {cond} AND id = ?
                LIMIT 1
                """,
                (*p, item_id),
            ).fetchone()
        return dict(row) if row else None

    def set_grocery_category(self, user_id: str, item_id: int, category: str) -> bool:
        clean_category = category.strip().lower()
        if not clean_category:
            raise ValueError("Category cannot be empty.")
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            cur = c.execute(
                f"""
                UPDATE grocery_items
                SET category = ?, updated_at = ?
                WHERE id = ? AND {cond}
                """,
                (clean_category, _utc_now_iso(), item_id, *p),
            )
            return cur.rowcount > 0

    def set_grocery_unit_price(self, user_id: str, item_id: int, unit_price: float) -> bool:
        if unit_price <= 0:
            raise ValueError("Unit price must be greater than zero.")
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            cur = c.execute(
                f"""
                UPDATE grocery_items
                SET unit_price = ?, updated_at = ?
                WHERE id = ? AND {cond}
                """,
                (float(unit_price), _utc_now_iso(), item_id, *p),
            )
            return cur.rowcount > 0

    def grocery_budget_summary(self, user_id: str, status: str = "pending") -> dict:
        rows = self.list_grocery_items(user_id, status=status)
        total_estimate = 0.0
        priced_count = 0
        missing_price_count = 0
        per_category: dict[str, dict[str, float | int | str]] = {}

        for row in rows:
            category = (row.get("category") or "other").strip() or "other"
            bucket = per_category.setdefault(
                category,
                {"category": category, "item_count": 0, "priced_count": 0, "missing_price_count": 0, "estimated_total": 0.0},
            )
            bucket["item_count"] = int(bucket["item_count"]) + 1

            unit_price = row.get("unit_price")
            qty = float(row.get("qty") or 0)
            if unit_price is None:
                missing_price_count += 1
                bucket["missing_price_count"] = int(bucket["missing_price_count"]) + 1
                continue

            estimate = qty * float(unit_price)
            total_estimate += estimate
            priced_count += 1
            bucket["priced_count"] = int(bucket["priced_count"]) + 1
            bucket["estimated_total"] = float(bucket["estimated_total"]) + estimate

        categories = sorted(
            per_category.values(),
            key=lambda x: str(x["category"]),
        )
        for cat in categories:
            cat["estimated_total"] = round(float(cat["estimated_total"]), 2)

        return {
            "item_count": len(rows),
            "priced_count": priced_count,
            "missing_price_count": missing_price_count,
            "estimated_total": round(total_estimate, 2),
            "categories": categories,
        }

    def mark_grocery_bought(self, user_id: str, item_id: int) -> bool:
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            cur = c.execute(
                f"""
                UPDATE grocery_items
                SET status = 'bought', updated_at = ?
                WHERE id = ? AND {cond}
                """,
                (_utc_now_iso(), item_id, *p),
            )
            return cur.rowcount > 0

    def remove_grocery_item(self, user_id: str, item_id: int) -> bool:
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            cur = c.execute(
                f"DELETE FROM grocery_items WHERE id = ? AND {cond}",
                (item_id, *p),
            )
            return cur.rowcount > 0

    def clear_grocery_items(self, user_id: str, status: str | None = None) -> int:
        status_filter = (status or "").strip().lower() or None
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            if status_filter and status_filter != "all":
                cur = c.execute(
                    f"DELETE FROM grocery_items WHERE {cond} AND status = ?",
                    (*p, status_filter),
                )
            else:
                cur = c.execute(
                    f"DELETE FROM grocery_items WHERE {cond}",
                    (*p,),
                )
            return int(cur.rowcount)

    def repeat_recent_bought_to_pending(
        self,
        user_id: str,
        days: int = 7,
        limit: int = 30,
    ) -> dict:
        if days <= 0:
            raise ValueError("Days must be > 0.")
        since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cond, p, _ = self._grocery_scope(user_id)
        with self._conn() as c:
            rows = c.execute(
                f"""
                SELECT
                    MIN(item_name) AS item_name,
                    normalized_name,
                    unit,
                    category,
                    AVG(qty) AS avg_qty,
                    COUNT(*) AS times_bought,
                    MAX(updated_at) AS last_bought_at,
                    MIN(updated_at) AS first_bought_at
                FROM grocery_items
                WHERE {cond} AND status = 'bought' AND updated_at >= ?
                GROUP BY normalized_name, unit, category
                ORDER BY times_bought DESC, last_bought_at DESC
                LIMIT ?
                """,
                (*p, since_iso, limit),
            ).fetchall()

        created = 0
        merged = 0
        repeated_items: list[str] = []
        for row in rows:
            qty = float(row["avg_qty"] or 1.0)
            result = self.upsert_pending_grocery_item(
                user_id=user_id,
                item_name=str(row["item_name"]),
                qty=max(qty, 1.0),
                unit=str(row["unit"] or "unit"),
                category=str(row["category"] or "other"),
            )
            repeated_items.append(str(row["item_name"]))
            if result["merged"]:
                merged += 1
            else:
                created += 1
        return {
            "total_candidates": len(rows),
            "created": created,
            "merged": merged,
            "items": repeated_items,
            "days": days,
        }

    def suggest_rebuy_candidates(self, user_id: str, limit: int = 8, override_hh_id: str | None = None) -> list[dict]:
        cond, p, _ = self._household_scope(user_id, override_hh_id)
        with self._conn() as c:
            pending_rows = c.execute(
                f"""
                SELECT normalized_name, unit
                FROM grocery_items
                WHERE {cond} AND status = 'pending'
                """,
                (*p,),
            ).fetchall()
            pending_pairs = {
                (str(r["normalized_name"]), str(r["unit"]))
                for r in pending_rows
            }

            bought_rows = c.execute(
                f"""
                SELECT
                    MIN(item_name) AS item_name,
                    normalized_name,
                    unit,
                    category,
                    AVG(qty) AS avg_qty,
                    COUNT(*) AS times_bought,
                    MAX(updated_at) AS last_bought_at,
                    MIN(updated_at) AS first_bought_at
                FROM grocery_items
                WHERE {cond} AND status = 'bought'
                GROUP BY normalized_name, unit, category
                HAVING COUNT(*) >= 2
                ORDER BY times_bought DESC, last_bought_at DESC
                LIMIT 100
                """,
                (*p,),
            ).fetchall()

        suggestions: list[dict] = []
        now = datetime.now(timezone.utc)
        
        for row in bought_rows:
            key = (str(row["normalized_name"]), str(row["unit"]))
            if key in pending_pairs:
                continue
                
            first_bought = datetime.fromisoformat(str(row["first_bought_at"]))
            last_bought = datetime.fromisoformat(str(row["last_bought_at"]))
            times_bought = int(row["times_bought"])
            
            # Calculate consumption velocity (average days between purchases)
            total_days = (last_bought - first_bought).total_seconds() / 86400.0
            velocity_days = total_days / (times_bought - 1) if times_bought > 1 else 0.0
            days_since_last = (now - last_bought).total_seconds() / 86400.0
            
            # Flag as due if we are at >= 80% of the normal consumption cycle
            is_due = bool(velocity_days > 0 and days_since_last >= (velocity_days * 0.8))
            
            suggestions.append(
                {
                    "item_name": str(row["item_name"]),
                    "unit": str(row["unit"]),
                    "category": str(row["category"]),
                    "avg_qty": round(float(row["avg_qty"] or 1.0), 2),
                    "times_bought": times_bought,
                    "last_bought_at": str(row["last_bought_at"]),
                    "velocity_days": round(velocity_days, 1),
                    "days_since_last": round(days_since_last, 1),
                    "is_due": is_due
                }
            )
            
        # Sort suggestions: Due items first, then by most frequently bought
        suggestions.sort(key=lambda x: (not x["is_due"], -x["times_bought"]))
        return suggestions[:limit]

    def record_store_price(
        self,
        user_id: str,
        item_name: str,
        store: str,
        unit_price: float,
        currency: str = "SGD",
        source: str = "manual",
    ) -> int:
        if unit_price <= 0:
            raise ValueError("Unit price must be greater than zero.")
        clean_item = item_name.strip()
        clean_store = store.strip()
        if not clean_item or not clean_store:
            raise ValueError("Item and store are required.")
        normalized_name = self._normalize_grocery_name(clean_item)
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO store_prices(
                    user_id, item_name, normalized_name, store, unit_price, currency, source, observed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    clean_item,
                    normalized_name,
                    clean_store,
                    float(unit_price),
                    (currency.strip().upper() or "SGD"),
                    source.strip() or "manual",
                    _utc_now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def compare_store_prices(self, user_id: str, item_name: str) -> list[dict]:
        normalized_name = self._normalize_grocery_name(item_name)
        sixty_days_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT store, unit_price, currency, source, observed_at
                FROM store_prices
                WHERE user_id = ? AND normalized_name = ? AND observed_at >= ?
                ORDER BY observed_at DESC
                """,
                (user_id, normalized_name, sixty_days_ago),
            ).fetchall()

        latest_by_store: dict[str, dict] = {}
        for row in rows:
            store = str(row["store"]).strip().lower()
            if store in latest_by_store:
                continue
            latest_by_store[store] = {
                "store": str(row["store"]),
                "unit_price": float(row["unit_price"]),
                "currency": str(row["currency"]),
                "source": str(row["source"]),
                "observed_at": str(row["observed_at"]),
            }
        return sorted(latest_by_store.values(), key=lambda x: x["unit_price"])

    def _get_inventory_item(
        self,
        user_id: str,
        normalized_name: str,
        unit: str,
    ) -> dict | None:
        clean_unit = unit.strip() or "unit"
        with self._conn() as c:
            row = c.execute(
                """
                SELECT id, item_name, normalized_name, qty_on_hand, unit, threshold_qty, auto_replenish, created_at, updated_at
                FROM inventory_items
                WHERE user_id = ? AND normalized_name = ? AND unit = ?
                LIMIT 1
                """,
                (user_id, normalized_name, clean_unit),
            ).fetchone()
        return dict(row) if row else None

    def ensure_inventory_item(
        self,
        user_id: str,
        item_name: str,
        unit: str = "unit",
    ) -> dict:
        clean_name = item_name.strip()
        clean_unit = unit.strip() or "unit"
        if not clean_name:
            raise ValueError("Inventory item name cannot be empty.")
        normalized_name = self._normalize_grocery_name(clean_name)
        existing = self._get_inventory_item(user_id, normalized_name, clean_unit)
        if existing:
            return existing
        now = _utc_now_iso()
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO inventory_items(
                    user_id, item_name, normalized_name, qty_on_hand, unit, threshold_qty, auto_replenish, created_at, updated_at
                )
                VALUES (?, ?, ?, 0, ?, 0, 1, ?, ?)
                """,
                (user_id, clean_name, normalized_name, clean_unit, now, now),
            )
            item_id = int(cur.lastrowid)
        return {
            "id": item_id,
            "item_name": clean_name,
            "normalized_name": normalized_name,
            "qty_on_hand": 0.0,
            "unit": clean_unit,
            "threshold_qty": 0.0,
            "auto_replenish": 1,
        }

    def _record_inventory_event(
        self,
        user_id: str,
        item_id: int,
        event_type: str,
        qty_delta: float,
        note: str = "",
    ) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO inventory_events(user_id, item_id, event_type, qty_delta, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, item_id, event_type, float(qty_delta), note.strip() or None, _utc_now_iso()),
            )

    def set_inventory_stock(
        self,
        user_id: str,
        item_name: str,
        qty_on_hand: float,
        unit: str = "unit",
    ) -> dict:
        if qty_on_hand < 0:
            raise ValueError("Stock cannot be negative.")
        item = self.ensure_inventory_item(user_id, item_name, unit)
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """
                UPDATE inventory_items
                SET item_name = ?, qty_on_hand = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (item_name.strip(), float(qty_on_hand), now, int(item["id"]), user_id),
            )
        self._record_inventory_event(user_id, int(item["id"]), "set", float(qty_on_hand), "set stock")
        threshold = float(item.get("threshold_qty") or 0)
        return {
            "id": int(item["id"]),
            "item_name": item_name.strip(),
            "normalized_name": item["normalized_name"],
            "qty_on_hand": float(qty_on_hand),
            "unit": unit.strip() or "unit",
            "threshold_qty": threshold,
            "is_low": threshold > 0 and float(qty_on_hand) < threshold,
            "needed_qty": max(threshold - float(qty_on_hand), 0.0),
        }

    def adjust_inventory_stock(
        self,
        user_id: str,
        item_name: str,
        qty_delta: float,
        unit: str = "unit",
        event_type: str = "adjust",
        note: str = "",
    ) -> dict:
        if qty_delta == 0:
            raise ValueError("Quantity delta cannot be zero.")
        item = self.ensure_inventory_item(user_id, item_name, unit)
        current_qty = float(item.get("qty_on_hand") or 0.0)
        new_qty = max(current_qty + float(qty_delta), 0.0)
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """
                UPDATE inventory_items
                SET item_name = ?, qty_on_hand = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (item_name.strip(), new_qty, now, int(item["id"]), user_id),
            )
        self._record_inventory_event(user_id, int(item["id"]), event_type, float(qty_delta), note)
        threshold = float(item.get("threshold_qty") or 0.0)
        needed_qty = max(threshold - new_qty, 0.0) if threshold > 0 else 0.0
        return {
            "id": int(item["id"]),
            "item_name": item_name.strip(),
            "normalized_name": item["normalized_name"],
            "qty_on_hand": new_qty,
            "unit": unit.strip() or "unit",
            "threshold_qty": threshold,
            "auto_replenish": int(item.get("auto_replenish") or 0),
            "is_low": threshold > 0 and new_qty < threshold,
            "needed_qty": needed_qty,
        }

    def set_inventory_threshold(
        self,
        user_id: str,
        item_name: str,
        threshold_qty: float,
        unit: str = "unit",
    ) -> dict:
        if threshold_qty < 0:
            raise ValueError("Threshold cannot be negative.")
        item = self.ensure_inventory_item(user_id, item_name, unit)
        now = _utc_now_iso()
        with self._conn() as c:
            c.execute(
                """
                UPDATE inventory_items
                SET threshold_qty = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (float(threshold_qty), now, int(item["id"]), user_id),
            )
        qty_on_hand = float(item.get("qty_on_hand") or 0.0)
        return {
            "id": int(item["id"]),
            "item_name": item_name.strip(),
            "normalized_name": item["normalized_name"],
            "qty_on_hand": qty_on_hand,
            "unit": unit.strip() or "unit",
            "threshold_qty": float(threshold_qty),
            "is_low": float(threshold_qty) > 0 and qty_on_hand < float(threshold_qty),
            "needed_qty": max(float(threshold_qty) - qty_on_hand, 0.0),
        }

    def list_inventory_items(self, user_id: str, low_only: bool = False) -> list[dict]:
        with self._conn() as c:
            if low_only:
                rows = c.execute(
                    """
                    SELECT id, item_name, normalized_name, qty_on_hand, unit, threshold_qty, auto_replenish, created_at, updated_at
                    FROM inventory_items
                    WHERE user_id = ? AND threshold_qty > 0 AND qty_on_hand < threshold_qty
                    ORDER BY normalized_name ASC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = c.execute(
                    """
                    SELECT id, item_name, normalized_name, qty_on_hand, unit, threshold_qty, auto_replenish, created_at, updated_at
                    FROM inventory_items
                    WHERE user_id = ?
                    ORDER BY normalized_name ASC
                    """,
                    (user_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def add_reminder(self, user_id: str, message: str, remind_at_iso: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO reminders(user_id, message, remind_at, sent, created_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (user_id, message.strip(), remind_at_iso, _utc_now_iso()),
            )
            return int(cur.lastrowid)

    def get_due_reminders(self, now_iso: str | None = None) -> list[Reminder]:
        now_iso = now_iso or _utc_now_iso()
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT id, user_id, message, remind_at
                FROM reminders
                WHERE sent = 0 AND remind_at <= ?
                ORDER BY remind_at ASC
                """,
                (now_iso,),
            ).fetchall()
        return [Reminder(**dict(r)) for r in rows]

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE reminders SET sent = 1 WHERE id = ?",
                (reminder_id,),
            )

    def save_note(self, user_id: str, content: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO notes(user_id, content, created_at)
                VALUES (?, ?, ?)
                """,
                (user_id, content.strip(), _utc_now_iso()),
            )
            return int(cur.lastrowid)

    def list_notes(self, user_id: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT id, content, created_at
                FROM notes
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_notes(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """Naive substring/keyword search over the user's private notes."""
        q = (query or "").strip()
        if not q:
            return []
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT id, content, created_at
                FROM notes
                WHERE user_id = ? AND LOWER(content) LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, f"%{q.lower()}%", limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            tokens = [t for t in re.split(r"\W+", q.lower()) if len(t) >= 3]
            if not tokens:
                return []
            placeholders = " OR ".join(["LOWER(content) LIKE ?"] * len(tokens))
            params = (user_id, *[f"%{t}%" for t in tokens], limit)
            rows = c.execute(
                f"""
                SELECT id, content, created_at
                FROM notes
                WHERE user_id = ? AND ({placeholders})
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def daily_summary(self, user_id: str) -> dict:
        tasks = self.list_tasks(user_id)
        notes = self.list_notes(user_id, limit=5)
        pending = sum(1 for t in tasks if t["status"] == "pending")
        done = sum(1 for t in tasks if t["status"] == "done")
        return {
            "task_total": len(tasks),
            "task_pending": pending,
            "task_done": done,
            "recent_notes": notes,
        }

    def get_user_settings(self, user_id: str) -> dict | None:
        """Fetches settings for a given user."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_user_setting(self, user_id: str, key: str, value: str) -> None:
        """Creates or updates a specific setting for a user."""
        now = _utc_now_iso()
        with self._conn() as c:
            # Ensure user row exists before trying to update.
            c.execute(
                """
                INSERT INTO user_settings(user_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO NOTHING
                """,
                (user_id, now, now),
            )
            
            # Update the specific setting.
            allowed_keys = {
                "default_currency",
                "timezone",
                "locale",
                "quiet_hours_start",
                "quiet_hours_end",
                "onboarding_state",
                "family_size",
                "is_allowed",
                "active_household_id"
            }
            
            if key in allowed_keys:
                c.execute(
                    f"UPDATE user_settings SET {key} = ?, updated_at = ? WHERE user_id = ?",
                    (value, now, user_id),
                )
            else:
                raise ValueError(f"Unknown user setting: {key}")

    def delete_user(self, user_id: str) -> None:
        """Completely removes all data associated with a user."""
        with self._conn() as c:
            tables_with_user_id = [
                "tasks", "reminders", "notes", "grocery_items",
                "inventory_items", "inventory_events", "store_prices",
                "user_settings", "events", "expenses", "budgets",
                "cards", "conversations", "jobs", "routing_log",
                "pending_clarifications", "household_members"
            ]
            for table in tables_with_user_id:
                c.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            
            c.execute("DELETE FROM invites WHERE created_by = ? OR used_by = ?", (user_id, user_id))
            c.execute("DELETE FROM households WHERE created_by = ?", (user_id,))

    def append_conversation(self, user_id: str, role: str, content: str, agent_name: str | None = None) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO conversations(user_id, role, content, agent, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, role, content, agent_name, _utc_now_iso()),
            )

    def get_monthly_message_count(self, user_id: str) -> int:
        now = datetime.now(timezone.utc)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._conn() as c:
            row = c.execute(
                """
                SELECT COUNT(*) as count FROM conversations 
                WHERE user_id = ? AND role = 'user' AND created_at >= ?
                """,
                (user_id, start_of_month)
            ).fetchone()
            return int(row["count"]) if row else 0

    def get_last_inbound_time(self, user_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT created_at FROM conversations
                WHERE user_id = ? AND role = 'user'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,)
            ).fetchone()
        return str(row["created_at"]) if row else None

    def get_recent_conversations(self, user_id: str, limit: int = 5) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT role, content
                FROM conversations
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def insert_routing_log(
        self,
        user_id: str,
        agent: str | None,
        route_decision: str,
        fallback_used: int,
        latency_ms: int,
        llm_calls: int,
        error: str | None = None,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO routing_log(
                    user_id, agent, route_decision, fallback_used, latency_ms, llm_calls, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, agent, route_decision, fallback_used, latency_ms, llm_calls, error, _utc_now_iso()),
            )

    def add_event(
        self,
        user_id: str,
        title: str,
        event_date: str,
        recurrence: str | None = None,
        remind_lead_days: int = 1,
        notes: str | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO events(user_id, title, event_date, recurrence, remind_lead_days, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, title.strip(), event_date.strip(), recurrence, remind_lead_days, notes, _utc_now_iso()),
            )
            return int(cur.lastrowid)

    def list_events(self, user_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE user_id = ? ORDER BY event_date ASC",
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_event(self, user_id: str, event_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
            return cur.rowcount > 0

    def expand_events_to_reminders(self) -> None:
        now = datetime.now(timezone.utc)
        with self._conn() as c:
            events = c.execute("SELECT * FROM events").fetchall()

            for evt in events:
                try:
                    dt = datetime.strptime(evt["event_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                lead_days = int(evt["remind_lead_days"] or 1)
                recurrence = evt["recurrence"]
                
                if recurrence == "yearly":
                    target_year = now.year
                    try:
                        next_dt = dt.replace(year=target_year)
                    except ValueError:
                        next_dt = dt.replace(year=target_year, month=3, day=1)
                    if next_dt < now - timedelta(days=1):
                        try:
                            next_dt = dt.replace(year=target_year + 1)
                        except ValueError:
                            next_dt = dt.replace(year=target_year + 1, month=3, day=1)
                elif recurrence == "monthly":
                    target_month = now.month
                    target_year = now.year
                    try:
                        next_dt = dt.replace(year=target_year, month=target_month)
                    except ValueError:
                        next_dt = dt.replace(year=target_year, month=target_month+1, day=1) if target_month < 12 else dt.replace(year=target_year+1, month=1, day=1)
                    
                    if next_dt < now - timedelta(days=1):
                        target_month = 1 if target_month == 12 else target_month + 1
                        target_year = target_year + 1 if target_month == 1 else target_year
                        try:
                            next_dt = dt.replace(year=target_year, month=target_month)
                        except ValueError:
                            next_dt = dt.replace(year=target_year, month=target_month+1, day=1) if target_month < 12 else dt.replace(year=target_year+1, month=1, day=1)
                else:
                    next_dt = dt

                reminder_dt = next_dt - timedelta(days=lead_days)
                
                # Check if reminder is due to be created in the next 24h
                if now <= reminder_dt <= now + timedelta(hours=24):
                    msg_prefix = f"Event: {evt['title']}"
                    existing = c.execute(
                        "SELECT id FROM reminders WHERE user_id = ? AND message LIKE ? AND remind_at >= ?",
                        (evt["user_id"], f"%{msg_prefix}%", (now - timedelta(days=1)).isoformat())
                    ).fetchone()
                    
                    if not existing:
                        c.execute(
                            "INSERT INTO reminders(user_id, message, remind_at, sent, created_at) VALUES (?, ?, ?, 0, ?)",
                            (evt["user_id"], f"{msg_prefix} is on {next_dt.strftime('%Y-%m-%d')}!", reminder_dt.isoformat(), _utc_now_iso())
                        )
                        c.connection.commit()

    def log_expense(
        self,
        user_id: str,
        amount_minor: int,
        currency: str,
        category: str,
        merchant: str | None = None,
        method: str | None = None,
        occurred_at: str | None = None,
    ) -> int:
        now = _utc_now_iso()
        when = occurred_at or now
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO expenses(
                    user_id, amount_minor, currency, merchant, category, method,
                    occurred_at, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, int(amount_minor), currency, merchant, category, method,
                 when, method or "manual", now),
            )
            return int(cur.lastrowid)

    def get_expenses_for_period(self, user_id: str, start_iso: str, end_iso: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT id, amount_minor, currency, merchant, category, method, occurred_at
                FROM expenses
                WHERE user_id = ? AND occurred_at >= ? AND occurred_at < ?
                ORDER BY occurred_at ASC
                """,
                (user_id, start_iso, end_iso),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_last_expense(self, user_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT id, amount_minor, currency, merchant, category, method, occurred_at
                FROM expenses
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_expense(self, user_id: str, expense_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM expenses WHERE id = ? AND user_id = ?",
                (int(expense_id), user_id),
            )
            return cur.rowcount > 0

    def get_expense_category_stats(self, user_id: str, category: str, currency: str) -> dict | None:
        """Returns the average amount and total count for a specific expense category."""
        with self._conn() as c:
            row = c.execute(
                """
                SELECT AVG(amount_minor) as avg_amount, COUNT(*) as count 
                FROM expenses 
                WHERE user_id = ? AND category = ? AND currency = ?
                """,
                (user_id, category, currency)
            ).fetchone()
            if row and row["count"] > 0:
                return {"avg_amount_minor": float(row["avg_amount"] or 0), "count": int(row["count"])}
            return None

    def set_budget(self, user_id: str, category: str, monthly_cap_minor: int, currency: str) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO budgets(user_id, category, monthly_cap_minor, currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, category) DO UPDATE SET 
                    monthly_cap_minor = excluded.monthly_cap_minor, 
                    currency = excluded.currency
                """,
                (user_id, category, monthly_cap_minor, currency)
            )

    def get_budget(self, user_id: str, category: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT monthly_cap_minor, currency FROM budgets WHERE user_id = ? AND category = ?",
                (user_id, category)
            ).fetchone()
            return dict(row) if row else None

    def get_current_month_category_total(self, user_id: str, category: str, currency: str) -> int:
        now = datetime.now(timezone.utc)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._conn() as c:
            row = c.execute(
                "SELECT SUM(amount_minor) as total FROM expenses WHERE user_id = ? AND category = ? AND currency = ? AND occurred_at >= ?",
                (user_id, category, currency, start_of_month)
            ).fetchone()
            return int(row["total"] or 0) if row and row["total"] is not None else 0
