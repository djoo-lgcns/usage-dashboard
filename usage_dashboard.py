#!/usr/bin/env python3
"""
Geek AI Usage Dashboard
Monitors Kiro CLI, Claude Code, AGY (Antigravity), and OpenAI Codex CLI usage quotas and statistics.
Automatically detects which tools are installed and dynamically tailors the dashboard.
"""

import argparse
import concurrent.futures
import datetime
import os
import re
import shutil
import subprocess
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.layout import Layout
from rich.live import Live
from rich import box

console = Console()

def run_command(cmd, timeout=20):
    """Run a shell command and return (returncode, stdout_or_stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
    except Exception as e:
        return 1, str(e)


def is_tool_installed(binary_name):
    """Check if the given binary is present in the system PATH."""
    return shutil.which(binary_name) is not None


# ============================================================================
# PARSERS
# ============================================================================

def parse_kiro(output):
    """
    Parses kiro usage output:
    Estimated Usage | resets on 2026-12-01 | KIRO POWER
    Credits (5830.00 of 10000 covered in plan), 58.3%
    Your plan is managed by your organization's administrator.
    """
    data = {
        "success": False,
        "plan": "KIRO POWER",
        "resets": "Unknown",
        "credits_used": 0.0,
        "credits_total": 0.0,
        "percentage": 0.0,
        "note": "",
        "raw": output
    }
    
    if not output:
        return data

    for line in output.splitlines():
        line = line.strip()
        if "Estimated Usage" in line:
            data["success"] = True
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                data["resets"] = parts[1].replace("resets on", "").strip()
            if len(parts) >= 3:
                data["plan"] = parts[2].strip()
        elif "Credits (" in line:
            data["success"] = True
            m = re.search(r"Credits\s*\(([0-9.]+)\s+of\s+([0-9.]+)[^)]*\),\s*([0-9.]+)%", line)
            if m:
                data["credits_used"] = float(m.group(1))
                data["credits_total"] = float(m.group(2))
                data["percentage"] = float(m.group(3))
        elif "plan is managed" in line.lower() or "administrator" in line.lower():
            data["note"] = line

    return data


def parse_claude(output):
    """
    Parses claude -p "/usage" output
    """
    data = {
        "success": False,
        "session_pct": 0.0,
        "session_reset": "Unknown",
        "week_all_pct": 0.0,
        "week_all_reset": "Unknown",
        "week_fable_pct": 0.0,
        "week_fable_reset": "Unknown",
        "h24_reqs": "0",
        "h24_sessions": "0",
        "h24_top": [],
        "d7_reqs": "0",
        "d7_sessions": "0",
        "d7_top": [],
        "raw": output
    }

    if not output:
        return data

    current_section = None

    for line in output.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        if "Current session:" in line_clean:
            data["success"] = True
            m = re.search(r"Current session:\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["session_pct"] = float(m.group(1))
                data["session_reset"] = m.group(2).strip()
        elif "Current week (all models):" in line_clean:
            data["success"] = True
            m = re.search(r"Current week \(all models\):\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["week_all_pct"] = float(m.group(1))
                data["week_all_reset"] = m.group(2).strip()
        elif "Current week (Fable):" in line_clean:
            data["success"] = True
            m = re.search(r"Current week \(Fable\):\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["week_fable_pct"] = float(m.group(1))
                data["week_fable_reset"] = m.group(2).strip()
        elif "Last 24h" in line_clean:
            data["success"] = True
            current_section = "24h"
            m = re.search(r"Last 24h\s*·\s*(\d+)\s*requests\s*·\s*(\d+)\s*sessions", line_clean)
            if m:
                data["h24_reqs"] = m.group(1)
                data["h24_sessions"] = m.group(2)
        elif "Last 7d" in line_clean:
            data["success"] = True
            current_section = "7d"
            m = re.search(r"Last 7d\s*·\s*(\d+)\s*requests\s*·\s*(\d+)\s*sessions", line_clean)
            if m:
                data["d7_reqs"] = m.group(1)
                data["d7_sessions"] = m.group(2)
        elif current_section == "24h":
            if line_clean.startswith("Top ") or re.match(r"^\d+%", line_clean):
                data["h24_top"].append(line_clean)
        elif current_section == "7d":
            if line_clean.startswith("Top ") or re.match(r"^\d+%", line_clean):
                data["d7_top"].append(line_clean)

    return data


def parse_agy(output):
    """
    Parses agy -p "/usage" output:
    Quota:
    Gemini Models          Weekly Limit Remaining     98%   2026-11-16 08:25 KST
    Gemini Models          Five Hour Limit Remaining  98%   2026-11-11 14:32 KST
    Claude and GPT models  Weekly Limit Remaining     100%  2026-11-18 09:37 KST
    Claude and GPT models  Five Hour Limit Remaining  100%  2026-11-11 14:37 KST
    """
    items = []
    success = False
    for line in output.splitlines():
        line_clean = line.strip()
        m = re.search(r"^(.*?)\s+((?:Weekly|Five Hour|Daily|Monthly|Session)[^0-9%]+?)\s+(\d+)%\s+(.*)$", line_clean)
        if m:
            success = True
            model_grp = m.group(1).strip()
            limit_type = m.group(2).strip()
            rem_pct = float(m.group(3))
            reset_ts = m.group(4).strip()
            
            limit_label = limit_type.replace("Limit Remaining", "").replace("Limit", "").strip()
            label = f"{model_grp} ({limit_label})"
            
            used_pct = max(0.0, min(100.0, 100.0 - rem_pct))
            items.append({
                "label": label,
                "model_group": model_grp,
                "limit_type": limit_label,
                "remaining_pct": rem_pct,
                "used_pct": used_pct,
                "reset": reset_ts
            })
    return {"success": success, "items": items, "raw": output}


def parse_codex(output):
    """
    Parses Codex CLI usage / status output.
    Supports interactive box format (/status), flat key-value formats, and rate limits.
    """
    data = {
        "success": False,
        "five_hour_pct": None,
        "five_hour_reset": "Unknown",
        "weekly_pct": None,
        "weekly_reset": "Unknown",
        "model": "",
        "plan": "",
        "note": "",
        "raw": output
    }

    if not output:
        return data

    # Strip box-drawing decorations and ANSI control characters
    cleaned = re.sub(r"[\x1b\x9b][\[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]", "", output)
    cleaned = re.sub(r"[┌┐└┘├┤│─╭╮╯╰\|•·]", " ", cleaned)
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        line_lower = line.lower()

        # 5-hour limit detection
        if "5h limit" in line_lower or "5-hour" in line_lower or "five hour" in line_lower:
            data["success"] = True
            snippet = " ".join(lines[i:i+4])
            m_pct = re.search(r"(\d+(?:\.\d+)?)%\s*used", snippet, re.IGNORECASE)
            m_rst = re.search(r"Resets?:\s*([A-Za-z0-9:,\s\./]+?)(?=\s*(?:Weekly|5h|Week|Five|•|\n|$))", snippet, re.IGNORECASE)
            if m_pct:
                data["five_hour_pct"] = float(m_pct.group(1))
            if m_rst:
                data["five_hour_reset"] = m_rst.group(1).strip()

        # Weekly limit detection
        elif "weekly limit" in line_lower or "week limit" in line_lower:
            data["success"] = True
            snippet = " ".join(lines[i:i+4])
            m_pct = re.search(r"(\d+(?:\.\d+)?)%\s*used", snippet, re.IGNORECASE)
            m_rst = re.search(r"Resets?:\s*([A-Za-z0-9:,\s\./]+?)(?=\s*(?:Weekly|5h|Week|Five|•|\n|$))", snippet, re.IGNORECASE)
            if m_pct:
                data["weekly_pct"] = float(m_pct.group(1))
            if m_rst:
                data["weekly_reset"] = m_rst.group(1).strip()

        # Model detection
        elif line_lower.startswith("model:"):
            data["success"] = True
            data["model"] = line.split(":", 1)[1].strip()

        # Plan detection
        elif line_lower.startswith("plan:"):
            data["success"] = True
            data["plan"] = line.split(":", 1)[1].strip()

    # Fallback search if box format wasn't strictly structured
    if data["five_hour_pct"] is None:
        m = re.search(r"5h[^\d%]*(\d+(?:\.\d+)?)%\s*used", cleaned, re.IGNORECASE)
        if m:
            data["success"] = True
            data["five_hour_pct"] = float(m.group(1))

    if data["weekly_pct"] is None:
        m = re.search(r"week[^\d%]*(\d+(?:\.\d+)?)%\s*used", cleaned, re.IGNORECASE)
        if m:
            data["success"] = True
            data["weekly_pct"] = float(m.group(1))

    return data


# ============================================================================
# RENDERING HELPERS
# ============================================================================

def make_gauge_bar(percent, width=22):
    """Return a styled color-coded gauge bar string."""
    percent = max(0.0, min(100.0, percent))
    filled_len = int(round((percent / 100.0) * width))
    empty_len = width - filled_len
    
    if percent >= 85:
        color = "bold red"
        bar_char = "█"
    elif percent >= 70:
        color = "bold yellow"
        bar_char = "█"
    elif percent >= 40:
        color = "bold cyan"
        bar_char = "█"
    else:
        color = "bold green"
        bar_char = "█"

    bar = f"[{color}]{bar_char * filled_len}[/{color}][dim white]{'░' * empty_len}[/dim white]"
    return bar, color


def render_kiro_panel(kiro):
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style="bold white", ratio=4)
    table.add_column(style="white", ratio=6)

    k_bar, k_color = make_gauge_bar(kiro["percentage"], width=22)
    credits_left = max(0.0, kiro["credits_total"] - kiro["credits_used"])
    
    table.add_row("Plan Tier", f"[bold magenta]{kiro['plan']}[/bold magenta]")
    table.add_row("Usage Quota", f"{k_bar}  [{k_color}]{kiro['percentage']:.1f}%[/{k_color}] used")
    table.add_row(
        "Credits Used",
        f"[bold white]{kiro['credits_used']:,.2f}[/bold white] / [dim]{kiro['credits_total']:,.2f}[/dim] (Left: [green]{credits_left:,.2f}[/green])"
    )
    table.add_row("Cycle Reset", f"[yellow]{kiro['resets']}[/yellow]")
    if kiro.get("note"):
        table.add_row("Status", f"[dim italic]{kiro['note']}[/dim italic]")

    return Panel(
        table,
        title="[bold blue]󰚩 KIRO MONITOR[/bold blue]",
        border_style="blue",
        box=box.ROUNDED,
        padding=(0, 1)
    )


def render_claude_panel(claude):
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style="bold white", ratio=4)
    table.add_column(style="white", ratio=6)

    s_bar, s_color = make_gauge_bar(claude["session_pct"], width=22)
    w_bar, w_color = make_gauge_bar(claude["week_all_pct"], width=22)
    f_bar, f_color = make_gauge_bar(claude["week_fable_pct"], width=22)

    table.add_row(
        "Session Usage",
        f"{s_bar}  [{s_color}]{claude['session_pct']:.0f}%[/{s_color}] used [dim](resets {claude['session_reset']})[/dim]"
    )
    table.add_row(
        "Week (All Models)",
        f"{w_bar}  [{w_color}]{claude['week_all_pct']:.0f}%[/{w_color}] used [dim](resets {claude['week_all_reset']})[/dim]"
    )
    table.add_row(
        "Week (Fable)",
        f"{f_bar}  [{f_color}]{claude['week_fable_pct']:.0f}%[/{f_color}] used [dim](resets {claude['week_fable_reset']})[/dim]"
    )

    return Panel(
        table,
        title="[bold yellow]󰘑 CLAUDE CODE MONITOR[/bold yellow]",
        border_style="bright_yellow",
        box=box.ROUNDED,
        padding=(0, 1)
    )


def render_agy_panel(agy):
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style="bold white", ratio=4)
    table.add_column(style="white", ratio=6)

    if agy.get("items"):
        for item in agy["items"]:
            a_bar, a_color = make_gauge_bar(item["used_pct"], width=22)
            table.add_row(
                item["label"],
                f"{a_bar}  [{a_color}]{item['used_pct']:.0f}%[/{a_color}] used [dim]({item['remaining_pct']:.0f}% left · resets {item['reset']})[/dim]"
            )
    else:
        table.add_row("Status", "[dim italic]No active quota returned[/dim italic]")

    return Panel(
        table,
        title="[bold magenta]󰑣 ANTIGRAVITY MONITOR[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1)
    )


def render_codex_panel(codex):
    table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    table.add_column(style="bold white", ratio=4)
    table.add_column(style="white", ratio=6)

    has_row = False
    if codex.get("five_hour_pct") is not None:
        f_bar, f_color = make_gauge_bar(codex["five_hour_pct"], width=22)
        table.add_row(
            "5-Hour Window",
            f"{f_bar}  [{f_color}]{codex['five_hour_pct']:.0f}%[/{f_color}] used [dim](resets {codex['five_hour_reset']})[/dim]"
        )
        has_row = True

    if codex.get("weekly_pct") is not None:
        w_bar, w_color = make_gauge_bar(codex["weekly_pct"], width=22)
        table.add_row(
            "Weekly Limit",
            f"{w_bar}  [{w_color}]{codex['weekly_pct']:.0f}%[/{w_color}] used [dim](resets {codex['weekly_reset']})[/dim]"
        )
        has_row = True

    if codex.get("model"):
        table.add_row("Active Model", f"[cyan]{codex['model']}[/cyan]")
        has_row = True

    if codex.get("plan"):
        table.add_row("Plan Tier", f"[bold green]{codex['plan']}[/bold green]")
        has_row = True

    if not has_row:
        table.add_row("Status", "[dim italic]Active (Rate limits update upon prompt execution)[/dim italic]")

    return Panel(
        table,
        title="[bold green]󰘧 CODEX CLI MONITOR[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1)
    )


def render_claude_insights(claude):
    insights_table = Table(box=None, expand=True, padding=(0, 1))
    insights_table.add_column("Window", style="bold cyan", width=12)
    insights_table.add_column("Traffic", style="bold white", width=22)
    insights_table.add_column("Top Consumption Drivers", style="dim white")

    top_24h_str = " • ".join(claude["h24_top"][:3]) if claude.get("h24_top") else "[dim]No activity recorded[/dim]"
    insights_table.add_row(
        "Last 24h",
        f"[green]{claude.get('h24_reqs', '0')}[/green] reqs / [magenta]{claude.get('h24_sessions', '0')}[/magenta] sessions",
        top_24h_str
    )

    top_7d_str = " • ".join(claude["d7_top"][:3]) if claude.get("d7_top") else "[dim]No activity recorded[/dim]"
    insights_table.add_row(
        "Last 7d",
        f"[green]{claude.get('d7_reqs', '0')}[/green] reqs / [magenta]{claude.get('d7_sessions', '0')}[/magenta] sessions",
        top_7d_str
    )

    return Panel(
        insights_table,
        title="[bold green]󰄬 CLAUDE CONSUMPTION INSIGHTS & DRIVERS[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1)
    )


# ============================================================================
# TOOL REGISTRY & DISCOVERY
# ============================================================================

TOOL_REGISTRY = {
    "kiro": {
        "label": "KIRO",
        "binary": "kiro-cli",
        "commands": ['kiro-cli chat --no-interactive "/usage"'],
        "parser": parse_kiro,
        "renderer": render_kiro_panel
    },
    "claude": {
        "label": "CLAUDE",
        "binary": "claude",
        "commands": ['claude -p "/usage"'],
        "parser": parse_claude,
        "renderer": render_claude_panel
    },
    "agy": {
        "label": "AGY",
        "binary": "agy",
        "commands": ['agy -p "/usage"'],
        "parser": parse_agy,
        "renderer": render_agy_panel
    },
    "codex": {
        "label": "CODEX",
        "binary": "codex",
        "commands": [
            'codex exec "/status"',
            'codex -p "/status"',
            'codex -p "/usage"',
            'codex status'
        ],
        "parser": parse_codex,
        "renderer": render_codex_panel
    }
}


def query_tool(key, config):
    """Attempt command(s) for a tool and parse the output."""
    raw_output = ""
    for cmd in config["commands"]:
        code, out = run_command(cmd)
        if code == 0 and out.strip():
            raw_output = out
            break
        elif out.strip():
            # Keep track of last non-empty output even on nonzero exit code
            raw_output = out

    parsed = config["parser"](raw_output)
    return key, parsed, raw_output


def fetch_usages(selected_tools=None, force_all=False):
    """
    Detect available tools and fetch their usages concurrently.
    Only queries tools whose binaries exist in PATH.
    Filters out unsuccessful/unconfigured tools unless force_all is True.
    """
    tools_to_query = {}
    
    for key, cfg in TOOL_REGISTRY.items():
        if selected_tools and key not in selected_tools:
            continue
        # Check binary presence
        if force_all or is_tool_installed(cfg["binary"]):
            tools_to_query[key] = cfg

    results = {}
    if not tools_to_query:
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(tools_to_query))) as executor:
        future_map = {
            executor.submit(query_tool, key, cfg): key
            for key, cfg in tools_to_query.items()
        }
        for future in concurrent.futures.as_completed(future_map):
            key, parsed, raw = future.result()
            # If not force_all, only include if parsed successfully
            if force_all or parsed.get("success"):
                results[key] = parsed

    return results


def render_dashboard(results):
    """Dynamically assemble the dashboard for whichever tools are active."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # If no tools are active
    if not results:
        table = Table(box=None, show_header=False, expand=True)
        table.add_column(style="bold white", width=18)
        table.add_column(style="dim white")

        table.add_row("Kiro CLI", "kiro-cli chat --no-interactive \"/usage\"")
        table.add_row("Claude Code", "claude -p \"/usage\"")
        table.add_row("AGY", "agy -p \"/usage\"")
        table.add_row("Codex CLI", "codex exec \"/status\"")
        
        info_panel = Panel(
            table,
            title="[bold yellow]⚡ NO ACTIVE AI TOOLS DETECTED[/bold yellow]",
            subtitle="[dim]Run with --all or --raw to inspect unconfigured tools[/dim]",
            border_style="yellow",
            box=box.ROUNDED,
            padding=(1, 2)
        )
        return info_panel

    # Active tool names for header
    active_labels = [TOOL_REGISTRY[k]["label"] for k in TOOL_REGISTRY if k in results]
    header_title = f"⚡ USAGE MONITOR // {' · '.join(active_labels)}"

    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left", ratio=3)
    header_table.add_column(justify="right", ratio=2)
    header_table.add_row(
        Text(header_title, style="bold cyan"),
        Text(f"REFRESH: {now_str} ", style="dim green")
    )
    header_panel = Panel(
        header_table,
        border_style="bright_blue",
        box=box.DOUBLE_EDGE,
        padding=(0, 1)
    )

    main_grid = Table.grid(expand=True)
    main_grid.add_row(header_panel)

    # Render each active tool's panel
    for key, cfg in TOOL_REGISTRY.items():
        if key in results:
            data = results[key]
            panel = cfg["renderer"](data)
            main_grid.add_row(panel)

    # Render Claude insights panel if Claude is active and has metrics
    if "claude" in results:
        claude_data = results["claude"]
        if claude_data.get("h24_top") or claude_data.get("d7_top") or claude_data.get("h24_reqs") != "0":
            main_grid.add_row(render_claude_insights(claude_data))

    footer = Text(" Press Ctrl+C to exit | Run with --watch [sec] for continuous live monitoring", style="dim italic white")
    main_grid.add_row(footer)

    return main_grid


def main():
    parser = argparse.ArgumentParser(
        description="Geek AI Usage Dashboard (Kiro, Claude, AGY, Codex)\n"
                    "Automatically detects installed CLIs and dynamically presents active quotas."
    )
    parser.add_argument("-w", "--watch", type=int, nargs="?", const=60, default=None,
                        help="Watch mode: continuously refresh every N seconds (default: 60)")
    parser.add_argument("-t", "--tools", type=str, default=None,
                        help="Comma-separated subset of tools to monitor (e.g. -t claude,codex)")
    parser.add_argument("--all", action="store_true",
                        help="Force query all tools, even if their binary is not found in PATH")
    parser.add_argument("--raw", action="store_true",
                        help="Print raw outputs from all detected CLIs")
    args = parser.parse_args()

    selected = [t.strip().lower() for t in args.tools.split(",")] if args.tools else None

    if args.raw:
        print("=== DETECTED TOOLS RAW DUMP ===")
        for key, cfg in TOOL_REGISTRY.items():
            if selected and key not in selected:
                continue
            is_present = is_tool_installed(cfg["binary"])
            print(f"\n[{key.upper()}] (Binary: {cfg['binary']} | Found: {is_present})")
            if is_present or args.all:
                for cmd in cfg["commands"]:
                    code, out = run_command(cmd)
                    print(f"Command: {cmd} (exit {code}):")
                    print(out if out else "<no output>")
                    if code == 0:
                        break
        return

    # Snapshot or watch mode
    if args.watch is None:
        with console.status("[bold cyan]Querying active usages in parallel...[/bold cyan]", spinner="dots12"):
            results = fetch_usages(selected_tools=selected, force_all=args.all)
        console.print(render_dashboard(results))
    else:
        interval = max(5, args.watch)
        console.clear()
        try:
            with Live(console=console, screen=True, refresh_per_second=2) as live:
                while True:
                    live.update(Panel(Text("Querying active usages in parallel...", style="bold cyan"), box=box.ROUNDED))
                    results = fetch_usages(selected_tools=selected, force_all=args.all)
                    live.update(render_dashboard(results))
                    time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard closed.[/dim]")


if __name__ == "__main__":
    main()
