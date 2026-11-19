#!/usr/bin/env python3
"""
Geek AI Usage Dashboard
Monitors Kiro CLI, Claude Code, AGY (Antigravity), and OpenAI Codex CLI usage quotas and statistics.
Automatically detects installed CLIs and dynamically presents active quotas.
Includes tmux status bar absolute-center indicator with background watch mode support.
"""

import argparse
import concurrent.futures
import datetime
import os
import re
import shutil
import signal
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

    cleaned = re.sub(r"[\x1b\x9b][\[()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]", "", output)
    cleaned = re.sub(r"[┌┐└┘├┤│─╭╮╯╰\|•·]", " ", cleaned)
    lines = [l.strip() for l in cleaned.splitlines() if l.strip()]

    for i, line in enumerate(lines):
        line_lower = line.lower()

        if "5h limit" in line_lower or "5-hour" in line_lower or "five hour" in line_lower:
            data["success"] = True
            snippet = " ".join(lines[i:i+4])
            m_pct = re.search(r"(\d+(?:\.\d+)?)%\s*used", snippet, re.IGNORECASE)
            m_rst = re.search(r"Resets?:\s*([A-Za-z0-9:,\s\./]+?)(?=\s*(?:Weekly|5h|Week|Five|•|\n|$))", snippet, re.IGNORECASE)
            if m_pct:
                data["five_hour_pct"] = float(m_pct.group(1))
            if m_rst:
                data["five_hour_reset"] = m_rst.group(1).strip()

        elif "weekly limit" in line_lower or "week limit" in line_lower:
            data["success"] = True
            snippet = " ".join(lines[i:i+4])
            m_pct = re.search(r"(\d+(?:\.\d+)?)%\s*used", snippet, re.IGNORECASE)
            m_rst = re.search(r"Resets?:\s*([A-Za-z0-9:,\s\./]+?)(?=\s*(?:Weekly|5h|Week|Five|•|\n|$))", snippet, re.IGNORECASE)
            if m_pct:
                data["weekly_pct"] = float(m_pct.group(1))
            if m_rst:
                data["weekly_reset"] = m_rst.group(1).strip()

        elif line_lower.startswith("model:"):
            data["success"] = True
            data["model"] = line.split(":", 1)[1].strip()

        elif line_lower.startswith("plan:"):
            data["success"] = True
            data["plan"] = line.split(":", 1)[1].strip()

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
        title="[bold blue]󰚩 KIRO-CLI MONITOR[/bold blue]",
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
        title="[bold magenta]󰑣 AGY (ANTIGRAVITY) MONITOR[/bold magenta]",
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
# TMUX STATUS BAR INTEGRATION
# ============================================================================

def get_tmux_color(pct, green_safe=False):
    """
    Return tmux style tag based on usage percent.
    By default, safe usage (<60%) uses #[default] so it inherits the natural text
    color of the status bar. This guarantees 100% visibility on ANY background
    (especially default green tmux status bars where fg=green is completely invisible!).
    """
    if pct >= 85:
        return "#[fg=red,bold]"
    elif pct >= 60:
        return "#[fg=yellow,bold]"
    else:
        if green_safe:
            return "#[fg=green]"
        return "#[default]"


def format_dual_metric(short_pct, long_pct, green_safe=False, plain=False):
    """
    Format dual metric string: <short-term>%/<weekly-term>%.
    Each percentage is independently color-coded.
    """
    s_col = get_tmux_color(short_pct, green_safe)
    l_col = get_tmux_color(long_pct, green_safe)

    if plain:
        s_str = f"{short_pct:.0f}%" if short_pct is not None else "-"
        l_str = f"{long_pct:.0f}%" if long_pct is not None else "-"
        return f"{s_str}/{l_str}"
    else:
        s_str = f"{s_col}{short_pct:.0f}%#[default]" if short_pct is not None else "#[default]-#[default]"
        l_str = f"{l_col}{long_pct:.0f}%#[default]" if long_pct is not None else "#[default]-#[default]"
        return f"{s_str}#[default]/#[default]{l_str}"


def format_tmux_indicator(results, plain=False, green_safe=False, badge=False, kiro_dash=False):
    """
    Format a concise single-line indicator string for tmux status bar.
    Shows dual window quotas (session|5h / weekly) for Claude, AGY, and Codex.
    Example: ⚡ 󰚩 97% │ 󰘑 0%/13% │ 󰑣 0%/13% │ 󰘧 14%/42%
    """
    if not results:
        return ""

    parts = []

    # 1. Kiro (single total plan credits)
    if "kiro" in results and results["kiro"].get("success"):
        k = results["kiro"]
        pct = k.get("percentage", 0.0)
        color = get_tmux_color(pct, green_safe)
        icon = "K:" if plain else "󰚩"
        if kiro_dash:
            if plain:
                parts.append(f"{icon}{pct:.0f}%/-")
            else:
                parts.append(f"#[fg=blue]{icon}#[default] {color}{pct:.0f}%#[default]#[default]/-#[default]")
        else:
            if plain:
                parts.append(f"{icon}{pct:.0f}%")
            else:
                parts.append(f"#[fg=blue]{icon}#[default] {color}{pct:.0f}%#[default]")

    # 2. Claude (session / weekly)
    if "claude" in results and results["claude"].get("success"):
        c = results["claude"]
        s_pct = c.get("session_pct")
        w_pct = c.get("week_all_pct")
        icon = "C:" if plain else "󰘑"
        val = format_dual_metric(s_pct, w_pct, green_safe=green_safe, plain=plain)
        if plain:
            parts.append(f"{icon}{val}")
        else:
            parts.append(f"#[fg=yellow]{icon}#[default] {val}")

    # 3. AGY (5-hour / weekly)
    if "agy" in results and results["agy"].get("success"):
        a = results["agy"]
        items = a.get("items", [])
        s_pct = None
        w_pct = None
        for it in items:
            lbl = it.get("label", "").lower()
            if "five hour" in lbl or "5h" in lbl:
                if s_pct is None or "gemini" in lbl:
                    s_pct = it.get("used_pct")
            elif "weekly" in lbl or "week" in lbl:
                if w_pct is None or "gemini" in lbl:
                    w_pct = it.get("used_pct")
        icon = "A:" if plain else "󰑣"
        val = format_dual_metric(s_pct, w_pct, green_safe=green_safe, plain=plain)
        if plain:
            parts.append(f"{icon}{val}")
        else:
            parts.append(f"#[fg=magenta]{icon}#[default] {val}")

    # 4. Codex (5-hour / weekly)
    if "codex" in results and results["codex"].get("success"):
        cx = results["codex"]
        s_pct = cx.get("five_hour_pct")
        w_pct = cx.get("weekly_pct")
        icon = "X:" if plain else "󰘧"
        val = format_dual_metric(s_pct, w_pct, green_safe=green_safe, plain=plain)
        if plain:
            parts.append(f"{icon}{val}")
        else:
            parts.append(f"#[fg=cyan]{icon}#[default] {val}")

    if not parts:
        return ""

    if plain:
        return "⚡ " + " | ".join(parts)
    else:
        sep = " #[default]│#[default] "
        core = "#[bold,fg=cyan]⚡#[default] " + sep.join(parts)
        if badge:
            return f"#[bg=colour236,fg=colour255] {core} #[default]"
        return core


def setup_tmux_center(force_clean=True):
    """
    Configures tmux status-format[0] so that @ai_usage is rendered in the EXACT geometric center
    using align=absolute-centre. Robustly cleans up any previous or duplicated tags.
    """
    chk = subprocess.run(["tmux", "ls"], capture_output=True, text=True)
    if chk.returncode != 0:
        return False, "tmux server is not running"

    res = subprocess.run(["tmux", "show-options", "-gv", "status-format[0]"], capture_output=True, text=True)
    fmt = res.stdout.strip()
    if not fmt:
        res = subprocess.run(["tmux", "show-options", "-g", "status-format[0]"], capture_output=True, text=True)
        fmt = res.stdout.replace("status-format[0] ", "").strip().strip('"')

    tag = "#[nolist align=absolute-centre]#{?@ai_usage,#{@ai_usage}, }#[default]"

    # If format already has exactly one tag and no duplicates/old tags, do nothing
    if not force_clean and tag in fmt and fmt.count("@ai_usage") == 2:
        return True, "tmux center indicator is already properly configured"

    # Robustly strip all prior @ai_usage blocks (including old align=centre, badges, duplicates)
    cleaned_fmt = re.sub(r"#\[[^\]]*\]#\{\?@ai_usage,.*?\}#\[default\]\s*", "", fmt)
    cleaned_fmt = re.sub(r"#\{\?@ai_usage,[^}]*\}", "", cleaned_fmt)

    if "#[nolist align=right" in cleaned_fmt:
        new_fmt = cleaned_fmt.replace("#[nolist align=right", f"{tag}#[nolist align=right")
    elif "#[align=right" in cleaned_fmt:
        new_fmt = cleaned_fmt.replace("#[align=right", f"{tag}#[align=right")
    else:
        new_fmt = f"{cleaned_fmt} {tag}"

    set_res = subprocess.run(["tmux", "set-option", "-g", "status-format[0]", new_fmt], capture_output=True, text=True)
    subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
    if set_res.returncode == 0:
        return True, "Successfully configured absolute-center indicator in tmux status bar"
    else:
        return False, f"Failed to update tmux status-format: {set_res.stderr.strip()}"


def reset_tmux_format():
    """Completely restore tmux status-format[0] by stripping all @ai_usage tags and resetting."""
    chk = subprocess.run(["tmux", "ls"], capture_output=True, text=True)
    if chk.returncode != 0:
        console.print("[yellow]tmux server is not running.[/yellow]")
        return
    res = subprocess.run(["tmux", "show-options", "-gv", "status-format[0]"], capture_output=True, text=True)
    fmt = res.stdout.strip()
    cleaned = re.sub(r"#\[[^\]]*\]#\{\?@ai_usage,.*?\}#\[default\]\s*", "", fmt)
    cleaned = re.sub(r"#\{\?@ai_usage,[^}]*\}", "", cleaned)
    subprocess.run(["tmux", "set-option", "-g", "status-format[0]", cleaned], capture_output=True)
    subprocess.run(["tmux", "set-option", "-gu", "@ai_usage"], capture_output=True)
    subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
    console.print("[bold green]✔ Successfully reset tmux status-format and cleared indicator.[/bold green]")


def update_tmux_status(indicator, ensure_center=False):
    """Update the tmux global user option @ai_usage and refresh status line."""
    chk = subprocess.run(["tmux", "ls"], capture_output=True, text=True)
    if chk.returncode != 0:
        return False, "tmux server is not running"

    if ensure_center:
        setup_tmux_center(force_clean=False)

    set_res = subprocess.run(["tmux", "set-option", "-g", "@ai_usage", indicator], capture_output=True, text=True)
    if set_res.returncode != 0:
        return False, f"Failed to set tmux @ai_usage: {set_res.stderr.strip()}"

    subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
    return True, "Updated successfully"


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
            if force_all or parsed.get("success"):
                results[key] = parsed

    return results


def render_dashboard(results):
    """Dynamically assemble the dashboard for whichever tools are active."""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

    for key, cfg in TOOL_REGISTRY.items():
        if key in results:
            data = results[key]
            panel = cfg["renderer"](data)
            main_grid.add_row(panel)

    if "claude" in results:
        claude_data = results["claude"]
        if claude_data.get("h24_top") or claude_data.get("d7_top") or claude_data.get("h24_reqs") != "0":
            main_grid.add_row(render_claude_insights(claude_data))

    footer = Text(" Press Ctrl+C to exit | Run with --watch [sec] for continuous live monitoring", style="dim italic white")
    main_grid.add_row(footer)

    return main_grid


# ============================================================================
# DAEMON MANAGEMENT
# ============================================================================

def get_pid_file():
    pid_dir = os.path.expanduser("~/.cache/usage-dashboard")
    os.makedirs(pid_dir, exist_ok=True)
    return os.path.join(pid_dir, "tmux_daemon.pid"), os.path.join(pid_dir, "tmux_daemon.log")


def start_tmux_daemon(interval, args):
    pid_file, log_file = get_pid_file()

    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            console.print(f"[yellow]tmux background monitor is already running (PID: {old_pid}).[/yellow]")
            console.print("[dim]Use 'usage --tmux --stop' to terminate it.[/dim]")
            return
        except (ProcessLookupError, ValueError):
            pass

    cmd = [sys.executable, os.path.abspath(__file__), "--tmux", "--watch", str(interval)]
    if args.tools:
        cmd.extend(["--tools", args.tools])
    if args.all:
        cmd.append("--all")
    if args.plain:
        cmd.append("--plain")
    if args.green_safe:
        cmd.append("--green-safe")
    if args.badge:
        cmd.append("--badge")
    if getattr(args, "kiro_dash", False):
        cmd.append("--kiro-dash")

    with open(log_file, "a") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )

    with open(pid_file, "w") as f:
        f.write(str(proc.pid))

    console.print(f"[bold green]✔ tmux usage monitor started in background[/bold green] (PID: {proc.pid})")
    console.print(f"[dim]Interval: {interval}s | Log: {log_file}[/dim]")
    console.print("[dim]Run 'usage --tmux --stop' to terminate.[/dim]")


def stop_tmux_daemon():
    pid_file, _ = get_pid_file()
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            console.print(f"[bold green]✔ Stopped tmux monitor process (PID: {pid}).[/bold green]")
        except ProcessLookupError:
            console.print("[yellow]Cleaned up stale PID file (process was not active).[/yellow]")
        except Exception as e:
            console.print(f"[red]Error stopping process: {e}[/red]")
        finally:
            if os.path.exists(pid_file):
                os.remove(pid_file)
    else:
        console.print("[yellow]No PID file found. Cleaning up tmux status...[/yellow]")

    # Clear tmux variable and restore clean status format
    subprocess.run(["tmux", "set-option", "-gu", "@ai_usage"], capture_output=True)
    res = subprocess.run(["tmux", "show-options", "-gv", "status-format[0]"], capture_output=True, text=True)
    if res.stdout.strip():
        cleaned = re.sub(r"#\[[^\]]*\]#\{\?@ai_usage,.*?\}#\[default\]\s*", "", res.stdout.strip())
        cleaned = re.sub(r"#\{\?@ai_usage,[^}]*\}", "", cleaned)
        subprocess.run(["tmux", "set-option", "-g", "status-format[0]", cleaned], capture_output=True)
    subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
    console.print("[dim]tmux status bar indicator cleared.[/dim]")


def status_tmux_daemon():
    pid_file, log_file = get_pid_file()
    if not os.path.exists(pid_file):
        console.print("[yellow]tmux background monitor: NOT RUNNING[/yellow]")
        return
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        console.print(f"[bold green]tmux background monitor: ACTIVE (PID: {pid})[/bold green]")
        console.print(f"[dim]Log file: {log_file}[/dim]")
    except (ProcessLookupError, ValueError):
        console.print("[yellow]tmux background monitor: STOPPED (stale PID file)[/yellow]")


def run_tmux_watch_loop(interval, selected, force_all, plain, green_safe=False, badge=False, kiro_dash=False):
    """Continuously fetch usage and update tmux status bar."""
    setup_ok, msg = setup_tmux_center(force_clean=True)
    now_str = datetime.datetime.now().strftime("%H:%M:%S")
    sys.stdout.write(f"[{now_str}] Initialized tmux status absolute-center: {msg}\n")
    sys.stdout.flush()

    try:
        while True:
            results = fetch_usages(selected_tools=selected, force_all=force_all)
            indicator = format_tmux_indicator(results, plain=plain, green_safe=green_safe, badge=badge, kiro_dash=kiro_dash)
            now_str = datetime.datetime.now().strftime("%H:%M:%S")

            if indicator:
                # Do NOT call setup_tmux_center in loop; only update @ai_usage
                ok, up_msg = update_tmux_status(indicator, ensure_center=False)
                sys.stdout.write(f"[{now_str}] Updated tmux status bar: {indicator}\n")
            else:
                sys.stdout.write(f"[{now_str}] No active tool usage found\n")
            sys.stdout.flush()

            time.sleep(interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n[tmux monitor exited]\n")
        sys.stdout.flush()


# ============================================================================
# MAIN ENTRYPOINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Geek AI Usage Dashboard (Kiro, Claude, AGY, Codex)\n"
                    "Automatically detects installed CLIs and dynamically presents active quotas.\n"
                    "Supports tmux status bar absolute-center indicator and background watch mode."
    )
    parser.add_argument("-w", "--watch", type=int, nargs="?", const=60, default=None,
                        help="Watch mode: continuously refresh every N seconds (default: 60)")
    parser.add_argument("-t", "--tools", type=str, default=None,
                        help="Comma-separated subset of tools to monitor (e.g. -t claude,codex)")
    parser.add_argument("--all", action="store_true",
                        help="Force query all tools, even if their binary is not found in PATH")
    parser.add_argument("--raw", action="store_true",
                        help="Print raw outputs from all detected CLIs")

    # Tmux options
    tmux_group = parser.add_argument_group("tmux indicator options")
    tmux_group.add_argument("--tmux", action="store_true",
                            help="Output or update tmux status bar center indicator")
    tmux_group.add_argument("--center", action="store_true",
                            help="Configure tmux status bar to show the indicator in the center")
    tmux_group.add_argument("-d", "--daemon", action="store_true",
                            help="Run tmux monitor in background daemon mode")
    tmux_group.add_argument("--stop", action="store_true",
                            help="Stop running tmux background monitor")
    tmux_group.add_argument("--status", action="store_true",
                            help="Check tmux background monitor status")
    tmux_group.add_argument("--reset", action="store_true",
                            help="Reset tmux status-format to clean default (removes lingering indicators)")
    tmux_group.add_argument("--plain", action="store_true",
                            help="Use plain ASCII text (K:, C:, A:, X:) instead of Nerd Font icons")
    tmux_group.add_argument("--green-safe", action="store_true",
                            help="Force green color for <60%% usage (for dark status bars)")
    tmux_group.add_argument("--badge", action="store_true",
                            help="Wrap indicator in a styled dark pill badge for maximum contrast")
    tmux_group.add_argument("--kiro-dash", action="store_true",
                            help="Display Kiro quota with trailing dash (e.g. 97%%/-) for format symmetry")

    args = parser.parse_args()
    selected = [t.strip().lower() for t in args.tools.split(",")] if args.tools else None

    # Handle --reset
    if args.reset:
        reset_tmux_format()
        return

    # Handle tmux daemon stop / status
    if args.stop:
        stop_tmux_daemon()
        return

    if args.status:
        status_tmux_daemon()
        return

    # Handle raw output
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

    # Handle --center only
    if args.center and not args.tmux and args.watch is None and not args.daemon:
        ok, msg = setup_tmux_center(force_clean=True)
        if ok:
            console.print(f"[bold green]✔ {msg}[/bold green]")
        else:
            console.print(f"[yellow]{msg}[/yellow]")
        return

    # Handle tmux mode
    if args.tmux or args.daemon:
        interval = max(5, args.watch if args.watch is not None else 60)

        # Background daemon mode
        if args.daemon:
            start_tmux_daemon(interval, args)
            return

        # Continuous watch mode in foreground (or nohup/background via shell &)
        if args.watch is not None:
            run_tmux_watch_loop(interval, selected, args.all, args.plain, green_safe=args.green_safe, badge=args.badge, kiro_dash=args.kiro_dash)
            return

        # One-shot tmux output
        results = fetch_usages(selected_tools=selected, force_all=args.all)
        indicator = format_tmux_indicator(results, plain=args.plain, green_safe=args.green_safe, badge=args.badge, kiro_dash=args.kiro_dash)
        if indicator:
            update_tmux_status(indicator, ensure_center=args.center)
            print(indicator)
        else:
            if args.plain:
                print("⚡ [no active quotas]")
            else:
                print("#[bold,fg=cyan]⚡#[default] #[dim]no active quotas#[default]")
        return

    # Default TUI Dashboard (Snapshot or Watch)
    if args.watch is None:
        with console.status("[bold cyan]Discovering tools & querying neural gateways...[/bold cyan]", spinner="dots12"):
            results = fetch_usages(selected_tools=selected, force_all=args.all)
        console.print(render_dashboard(results))
    else:
        interval = max(5, args.watch)
        console.clear()
        try:
            with Live(console=console, screen=True, refresh_per_second=2) as live:
                while True:
                    live.update(Panel(Text("Querying active neural APIs in parallel...", style="bold cyan"), box=box.ROUNDED))
                    results = fetch_usages(selected_tools=selected, force_all=args.all)
                    live.update(render_dashboard(results))
                    time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard closed.[/dim]")


if __name__ == "__main__":
    main()
