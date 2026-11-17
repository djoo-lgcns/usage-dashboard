#!/usr/bin/env python3
"""
Geek AI Usage Dashboard
Monitors Kiro CLI, Claude Code, and AGY usage quotas and statistics.
"""

import argparse
import concurrent.futures
import datetime
import os
import re
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
    """Run a shell command and return stdout or stderr."""
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


def parse_kiro(output):
    """
    Parses kiro usage output:
    Estimated Usage | resets on 2026-12-01 | KIRO POWER
    Credits (5830.00 of 10000 covered in plan), 58.3%
    Your plan is managed by your organization's administrator.
    """
    data = {
        "plan": "KIRO POWER",
        "resets": "Unknown",
        "credits_used": 0.0,
        "credits_total": 0.0,
        "percentage": 0.0,
        "note": "",
        "raw": output
    }
    
    for line in output.splitlines():
        line = line.strip()
        if "Estimated Usage" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                data["resets"] = parts[1].replace("resets on", "").strip()
            if len(parts) >= 3:
                data["plan"] = parts[2].strip()
        elif "Credits (" in line:
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

    current_section = None

    for line in output.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue

        if "Current session:" in line_clean:
            m = re.search(r"Current session:\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["session_pct"] = float(m.group(1))
                data["session_reset"] = m.group(2).strip()
        elif "Current week (all models):" in line_clean:
            m = re.search(r"Current week \(all models\):\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["week_all_pct"] = float(m.group(1))
                data["week_all_reset"] = m.group(2).strip()
        elif "Current week (Fable):" in line_clean:
            m = re.search(r"Current week \(Fable\):\s*([0-9.]+)%\s*used\s*·\s*resets\s*(.*)", line_clean)
            if m:
                data["week_fable_pct"] = float(m.group(1))
                data["week_fable_reset"] = m.group(2).strip()
        elif "Last 24h" in line_clean:
            current_section = "24h"
            m = re.search(r"Last 24h\s*·\s*(\d+)\s*requests\s*·\s*(\d+)\s*sessions", line_clean)
            if m:
                data["h24_reqs"] = m.group(1)
                data["h24_sessions"] = m.group(2)
        elif "Last 7d" in line_clean:
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
    for line in output.splitlines():
        line_clean = line.strip()
        m = re.search(r"^(.*?)\s+((?:Weekly|Five Hour|Daily|Monthly|Session)[^0-9%]+?)\s+(\d+)%\s+(.*)$", line_clean)
        if m:
            model_grp = m.group(1).strip()
            limit_type = m.group(2).strip()
            rem_pct = float(m.group(3))
            reset_ts = m.group(4).strip()
            
            # Clean up limit label
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
    return {"items": items, "raw": output}


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


def fetch_all_usages():
    """Fetch usage from kiro, claude, and agy concurrently."""
    kiro_cmd = 'kiro-cli chat --no-interactive "/usage"'
    claude_cmd = 'claude -p "/usage"'
    agy_cmd = 'agy -p "/usage"'
    
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_kiro = executor.submit(run_command, kiro_cmd)
        future_claude = executor.submit(run_command, claude_cmd)
        future_agy = executor.submit(run_command, agy_cmd)
        
        _, kiro_out = future_kiro.result()
        _, claude_out = future_claude.result()
        _, agy_out = future_agy.result()
        
        results["kiro"] = parse_kiro(kiro_out)
        results["claude"] = parse_claude(claude_out)
        results["agy"] = parse_agy(agy_out)
        
    return results


def render_dashboard(results):
    """Build the geek dashboard renderable."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kiro = results["kiro"]
    claude = results["claude"]
    agy = results["agy"]

    # Top Header
    header_table = Table.grid(expand=True)
    header_table.add_column(justify="left", ratio=3)
    header_table.add_column(justify="right", ratio=2)
    header_table.add_row(
        Text("⚡ USAGE MONITOR // QUOTAS", style="bold cyan"),
        Text(f"REFRESH: {now_str} ", style="dim green")
    )
    header_panel = Panel(
        header_table,
        border_style="bright_blue",
        box=box.DOUBLE_EDGE,
        padding=(0, 1)
    )

    # 1. Kiro Panel
    kiro_table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    kiro_table.add_column(style="bold white", ratio=4)
    kiro_table.add_column(style="white", ratio=6)

    k_bar, k_color = make_gauge_bar(kiro["percentage"], width=22)
    credits_left = max(0.0, kiro["credits_total"] - kiro["credits_used"])
    
    kiro_table.add_row("Plan Tier", f"[bold magenta]{kiro['plan']}[/bold magenta]")
    kiro_table.add_row("Usage Quota", f"{k_bar}  [{k_color}]{kiro['percentage']:.1f}%[/{k_color}] used")
    kiro_table.add_row(
        "Credits Used",
        f"[bold white]{kiro['credits_used']:,.2f}[/bold white] / [dim]{kiro['credits_total']:,.2f}[/dim] (Left: [green]{credits_left:,.2f}[/green])"
    )
    kiro_table.add_row("Cycle Reset", f"[yellow]{kiro['resets']}[/yellow]")
    if kiro["note"]:
        kiro_table.add_row("Status", f"[dim italic]{kiro['note']}[/dim italic]")

    kiro_panel = Panel(
        kiro_table,
        title="[bold blue]󰚩 KIRO-CLI MONITOR[/bold blue]",
        border_style="blue",
        box=box.ROUNDED,
        padding=(0, 1)
    )

    # 2. Claude Panel
    claude_table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    claude_table.add_column(style="bold white", ratio=4)
    claude_table.add_column(style="white", ratio=6)

    s_bar, s_color = make_gauge_bar(claude["session_pct"], width=22)
    w_bar, w_color = make_gauge_bar(claude["week_all_pct"], width=22)
    f_bar, f_color = make_gauge_bar(claude["week_fable_pct"], width=22)

    claude_table.add_row(
        "Session Usage",
        f"{s_bar}  [{s_color}]{claude['session_pct']:.0f}%[/{s_color}] used [dim](resets {claude['session_reset']})[/dim]"
    )
    claude_table.add_row(
        "Week (All Models)",
        f"{w_bar}  [{w_color}]{claude['week_all_pct']:.0f}%[/{w_color}] used [dim](resets {claude['week_all_reset']})[/dim]"
    )
    claude_table.add_row(
        "Week (Fable)",
        f"{f_bar}  [{f_color}]{claude['week_fable_pct']:.0f}%[/{f_color}] used [dim](resets {claude['week_fable_reset']})[/dim]"
    )

    claude_panel = Panel(
        claude_table,
        title="[bold yellow]󰘑 CLAUDE CODE MONITOR[/bold yellow]",
        border_style="bright_yellow",
        box=box.ROUNDED,
        padding=(0, 1)
    )

    # 3. AGY Panel
    agy_table = Table(box=None, show_header=False, expand=True, padding=(0, 1))
    agy_table.add_column(style="bold white", ratio=4)
    agy_table.add_column(style="white", ratio=6)

    if agy["items"]:
        for item in agy["items"]:
            a_bar, a_color = make_gauge_bar(item["used_pct"], width=22)
            agy_table.add_row(
                item["label"],
                f"{a_bar}  [{a_color}]{item['used_pct']:.0f}%[/{a_color}] used [dim]({item['remaining_pct']:.0f}% left · resets {item['reset']})[/dim]"
            )
    else:
        agy_table.add_row("Status", "[dim italic]No AGY quota data returned[/dim italic]")

    agy_panel = Panel(
        agy_table,
        title="[bold magenta]󰑣 ANTIGRAVITY MONITOR[/bold magenta]",
        border_style="magenta",
        box=box.ROUNDED,
        padding=(0, 1)
    )

    # 4. Cost & Activity Driver Analytics Panel
    insights_table = Table(box=None, expand=True, padding=(0, 1))
    insights_table.add_column("Window", style="bold cyan", width=12)
    insights_table.add_column("Traffic", style="bold white", width=22)
    insights_table.add_column("Top Consumption Drivers", style="dim white")

    top_24h_str = " • ".join(claude["h24_top"][:3]) if claude["h24_top"] else "[dim]No activity recorded[/dim]"
    insights_table.add_row(
        "Last 24h",
        f"[green]{claude['h24_reqs']}[/green] reqs / [magenta]{claude['h24_sessions']}[/magenta] sessions",
        top_24h_str
    )

    top_7d_str = " • ".join(claude["d7_top"][:3]) if claude["d7_top"] else "[dim]No activity recorded[/dim]"
    insights_table.add_row(
        "Last 7d",
        f"[green]{claude['d7_reqs']}[/green] reqs / [magenta]{claude['d7_sessions']}[/magenta] sessions",
        top_7d_str
    )

    insights_panel = Panel(
        insights_table,
        title="[bold green]󰄬 CONSUMPTION INSIGHTS & DRIVERS[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        padding=(0, 1)
    )

    # Footer
    footer = Text(" Press Ctrl+C to exit | Run with --watch [sec] for continuous live monitoring", style="dim italic white")

    main_grid = Table.grid(expand=True)
    main_grid.add_row(header_panel)
    main_grid.add_row(kiro_panel)
    main_grid.add_row(claude_panel)
    main_grid.add_row(agy_panel)
    main_grid.add_row(insights_panel)
    main_grid.add_row(footer)

    return main_grid


def main():
    parser = argparse.ArgumentParser(description="Geek AI Usage Dashboard (Kiro + Claude + AGY)")
    parser.add_argument("-w", "--watch", type=int, nargs="?", const=60, default=None,
                        help="Watch mode: continuously refresh every N seconds (default: 60)")
    parser.add_argument("--raw", action="store_true", help="Print raw outputs from all CLIs")
    args = parser.parse_args()

    if args.raw:
        print("=== KIRO USAGE ===")
        _, k_out = run_command('kiro-cli chat --no-interactive "/usage"')
        print(k_out)
        print("\n=== CLAUDE USAGE ===")
        _, c_out = run_command('claude -p "/usage"')
        print(c_out)
        print("\n=== AGY USAGE ===")
        _, a_out = run_command('agy -p "/usage"')
        print(a_out)
        return

    if args.watch is None:
        with console.status("[bold cyan]Querying neural gateways (kiro, claude, agy)...[/bold cyan]", spinner="dots12"):
            results = fetch_all_usages()
        console.print(render_dashboard(results))
    else:
        interval = max(5, args.watch)
        console.clear()
        try:
            with Live(console=console, screen=True, refresh_per_second=2) as live:
                while True:
                    live.update(Panel(Text("Querying APIs in parallel...", style="bold cyan"), box=box.ROUNDED))
                    results = fetch_all_usages()
                    live.update(render_dashboard(results))
                    time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard closed.[/dim]")


if __name__ == "__main__":
    main()
