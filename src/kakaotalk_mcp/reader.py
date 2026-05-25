"""KakaoTalk local DB reader.

Reads messages from the KakaoTalk Mac app's local SQLCipher database via
the `kakaocli` CLI (https://github.com/silver-flight-group/kakaocli).

This module does **not** touch the KakaoTalk UI — it only reads the local DB,
so it never affects the user's read-position in chats.

Quick start:
    from kakaotalk_mcp import KakaoReader

    reader = KakaoReader()
    messages = reader.fetch(100000000000001, days=7)
    # → [{"dt": datetime(KST), "author": str, "text": str}, ...]

Prerequisites:
    1. `brew install silver-flight-group/tap/kakaocli`
    2. macOS Full Disk Access for your terminal app + python3 + kakaocli
       (System Settings → Privacy & Security → Full Disk Access)
    3. KakaoTalk Mac app installed + logged in
    4. k-skill auth cache at ~/.cache/k-skill/kakaotalk-mac-auth.json
       (see docs/INSTALL.md for setup)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ============================================================
# Constants & configuration
# ============================================================

KST = timezone(timedelta(hours=9))

# kakaocli CLI binary. Honor env override; otherwise discover via PATH.
KAKAOCLI_BIN = os.environ.get("KAKAOCLI_BIN") or shutil.which("kakaocli") or "/opt/homebrew/bin/kakaocli"

# k-skill auth cache (database path + decryption key)
AUTH_CACHE_PATH = Path(
    os.environ.get("KAKAOTALK_AUTH_CACHE", "")
    or (Path.home() / ".cache" / "k-skill" / "kakaotalk-mac-auth.json")
)

# Helper script for refreshing auth (downloaded on demand)
AUTH_HELPER_PATH = Path(os.environ.get("KAKAOTALK_AUTH_HELPER", "/tmp/kakaotalk_mac.py"))
AUTH_HELPER_URL = (
    "https://raw.githubusercontent.com/NomaDamas/k-skill/main/"
    "kakaotalk-mac/scripts/kakaotalk_mac.py"
)


# ============================================================
# Chat-id mapping (user-supplied, optional)
# ============================================================

def load_chat_ids() -> dict[str, int]:
    """Load user-supplied {name: chat_id} mapping.

    Resolution order:
        1. `KAKAOTALK_MCP_CHATS_JSON` env var — JSON string, parsed inline.
        2. `KAKAOTALK_MCP_CHATS_FILE` env var — path to a JSON file.
        3. `~/.config/kakaotalk-mcp/chats.json` (default location).
        4. Empty dict (callers must use numeric chat_id directly).

    Returns:
        dict mapping display names (e.g. "family") to integer chat_id.
    """
    raw = os.environ.get("KAKAOTALK_MCP_CHATS_JSON", "").strip()
    if raw:
        try:
            return {str(k): int(v) for k, v in json.loads(raw).items()}
        except (ValueError, TypeError):
            pass

    paths_to_try = []
    env_path = os.environ.get("KAKAOTALK_MCP_CHATS_FILE", "").strip()
    if env_path:
        paths_to_try.append(Path(env_path).expanduser())
    paths_to_try.append(Path.home() / ".config" / "kakaotalk-mcp" / "chats.json")

    for p in paths_to_try:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return {str(k): int(v) for k, v in data.items()}
            except (ValueError, TypeError, OSError):
                continue

    return {}


# ============================================================
# Reader
# ============================================================


class KakaoReader:
    """Read KakaoTalk messages from the local DB.

    Loads auth (database path + key) once per instance. Safe to call
    multiple read methods on a single instance.
    """

    def __init__(self) -> None:
        self._db, self._key = self._load_auth()

    # ── public ──────────────────────────────────────────────────────────────

    def fetch(
        self,
        chat_id: int | str,
        *,
        days: int = 7,
        since: Optional[date] = None,
        until: Optional[date] = None,
        text_only: bool = True,
    ) -> list[dict]:
        """Fetch messages within a date range.

        Args:
            chat_id: Numeric chat id (see `find_chat_by_name` to discover).
            days: When `since` is omitted, look back this many days from today.
            since: Inclusive start date (KST). Overrides `days` when set.
            until: Inclusive end date (KST). Defaults to today.
            text_only: When True, only text messages (type=1). When False,
                       include other types (e.g. official notification bots
                       which use type=72).

        Returns:
            List of dicts: {"dt": datetime(KST), "author": str, "text": str}.
            Sorted by sentAt ascending.
        """
        today = date.today()
        _until = until or today
        _since = since or (today - timedelta(days=days))

        since_ts = int(datetime(_since.year, _since.month, _since.day, tzinfo=KST).timestamp())
        until_ts = int(datetime(_until.year, _until.month, _until.day, 23, 59, 59, tzinfo=KST).timestamp())

        type_filter = "AND m.type = 1" if text_only else ""

        sql = f"""
            SELECT m.sentAt, COALESCE(u.displayName, '(unknown)'), m.message
            FROM NTChatMessage m
            LEFT JOIN NTUser u ON m.authorId = u.userId
            WHERE m.chatId = {int(chat_id)}
              AND m.message IS NOT NULL AND m.message != ''
              {type_filter}
              AND m.sentAt >= {since_ts}
              AND m.sentAt <= {until_ts}
            ORDER BY m.sentAt ASC
        """

        rows = self._query(sql)
        return [
            {
                "dt": datetime.fromtimestamp(row[0], tz=KST),
                "author": row[1],
                "text": row[2],
            }
            for row in rows
        ]

    def fetch_recent(self, chat_id: int | str, limit: int = 100) -> list[dict]:
        """Fetch the most recent N messages (any date)."""
        sql = f"""
            SELECT m.sentAt, COALESCE(u.displayName, '(unknown)'), m.message
            FROM NTChatMessage m
            LEFT JOIN NTUser u ON m.authorId = u.userId
            WHERE m.chatId = {int(chat_id)}
              AND m.type = 1
              AND m.message IS NOT NULL AND m.message != ''
            ORDER BY m.sentAt DESC
            LIMIT {int(limit)}
        """
        rows = self._query(sql)
        return sorted(
            [
                {
                    "dt": datetime.fromtimestamp(row[0], tz=KST),
                    "author": row[1],
                    "text": row[2],
                }
                for row in rows
            ],
            key=lambda m: m["dt"],
        )

    def search(self, keyword: str, limit: int = 20) -> list[dict]:
        """Search messages across all chats by keyword."""
        result = subprocess.run(
            [KAKAOCLI_BIN, "search", keyword,
             "--db", self._db, "--key", self._key,
             "--json", "--limit", str(limit)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"kakaocli search failed:\n{result.stderr}")

        raw = json.loads(result.stdout)
        return [
            {
                "chat_id": item["chat_id"],
                "text": item.get("text", ""),
                "dt": datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).astimezone(KST),
                "sender_id": item.get("sender_id"),
            }
            for item in raw
        ]

    def find_chat_by_name(self, name: str, limit: int = 10) -> list[dict]:
        """Find candidate chat_ids by searching messages that contain `name`.

        Returns a deduplicated list of candidates. **Do not auto-select** —
        present these to the user and let them confirm which one to use.
        """
        results = self.search(name, limit=limit)
        seen: dict[int, dict] = {}
        for r in results:
            cid = r["chat_id"]
            if cid not in seen:
                seen[cid] = {
                    "chat_id": cid,
                    "sample_text": r["text"][:80],
                    "dt": r["dt"],
                }
        return list(seen.values())

    def list_chats(self, limit: int = 50) -> list[dict]:
        """List chat rooms via `kakaocli chats`.

        Group chats and open chats may show `(unknown)` as display_name —
        in that case use `search()` or `find_chat_by_name()` instead.
        """
        result = subprocess.run(
            [KAKAOCLI_BIN, "chats",
             "--db", self._db, "--key", self._key,
             "--json", "--limit", str(limit)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"kakaocli chats failed:\n{result.stderr}")

        chats = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'\[(\d+)\]\s+(.*?)(?:\s+\((\d+)\s+unread\))?\s+\S+$', line)
            if m:
                chats.append({
                    "chat_id": int(m.group(1)),
                    "display_name": m.group(2).strip(),
                    "unread_count": int(m.group(3)) if m.group(3) else 0,
                })
        return chats

    def format_for_llm(self, messages: list[dict]) -> str:
        """Format messages as a compact LLM-friendly text block."""
        return "\n".join(
            f"[{m['dt'].strftime('%H:%M')}] {m['author']}: {m['text']}"
            for m in messages
        )

    def group_by_date(self, messages: list[dict]) -> dict[date, list[dict]]:
        """Group messages by KST date (sorted ascending)."""
        grouped: dict[date, list] = defaultdict(list)
        for msg in messages:
            grouped[msg["dt"].date()].append(msg)
        return dict(sorted(grouped.items()))

    # ── private ─────────────────────────────────────────────────────────────

    def _load_auth(self) -> tuple[str, str]:
        """Load DB path + decryption key from the k-skill auth cache."""
        if not AUTH_CACHE_PATH.exists():
            self._run_auth_helper()

        cache = json.loads(AUTH_CACHE_PATH.read_text())
        db = cache.get("database_path")
        key = cache.get("key")
        if not db or not key:
            raise RuntimeError(
                f"Auth cache is incomplete: {AUTH_CACHE_PATH}\n"
                f"Refresh it with: python3 {AUTH_HELPER_PATH} auth --refresh"
            )
        return db, key

    def _run_auth_helper(self) -> None:
        """Run the k-skill auth helper to populate the cache."""
        if not AUTH_HELPER_PATH.exists():
            raise RuntimeError(
                f"Auth helper not found at {AUTH_HELPER_PATH}.\n"
                "Download and run it:\n"
                f"  curl -s {AUTH_HELPER_URL} -o {AUTH_HELPER_PATH}\n"
                f"  python3 {AUTH_HELPER_PATH} auth --refresh"
            )
        result = subprocess.run(
            [sys.executable, str(AUTH_HELPER_PATH), "auth", "--refresh"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Auth helper failed:\n{result.stderr}")

    def _query(self, sql: str, timeout: int = 180, retries: int = 1) -> list:
        """Run a SQL query through kakaocli and return parsed JSON rows.

        Default timeout 180s — tolerates SQLCipher read locks during
        morning sync / WAL checkpoint. One automatic retry after 30s wait.
        """
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = subprocess.run(
                    [KAKAOCLI_BIN, "query", sql, "--db", self._db, "--key", self._key],
                    capture_output=True, text=True, timeout=timeout,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"kakaocli query failed:\n{result.stderr}")
                return json.loads(result.stdout)
            except subprocess.TimeoutExpired as e:
                last_err = e
                if attempt < retries:
                    time.sleep(30)
                    continue
                raise
        raise last_err  # unreachable
