# Installation Guide

A step-by-step guide to get `kakaotalk-mcp` running on macOS.

## Prerequisites checklist

- [ ] macOS (tested on macOS 13+ / Apple Silicon)
- [ ] KakaoTalk Mac app installed and logged in
- [ ] Homebrew installed
- [ ] Python 3.10+

## Step 1 — Install `kakaocli`

```bash
brew install silver-flight-group/tap/kakaocli
```

Verify:

```bash
kakaocli --version
# expected: 0.6.0 (or newer)
```

The `silver-flight-group/tap` formula also installs `kmsg` (used by `kakao_send`).

## Step 2 — Grant Full Disk Access

KakaoTalk's local DB is at `~/Library/Containers/com.kakao.KakaoTalkMac/Data/...`. macOS protects this path via TCC, so you must grant **Full Disk Access** to every binary in the chain:

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Click **+** and add:
   - Your terminal app (Terminal.app, iTerm2, or `claude.app` if running from Claude Code)
   - The Python interpreter you'll use (e.g. `/opt/homebrew/bin/python3.12`)
   - `/opt/homebrew/bin/kakaocli`
   - `/usr/sbin/cron` *(only if you'll run the MCP server from cron)*

> **Tip:** If a binary doesn't show up in the "+" dialog, navigate via Cmd+Shift+G and paste the absolute path.

## Step 3 — Bootstrap the auth cache

The reader needs two things from KakaoTalk: the **database file path** and the **decryption key**. These are extracted by the [`k-skill`](https://github.com/NomaDamas/k-skill) helper script.

⚠️ **Supply-chain caution.** You are about to download and execute a Python script that will read your local KakaoTalk database. Pin to a known commit and verify the checksum before running.

```bash
# 1. Create a private user-local directory for the helper
mkdir -p ~/.local/share/kakaotalk-mcp

# 2. Pin to a specific k-skill commit (replace SHA below with the one you trust)
KSKILL_COMMIT=ed30f22f86ab5e7515a8c7211b3702bfec00fba4

# 3. Download
curl -fsSL \
  "https://raw.githubusercontent.com/NomaDamas/k-skill/${KSKILL_COMMIT}/kakaotalk-mac/scripts/kakaotalk_mac.py" \
  -o ~/.local/share/kakaotalk-mcp/kakaotalk_mac.py

# 4. Verify checksum (must match — abort if it doesn't)
#    SHA256 for k-skill@ed30f22f (2026-05-25):
echo "6a73749638b294198ce3ac20b4381aa11731b600bd7e3bc2463c4f5e5f00d92e  $HOME/.local/share/kakaotalk-mcp/kakaotalk_mac.py" \
  | shasum -a 256 -c

# 5. Run it to populate the auth cache
python3 ~/.local/share/kakaotalk-mcp/kakaotalk_mac.py auth --refresh
```

> The default path that `kakaotalk-mcp` looks for the helper at is `~/.local/share/kakaotalk-mcp/kakaotalk_mac.py`. Override with `KAKAOTALK_AUTH_HELPER` env var if you keep it elsewhere.

**To update the pinned commit:** check the latest commit on the [k-skill repo](https://github.com/NomaDamas/k-skill/commits/main), recompute the SHA256 yourself, and replace both values above. Don't blindly follow `main` — that's how supply-chain attacks land.

This creates `~/.cache/k-skill/kakaotalk-mac-auth.json` containing:

```json
{
  "database_path": "/Users/.../com.kakao.KakaoTalkMac/.../db.sqlite",
  "key": "<hex>"
}
```

**Keep this file private.** Anyone with this file can read your local KakaoTalk DB.

### Re-running auth

You only need to re-run `kakaotalk_mac.py auth --refresh` if:
- You re-installed KakaoTalk
- You logged out and back in to a different account
- The auth cache file got deleted

## Step 4 — Install kakaotalk-mcp

### Option A — clone and `pip install -e`

```bash
git clone https://github.com/kemy-ai/kakaotalk-mcp.git
cd kakaotalk-mcp
python3 -m pip install -e .
```

### Option B — uv

```bash
git clone https://github.com/kemy-ai/kakaotalk-mcp.git
cd kakaotalk-mcp
uv venv
uv pip install -e .
```

### Option C — pipx (isolated global install)

```bash
pipx install git+https://github.com/kemy-ai/kakaotalk-mcp.git
```

Verify:

```bash
which kakaotalk-mcp
kakaotalk-mcp --help 2>&1 | head  # FastMCP doesn't expose --help but should not crash
```

## Step 5 — (Optional) Configure chat aliases

Numeric chat_ids are hard to remember. Set up aliases:

```bash
mkdir -p ~/.config/kakaotalk-mcp
cp examples/chats.example.json ~/.config/kakaotalk-mcp/chats.json
$EDITOR ~/.config/kakaotalk-mcp/chats.json
```

To **discover chat_ids**, run a search first:

```bash
# Need a keyword from messages in the target chat
python3 -c "from kakaotalk_mcp import KakaoReader; \
  print(KakaoReader().find_chat_by_name('mom'))"
```

Then put the results into `chats.json`:

```json
{
  "mom": 100000000000001,
  "team-standup": 100000000000002
}
```

## Step 6 — Register with Claude Code

Edit `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "kakaotalk": {
      "command": "kakaotalk-mcp"
    }
  }
}
```

If you installed via `uv venv` and don't want to activate it manually:

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

Restart Claude Code. The 5 tools (`kakao_read`, `kakao_read_recent`, `kakao_search`, `kakao_list_chats`, `kakao_send`) should now be available.

## Verifying everything works

In Claude Code:

```
Use kakao_list_chats to show my configured chats.
```

Then:

```
Use kakao_search to find any chat that mentions "hello".
```

If you see results, the chain (Full Disk Access → kakaocli → auth cache → MCP server) is wired correctly.

## Common issues

### `RuntimeError: kakaocli query failed: ... permission denied`

→ Full Disk Access missing. Re-check Step 2.

### `Auth cache not found: ~/.cache/k-skill/kakaotalk-mac-auth.json`

→ Step 3 wasn't run, or the cache got deleted. Re-run the helper.

### `subprocess.TimeoutExpired` after 180s

→ Usually the morning KakaoTalk sync / WAL checkpoint holding a SQLCipher lock. The reader already retries once after 30s. If still failing, close KakaoTalk briefly and reopen.

### Permission dialog ("claude.app wants to manage your computer") every time

→ Known macOS 26+ behavior when modifying cron via Claude Code's Bash tool. Doesn't affect cron-scheduled runs (which use the cron daemon directly).

### Group chats show as `(unknown)`

→ Normal for `kakao_list_chats`. Use `kakao_search` with a keyword from the chat to discover the chat_id.
