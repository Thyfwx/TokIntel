#!/usr/bin/env python3
"""
tiktok_ui.py — a pretty terminal UI for TikTok account creation-date lookups.

Wraps the free, no-API logic from tiktok_created.py in a Rich interface:
type a username (or @handle / profile URL / video URL) and get a clean card
showing when the account was created, plus profile stats. No API key or signup.

Launch it with ./start.sh  (or the `tokintel` alias).

Part of TokIntel (https://github.com/HackUnderway/TokIntel) by Victor Bancayan /
Hack Underway. This lookup UI (no API key needed) was contributed by @Thyfwx.
"""
import os
import sys

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt
from rich import box

# Reuse the validated lookup engine living next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tiktok_created import lookup, new_session, save_reports  # noqa: E402

console = Console()

TIKTOK_CYAN = "#25F4EE"
TIKTOK_RED = "#FE2C55"
TT_LOGO = [
    "████████╗██╗██╗  ██╗████████╗ ██████╗ ██╗  ██╗",
    "╚══██╔══╝██║██║ ██╔╝╚══██╔══╝██╔═══██╗██║ ██╔╝",
    "   ██║   ██║█████╔╝    ██║   ██║   ██║█████╔╝ ",
    "   ██║   ██║██╔═██╗    ██║   ██║   ██║██╔═██╗ ",
    "   ██║   ██║██║  ██╗   ██║   ╚██████╔╝██║  ██╗",
    "   ╚═╝   ╚═╝╚═╝  ╚═╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝",
]


def num(x):
    return f"{x:,}" if isinstance(x, int) else ("—" if x in (None, "") else str(x))


def header():
    # TikTok-style wordmark: cyan on the left, white core, red on the right.
    w = max(len(line) for line in TT_LOGO)
    a, b = w // 3, 2 * w // 3
    logo = Text(justify="center")
    for line in TT_LOGO:
        line = line.ljust(w)
        logo.append(line[:a], style=f"bold {TIKTOK_CYAN}")
        logo.append(line[a:b], style="bold white")
        logo.append(line[b:] + "\n", style=f"bold {TIKTOK_RED}")

    tag = Text(justify="center")
    tag.append("♪ ", style=TIKTOK_CYAN)
    tag.append("Account Lookup", style="bold white")
    tag.append(" ♪", style=TIKTOK_RED)
    sub = Text("when was this account created?  ·  no API key needed",
               style="dim italic", justify="center")
    credit = Text("part of TokIntel · by Hack Underway", style="dim", justify="center")

    body = Group(logo, tag, Text(""), sub, credit)
    console.print(Panel(body, box=box.DOUBLE, border_style=TIKTOK_CYAN, padding=(1, 3)))


def render_account(d):
    created = d.get("account_created") or "unknown"
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="right", style="cyan", no_wrap=True)
    tbl.add_column(style="white")
    if d.get("nickname"):
        tbl.add_row("Name", str(d["nickname"]))
    tbl.add_row("Verified", "✅ yes" if d.get("verified") else "no")
    tbl.add_row("Private", "🔒 yes" if d.get("private") else "no")
    tbl.add_row("Followers", num(d.get("followers")))
    tbl.add_row("Following", num(d.get("following")))
    tbl.add_row("Likes", num(d.get("likes")))
    tbl.add_row("Videos", num(d.get("videos")))
    if d.get("region"):
        tbl.add_row("Region", str(d["region"]))
    if d.get("bio"):
        tbl.add_row("Bio", str(d["bio"]))
    tbl.add_row("User ID", str(d.get("user_id")))

    body = Group(
        Align.center(Text(f"📅  {created}", style="bold green")),
        Align.center(Text("account created", style="dim")),
        Text(""),
        tbl,
    )
    console.print(Panel(body, title=f"[bold magenta]@{d.get('username')}[/]",
                        border_style="green", box=box.ROUNDED, padding=(1, 2)))


def render_simple(title, line, note=None, color="green"):
    body = [Align.center(Text(line, style=f"bold {color}"))]
    if note:
        body.append(Align.center(Text(note, style="dim")))
    console.print(Panel(Group(*body), title=title, border_style=color,
                        box=box.ROUNDED, padding=(1, 2)))


def render(data):
    if "error" in data:
        render_simple("not found", data["error"], color="red")
    elif data.get("type") == "account":
        render_account(data)
    elif data.get("type") == "video":
        render_simple("video", f"📅  uploaded {data.get('uploaded')}",
                      note="video upload time, not account creation")
    else:
        render_simple("id decode", f"📅  {data.get('decoded')}", note=data.get("note"))


def main():
    console.clear()
    header()
    console.print(
        "  Type a [bold cyan]username[/], @handle, or profile/video URL.  "
        "Empty line or [bold]q[/] to quit.\n")

    session = new_session()
    results = []
    while True:
        try:
            entry = Prompt.ask("[bold magenta]🔎 lookup[/]", default="", show_default=False).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not entry or entry.lower() in {"q", "quit", "exit"}:
            break
        with console.status(f"[cyan]Fetching {entry}…[/]", spinner="dots"):
            _, data = lookup(entry, session)
        render(data)
        results.append({"target": entry, "data": data})

    if results:
        _, tp = save_reports(results, "ui")
        console.print(f"\n[dim]Looked up {len(results)} · report saved →[/] {tp}")
    console.print("\n[magenta]bye 👁[/]\n")


if __name__ == "__main__":
    main()
