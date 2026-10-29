import secrets
import sqlite3
import string
import time

_CODE_ALPHABET = string.ascii_letters + string.digits


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                full_name  TEXT,
                last_ref   TEXT,
                joined_at  INTEGER
            );
            CREATE TABLE IF NOT EXISTS links (
                code       TEXT PRIMARY KEY,
                title      TEXT,
                clicks     INTEGER DEFAULT 0,
                deliveries INTEGER DEFAULT 0,
                created_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS link_files (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                code      TEXT REFERENCES links(code) ON DELETE CASCADE,
                file_type TEXT,
                file_id   TEXT,
                file_name TEXT
            );
            CREATE TABLE IF NOT EXISTS channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER UNIQUE,
                title       TEXT,
                invite_link TEXT,
                added_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS join_requests (
                user_id INTEGER,
                chat_id INTEGER,
                PRIMARY KEY (user_id, chat_id)
            );
            """
        )
        self.conn.commit()

    # ---------- meta ----------

    def seed_channels(self, links: list[str]) -> None:
        """Добавляет стартовые каналы только при самом первом запуске."""
        if self.conn.execute("SELECT 1 FROM meta WHERE key = 'seeded'").fetchone():
            return
        for link in links:
            self.add_channel(link)
        self.conn.execute("INSERT INTO meta (key, value) VALUES ('seeded', '1')")
        self.conn.commit()

    # ---------- users ----------

    def upsert_user(self, user_id: int, username: str | None, full_name: str) -> None:
        self.conn.execute(
            """
            INSERT INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username, full_name = excluded.full_name
            """,
            (user_id, username, full_name, int(time.time())),
        )
        self.conn.commit()

    def set_last_ref(self, user_id: int, code: str) -> None:
        self.conn.execute("UPDATE users SET last_ref = ? WHERE user_id = ?", (code, user_id))
        self.conn.commit()

    def get_last_ref(self, user_id: int) -> str | None:
        row = self.conn.execute("SELECT last_ref FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row["last_ref"] if row else None

    def count_users(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ---------- links ----------

    def create_link(self, title: str, files: list[dict]) -> str:
        while True:
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
            if not self.get_link(code):
                break
        self.conn.execute(
            "INSERT INTO links (code, title, created_at) VALUES (?, ?, ?)",
            (code, title, int(time.time())),
        )
        self.conn.executemany(
            "INSERT INTO link_files (code, file_type, file_id, file_name) VALUES (?, ?, ?, ?)",
            [(code, f["type"], f["file_id"], f["name"]) for f in files],
        )
        self.conn.commit()
        return code

    def get_link(self, code: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM links WHERE code = ?", (code,)).fetchone()

    def get_links(self, offset: int, limit: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM links ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()

    def count_links(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]

    def get_link_files(self, code: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM link_files WHERE code = ? ORDER BY id", (code,)
        ).fetchall()

    def delete_link(self, code: str) -> None:
        self.conn.execute("DELETE FROM links WHERE code = ?", (code,))
        self.conn.commit()

    def add_click(self, code: str) -> None:
        self.conn.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
        self.conn.commit()

    def add_delivery(self, code: str) -> None:
        self.conn.execute("UPDATE links SET deliveries = deliveries + 1 WHERE code = ?", (code,))
        self.conn.commit()

    def links_totals(self) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(clicks), 0), COALESCE(SUM(deliveries), 0) FROM links"
        ).fetchone()
        return row[0], row[1]

    # ---------- channels ----------

    def add_channel(self, invite_link: str, chat_id: int | None = None, title: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO channels (chat_id, title, invite_link, added_at) VALUES (?, ?, ?, ?)",
            (chat_id, title, invite_link, int(time.time())),
        )
        self.conn.commit()
        return cur.lastrowid

    def bind_channel(self, channel_id: int, chat_id: int, title: str | None) -> None:
        self.conn.execute(
            "UPDATE channels SET chat_id = ?, title = ? WHERE id = ?", (chat_id, title, channel_id)
        )
        self.conn.commit()

    def update_channel_title(self, chat_id: int, title: str | None) -> None:
        self.conn.execute("UPDATE channels SET title = ? WHERE chat_id = ?", (title, chat_id))
        self.conn.commit()

    def get_channel(self, channel_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()

    def get_channel_by_chat(self, chat_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM channels WHERE chat_id = ?", (chat_id,)).fetchone()

    def get_channels(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM channels ORDER BY id").fetchall()

    def get_bound_channels(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM channels WHERE chat_id IS NOT NULL ORDER BY id"
        ).fetchall()

    def get_unbound_channels(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM channels WHERE chat_id IS NULL ORDER BY id").fetchall()

    def delete_channel(self, channel_id: int) -> None:
        self.conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        self.conn.commit()

    # ---------- join requests ----------

    def add_join_request(self, user_id: int, chat_id: int) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO join_requests (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id)
        )
        self.conn.commit()

    def has_join_request(self, user_id: int, chat_id: int) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM join_requests WHERE user_id = ? AND chat_id = ?", (user_id, chat_id)
            ).fetchone()
            is not None
        )
