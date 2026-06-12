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

try:
    import readline  # noqa: F401  enables ← → line editing + ↑ history in prompts
except ImportError:
    readline = None  # Windows without pyreadline; basic input still works

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt
from rich.markup import escape
from rich.style import Style
from rich import box

# Reuse the validated lookup engine living next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tiktok_created import (  # noqa: E402
    lookup, new_session, save_reports, osint_pivots, integrity_flags,
    probe_pivots, human_age, _pivot_section,
)

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


# The looked-up account is the potentially hostile party in an OSINT tool, so
# its profile text is untrusted. Rich interprets [..] markup in plain strings,
# which would let a crafted nickname / bio / link inject styling, a spoofed
# clickable [link=...], or terminal escapes (e.g. an OSC 52 clipboard write).
# These mirror the CLI's _osc8 / _CTRL_BYTES defense for the Rich UI.
_CTRL_BYTES = {**{i: None for i in range(0x20)}, 0x7f: None}


def _safe(value):
    """Untrusted profile text rendered literally: strip control bytes, then
    escape Rich markup so brackets show as text instead of being interpreted."""
    return escape(str(value).translate(_CTRL_BYTES))


def _safe_link(url, text=None):
    """A clickable cell built as a styled Text (not markup), so a hostile bio
    link or avatar URL can't break out of [link=...] or smuggle terminal
    escapes. Visible text defaults to the URL; pass `text` for a clean label.
    The link target is set via a Style object, never string-parsed."""
    clean_url = str(url).translate(_CTRL_BYTES)
    label = str(text).translate(_CTRL_BYTES) if text is not None else clean_url
    return Text(label, style=Style(link=clean_url))


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
    credit = Text("part of TokIntel · by Hack Underway · contributed by @Thyfwx",
                  style="dim", justify="center")

    body = Group(logo, tag, Text(""), sub, credit)
    console.print(Panel(body, box=box.DOUBLE, border_style=TIKTOK_CYAN, padding=(1, 3)))


def render_account(d):
    created = d.get("account_created") or "unknown"
    age = human_age(d.get("account_created_unix"))
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="right", style="cyan", no_wrap=True)
    tbl.add_column(style="white")
    if d.get("nickname"):
        tbl.add_row("Name", _safe(d["nickname"]))
    tbl.add_row("Verified", "✅ yes" if d.get("verified") else "no")
    tbl.add_row("Private", "🔒 yes" if d.get("private") else "no")
    tbl.add_row("Followers", num(d.get("followers")))
    tbl.add_row("Following", num(d.get("following")))
    tbl.add_row("Likes", num(d.get("likes")))
    tbl.add_row("Videos", num(d.get("videos")))
    if d.get("region"):
        tbl.add_row("Region", _safe(d["region"]))
    if d.get("bio"):
        tbl.add_row("Bio", _safe(d["bio"]))
    tbl.add_row("User ID", str(d.get("user_id")))

    body = Group(
        Align.center(Text(f"📅  {created}", style="bold green")),
        Align.center(Text(f"account created · {age}" if age else "account created", style="dim")),
        Text(""),
        tbl,
    )
    console.print(Panel(body, title=f"[bold magenta]@{_safe(d.get('username'))}[/]",
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


_STATUS_BADGE = {
    "exists":  "[green]✓[/]",
    "missing": "[red]✗[/]",
    "unknown": "[yellow]?[/]",
    None:      " ",
}


def render_pivots(data):
    pivots = osint_pivots(data)
    if not pivots:
        console.print("[dim]   no pivots available for this result[/]")
        return

    # HEAD-check the cross-platform probes in parallel; total wait ≈ 1s.
    with console.status("[cyan]checking platforms…[/]", spinner="dots"):
        probed = probe_pivots(pivots)

    # Group the pivots under plain headings, and show each as a full, visible
    # URL. The URL itself is the clickable hyperlink in any terminal that
    # supports OSC 8 links, and because it shows in full it also stays readable,
    # copyable, and auto-detectable as a link in terminals that don't.
    by_section, order = {}, []
    for label, url, status in probed:
        sect = _pivot_section(label)
        if sect not in by_section:
            by_section[sect] = []
            order.append(sect)
        by_section[sect].append((label, url, status))

    blocks = []
    for sect in order:
        tbl = Table.grid(padding=(0, 2))
        tbl.add_column(justify="center", no_wrap=True, width=2)            # ✓ / ✗
        tbl.add_column(justify="right", style=TIKTOK_CYAN, no_wrap=True)   # label
        tbl.add_column(style="white", overflow="fold")                    # visible URL
        for label, url, status in by_section[sect]:
            tbl.add_row(_STATUS_BADGE[status], label, _safe_link(url))
        blocks += [Text(sect, style="dim"), tbl, Text("")]

    console.print(Panel(Group(*blocks), title="[bold]🧭 OSINT pivots[/]",
                        subtitle="[dim]click any link to open it · ✓ = YouTube confirmed[/]",
                        subtitle_align="left",
                        border_style=TIKTOK_CYAN, box=box.ROUNDED, padding=(1, 2)))


def render_flags(data):
    flags = integrity_flags(data)
    if not flags:
        return
    style = {"warn": ("⚠️ ", "yellow"), "info": ("ℹ️ ", "cyan"), "ok": ("✅", "green")}
    body = Text()
    for i, (sev, msg) in enumerate(flags):
        icon, color = style.get(sev, ("·", "white"))
        if i:
            body.append("\n")
        body.append(f"{icon} ", style=color)
        body.append(msg)
    border = "yellow" if any(s == "warn" for s, _ in flags) else (
             "cyan" if any(s == "info" for s, _ in flags) else "green")
    console.print(Panel(body, title="[bold]🚩 Integrity flags[/]",
                        border_style=border, box=box.ROUNDED, padding=(1, 2)))


def extras_menu(data):
    """After an account card, offer a small numbered menu of extras. Returns
    True if the user asked to quit the whole program (q / quit / exit, or
    Ctrl-C / Ctrl-D), so the caller can break out. Anything else just skips back
    to the lookup prompt.

    Note: no strict `choices=` here on purpose. Rich's choice validation keeps
    re-asking on any input it doesn't recognise, which means 'q' could never get
    the user out of this menu. That was the trap."""
    if not isinstance(data, dict) or data.get("type") != "account":
        return False
    console.print(
        "  [bold]What else?[/]  "
        f"[{TIKTOK_CYAN}]1[/] pivot links  ·  "
        f"[{TIKTOK_CYAN}]2[/] integrity flags  ·  "
        f"[{TIKTOK_CYAN}]3[/] both  ·  "
        "[dim]Enter to skip · q to quit[/]"
    )
    try:
        choice = Prompt.ask("[dim]  choose[/]", default="", show_default=False).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return True
    if choice in {"q", "quit", "exit"}:
        return True
    if choice == "1":
        render_pivots(data)
    elif choice == "2":
        render_flags(data)
    elif choice == "3":
        render_flags(data)
        render_pivots(data)
    return False


def main():
    console.clear()
    header()
    console.print(
        "  Type a [bold cyan]username[/], @handle, or profile/video URL.\n"
        "  [dim]q or Ctrl-C to quit[/]\n")

    session = new_session()
    results = []
    while True:
        # One try/except around the whole turn, so Ctrl-C or Ctrl-D quits from
        # anywhere — the lookup prompt, the fetch, or the extras menu — not just
        # from one exact spot.
        try:
            # Plain prompt (no color codes, no emoji) so readline's cursor math
            # is exact and ← → editing stays smooth. The styled Rich prompt threw
            # the cursor off because the emoji is double-width and the ANSI codes
            # are invisible bytes readline still counted.
            entry = input("  lookup ▸ ").strip()
            if not entry or entry.lower() in {"q", "quit", "exit"}:
                break
            with console.status(f"[cyan]Fetching {entry}…[/]", spinner="dots"):
                _, data = lookup(entry, session)
            render(data)
            if extras_menu(data):        # True means the user asked to quit
                break
            results.append({"target": entry, "data": data})
        except (EOFError, KeyboardInterrupt):
            break

    successes = [r for r in results if isinstance(r.get("data"), dict) and "error" not in r["data"]]
    if successes:
        _, tp = save_reports(results, "ui")
        console.print(f"\n[dim]Looked up {len(results)} · report saved →[/] {tp}")
    elif results:
        console.print("\n[yellow]Nothing worth saving — every lookup errored. No report written.[/]")
    console.print("\n[dim]bye[/]\n")


if __name__ == "__main__":
    main()
