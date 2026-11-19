# AI Usage Geek Dashboard ⚡

A high-tech, multi-tenant terminal dashboard monitoring your AI coding assistant quotas in real-time.

Supports:
- **Kiro CLI** (`kiro-cli chat --no-interactive "/usage"`)
- **Claude Code** (`claude -p "/usage"`)
- **AGY (Antigravity)** (`agy -p "/usage"`)
- **OpenAI Codex CLI** (`codex exec "/status"`, `codex -p "/status"`)

---

## ⚡ tmux Status Bar Center Indicator

Displays a compact, dual-window quota indicator in the **exact geometric center** (`align=absolute-centre`) of your tmux status bar:

```text
# Nerd Font icon style (dual quota: short-term% / weekly%):
⚡ 󰚩 97% │ 󰘑 0%/13% │ 󰑣 2%/11% │ 󰘧 14%/42%

# Optional symmetrical Kiro dash (--kiro-dash):
⚡ 󰚩 97%/- │ 󰘑 0%/13% │ 󰑣 2%/11% │ 󰘧 14%/42%

# Plain ASCII style:
⚡ K:97% | C:0%/13% | A:2%/11% | X:14%/42%
```

### Dual-Window Quota Logic
- **Claude Code**: `Session% / Weekly%` (e.g. `0%/13%`)
- **AGY (Antigravity)**: `5-Hour% / Weekly%` (e.g. `2%/11%`)
- **OpenAI Codex CLI**: `5-Hour% / Weekly%` (e.g. `14%/42%`)
- **Kiro CLI**: `Total Monthly Plan%` (e.g. `97%` or `97%/-`)
- **Independent Color Coding**: Each percentage number in `<short>%/<weekly>%` is color-coded individually based on its own threshold!

### Background Daemon Watch Mode (Recommended)
Run the monitor in the background to continuously update your tmux status bar without lagging your shell:

```bash
# Start background daemon with 180s interval and dark pill badge
usage --tmux -w 180 -d --badge

# Symmetrical Kiro dash format
usage --tmux -w 180 -d --badge --kiro-dash

# Reset tmux status-format cleanly
usage --reset

# Check background daemon status
usage --status

# Stop background daemon
usage --stop
```

### Visibility & Theming
- **Adaptive Default**: Safe usage (<60%) uses `#[default]` (inheriting the status bar's natural text color), so it is guaranteed 100% visible on **both green and dark status bars**.
- **--badge**: Wraps the indicator inside a dark pill background (`#[bg=colour236]`) for a clean, floating pill aesthetic.
- **--kiro-dash**: Appends `/-` to Kiro's single quota (e.g. `97%/-`) for visual symmetry with dual-quota tools.
- **--green-safe**: Forces green text for safe usage on dark-themed status bars.
- **--plain**: Uses plain text labels (`K:`, `C:`, `A:`, `X:`) instead of Nerd Font icons.

---

## 🖥 Terminal Dashboard Commands

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
