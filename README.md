# kakaotalk-mcp

> Read & send KakaoTalk messages from Claude Code (or any MCP-compatible client) on macOS.

A Model Context Protocol (MCP) server that lets Claude read your KakaoTalk chats and send messages — without scraping the UI for reads, so it never affects your unread counters.

## What it does

| Tool | What it does | How |
|---|---|---|
| `kakao_read` | Read messages from a chat by date range | Direct DB read (no UI) |
| `kakao_read_recent` | Read the most recent N messages | Direct DB read (no UI) |
| `kakao_search` | Search messages by keyword across all chats | Direct DB read (no UI) |
| `kakao_list_chats` | List your configured chat aliases | Local config |
| `kakao_send` | Send a message to a chat | UI automation (opens KakaoTalk) |

**Read operations are safe.** They query the local SQLCipher database directly via [`kakaocli`](https://github.com/silver-flight-group/kakaocli), so they:
- never open the chat in the UI
- never reset your "unread from here" position
- don't mark messages as read

**Send operations use UI automation** via `kmsg`, so KakaoTalk must be running.

## Use cases

- Daily AI/news curation from group chats (e.g. summarize → push to Notion/Slack)
- Personal finance tracking from securities-firm notification chats (e.g. parse dividend alerts → log to Google Sheets)
- Custom workflows that need to read or respond to KakaoTalk messages programmatically

## Requirements

- **macOS** (KakaoTalk Mac app required)
- **Python 3.10+**
- **`kakaocli`** installed: `brew install silver-flight-group/tap/kakaocli`
- **Full Disk Access** granted to your terminal app + `python3` + `kakaocli` (System Settings → Privacy & Security → Full Disk Access)
- **`k-skill` auth cache** at `~/.cache/k-skill/kakaotalk-mac-auth.json` — see [docs/INSTALL.md](docs/INSTALL.md)
- **KakaoTalk Mac app** installed and logged in

## Quick start

### 1. Install

```bash
# Option A: install from this repo
git clone https://github.com/kemy-ai/kakaotalk-mcp.git
cd kakaotalk-mcp
uv pip install -e .

# Option B: install in an isolated venv with uv
uv venv && uv pip install -e .
```

### 2. Bootstrap auth

Follow [docs/INSTALL.md](docs/INSTALL.md) to:
1. Install `kakaocli` and grant Full Disk Access
2. Download and run the k-skill auth helper to create the auth cache

### 3. (Optional) Configure chat aliases

Numeric chat IDs are hard to remember. Set up aliases:

```bash
mkdir -p ~/.config/kakaotalk-mcp
cp examples/chats.example.json ~/.config/kakaotalk-mcp/chats.json
# edit the file: replace numeric ids with your real ones
```

Discover your chat_ids first with `kakao_search "<keyword from the chat>"`.

### 4. Register with Claude Code

Add to `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "kakaotalk": {
      "command": "uv",
      "args": ["run", "kakaotalk-mcp"],
      "cwd": "/absolute/path/to/kakaotalk-mcp"
    }
  }
}
```

Or, if installed in your global Python:

```json
{
  "mcpServers": {
    "kakaotalk": {
      "command": "kakaotalk-mcp"
    }
  }
}
```

Restart Claude Code. You should see 5 new tools: `kakao_read`, `kakao_read_recent`, `kakao_search`, `kakao_list_chats`, `kakao_send`.

## Configuration

All configuration is via environment variables — no code changes needed.

| Variable | Default | Description |
|---|---|---|
| `KAKAOTALK_MCP_CHATS_FILE` | `~/.config/kakaotalk-mcp/chats.json` | Path to chat-id alias JSON file |
| `KAKAOTALK_MCP_CHATS_JSON` | (unset) | Inline JSON string for aliases (overrides the file) |
| `KAKAOCLI_BIN` | auto-discover via `PATH` | Path to `kakaocli` binary |
| `KMSG_BIN` | auto-discover via `PATH` | Path to `kmsg` binary (for `kakao_send`) |
| `KAKAOTALK_AUTH_CACHE` | `~/.cache/k-skill/kakaotalk-mac-auth.json` | Path to auth cache JSON |
| `KAKAOTALK_AUTH_HELPER` | `/tmp/kakaotalk_mac.py` | Path to k-skill auth helper script |
| `KAKAOTALK_SYNC_WAIT_SEC` | `5` | Seconds to wait after launching KakaoTalk for DB sync |

### Chat alias format

`chats.json`:

```json
{
  "family": 100000000000001,
  "team": 100000000000002,
  "ai-news": 100000000000003
}
```

Then call `kakao_read(chat="family", days=7)` instead of remembering the numeric id.

## Library usage (without MCP)

The reader is reusable as a standalone Python library:

```python
from kakaotalk_mcp import KakaoReader

reader = KakaoReader()

# By date range (last 7 days)
msgs = reader.fetch(chat_id=100000000000001, days=7)

# Most recent N
msgs = reader.fetch_recent(chat_id=100000000000001, limit=50)

# Search across all chats (also useful for discovering chat_ids)
hits = reader.search("dividend", limit=20)

# Find candidate chat_ids by keyword
candidates = reader.find_chat_by_name("family")
# → present to user, do NOT auto-select
```

Including type=72 messages (official notification bots from banks, securities firms, etc.):

```python
msgs = reader.fetch(chat_id=100000000000004, days=18, text_only=False)
```

## Safety notes

- **Reads are safe** but **sends are real**. `kakao_send` actually delivers the message — there's no undo. Use `dry_run=True` to preview.
- **`kakaocli harvest` / `inspect` / `send` are NOT used by this server's read path.** Those commands scroll the UI and can reset your read-position. We use `kakaocli query` (DB-only) instead.
- **Auth cache contains your DB decryption key.** Keep `~/.cache/k-skill/kakaotalk-mac-auth.json` private. Anyone with that file can read your local KakaoTalk DB.
- This server **does not transmit messages anywhere** — it runs locally and returns results to the calling MCP client only.

## Troubleshooting

**Permission dialog every time:**
- Make sure Full Disk Access is granted to **all three** of: your terminal app (or `claude.app`), `/opt/homebrew/bin/python3.12` (or whichever Python you use), `/opt/homebrew/bin/kakaocli`.

**`kakaocli` times out (60s):**
- The default timeout is now 180s with one retry. If you still time out, check whether SQLCipher has a lock — often the morning sync after KakaoTalk launches.

**Mobile-only messages not showing up:**
- The Mac app needs to be open to sync from the server. The MCP `_sync_kakaotalk()` step does this automatically before reads (5s wait by default — increase with `KAKAOTALK_SYNC_WAIT_SEC`).

**Group chat names show as `(unknown)`:**
- Normal. Use `kakao_search` with a keyword to find the chat_id, then alias it in `chats.json`.

## License

[MIT](LICENSE) © 2026 Kemy / Ikda Company

## Acknowledgments

- [`kakaocli`](https://github.com/silver-flight-group/kakaocli) by silver-flight-group — the underlying CLI that reads the local DB.
- [`k-skill`](https://github.com/NomaDamas/k-skill) by NomaDamas — auth helper that extracts the database path and decryption key.
- [`FastMCP`](https://github.com/jlowin/fastmcp) — the Python MCP framework used here.
