# AI Usage Geek Dashboard ⚡

A high-tech, multi-tenant terminal dashboard monitoring your AI coding assistant quotas in real-time.

Supports:
- **Kiro CLI** (`kiro-cli chat --no-interactive "/usage"`)
- **Claude Code** (`claude -p "/usage"`)
- **AGY (Antigravity)** (`agy -p "/usage"`)
- **OpenAI Codex CLI** (`codex exec "/status"`, `codex -p "/status"`)

---

## Key Highlights

### 1. Smart Dynamic Detection
Users don't need to install or use all 4 tools. The dashboard automatically scans `$PATH` and **only renders cards for the tools you actually use**.
- If a developer only uses Claude, only Claude's metrics and activity insights appear.
- If a developer uses Claude + Codex, only those two appear.
- The title bar and grid layout adapt dynamically to your active environment.

### 2. OpenAI Codex CLI Support
Full support for Codex CLI rate limits:
- **5-Hour Rolling Window**: Usage gauge bar, percentage used, and cycle reset timestamp.
- **Weekly Allowance**: Usage gauge bar, percentage used, and cycle reset timestamp.
- **Active Model & Tier Detection**.

### 3. Concurrent Multi-Threaded Querying
All active tools are queried in parallel via thread workers, maintaining lightning-fast rendering without sequential waiting.

---

## Commands

```bash
# Snapshot view (automatically discovers active tools)
usage

# Live continuous monitor (refreshes every 30 seconds)
usage -w 30

# Explicitly filter specific tools
usage -t claude,codex

# Inspect raw output from all detected tools
usage --raw

# Force query all tools (including uninstalled/unconfigured ones)
usage --all
```
