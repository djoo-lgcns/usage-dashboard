# AI Usage Geek Dashboard ⚡

A high-tech terminal dashboard monitoring your **Kiro CLI** and **Claude Code** usage quotas, credits, reset schedules, and activity analytics concurrently in real-time.

## Features
- **Parallel Querying**: Fetches `kiro-cli chat --no-interactive "/usage"` and `claude -p "/usage"` concurrently in background threads.
- **Cyberpunk Terminal Aesthetic**: Rich color-coded gauge bars (`████░░░░`), formatted quota meters, and clean unicode card layouts.
- **Consumption Analytics**: Displays top contributors and request/session metrics for 24-hour and 7-day windows.
- **Live Watch Mode**: Continuously refreshes the dashboard without flickering.
- **Zero Configuration**: Ready to run globally as `usage` or `usage-dash`.

## Usage

```bash
# Print the geek dashboard once
usage
# or
usage-dash

# Live watch mode (refreshes every 30 seconds)
usage --watch 30

# View raw outputs
usage --raw
```
