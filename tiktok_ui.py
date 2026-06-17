#!/usr/bin/env python3
"""
tiktok_ui.py: a pretty terminal UI for TikTok account creation-date lookups.

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
from rich import box

# Reuse the validated lookup engine living next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tiktok_created import (  # noqa: E402
    lookup, new_session, save_reports, osint_pivots, integrity_flags,
    probe_pivots, human_age, grouped_pivots, limited_lines,
    use_browser_session, _session_configured,
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
    tbl.add_row("Verified", "[green]yes[/]" if d.get("verified") else "no")
    tbl.add_row("Private", "[yellow]yes[/]" if d.get("private") else "no")
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
        # A handle TikTok recognizes but won't serve isn't "not found", and we
        # can't prove it's gone, so it gets a softer amber "couldn't load" rather
        # than a red "not found" verdict.
        if data.get("state") == "unavailable":
            render_simple("couldn't load", data["error"], color="yellow")
        else:
            render_simple("not found", data["error"], color="red")
    elif data.get("type") == "account":
        render_account(data)
    elif data.get("type") == "limited":
        # username / nickname inside `who` are already control-byte-stripped at the
        # source (fetch_user runs them through _clean), and render_simple prints via
        # Text(), which never interprets Rich markup, so this can't inject styling.
        who, hint = limited_lines(data)
        render_simple("found · audience controls", f"{who} is a real account.",
                      note=hint, color="cyan")
    elif data.get("type") == "video":
        render_simple("video", f"📅  uploaded {data.get('uploaded')}",
                      note="video upload time, not account creation")
    else:
        render_simple("id decode", f"📅  {data.get('decoded')}", note=data.get("note"))


def render_pivots(data):
    pivots = osint_pivots(data)
    if not pivots:
        console.print("[dim]   no pivots available for this result[/]")
        return

    with console.status("[cyan]checking…[/]", spinner="dots"):
        own, same_name, found_handle = grouped_pivots(probe_pivots(pivots), data.get("bio_link"))

    def group_table(items):
        tbl = Table.grid(padding=(0, 2))
        tbl.add_column(justify="right", style=TIKTOK_CYAN, no_wrap=True)   # label
        tbl.add_column(overflow="fold")                                   # full URL
        for i, (label, url, _) in enumerate(items):
            if i:
                tbl.add_row("", "")   # a little breathing room between links
            # A plain full URL to copy or open; never made clickable.
            tbl.add_row(label, Text(str(url).translate(_CTRL_BYTES)))
        return tbl

    # The profile's own links and any account confirmed through its bio go first,
    # then the same handle found on other sites. Misses are left out entirely.
    blocks = []
    if own:
        blocks += [Text("From this profile", style="dim"), group_table(own)]
    if same_name:
        if blocks:
            blocks.append(Text(""))
        blocks += [Text("Same handle on other sites", style="dim"), group_table(same_name)]
    elif not found_handle:
        if blocks:
            blocks.append(Text(""))
        blocks += [Text("Same handle on other sites", style="dim"),
                   Text("  Nothing found with this handle", style="yellow")]

    console.print(Panel(Group(*blocks), title="[bold]🧭 OSINT pivots[/]",
                        subtitle="[dim]links shown in full · copy to open[/]",
                        subtitle_align="left",
                        border_style=TIKTOK_CYAN, box=box.ROUNDED, padding=(1, 2)))


def render_flags(data):
    flags = integrity_flags(data)
    if not flags:
        return
    color = {"warn": "yellow", "info": "cyan", "ok": "green"}
    body = Text()
    for i, (sev, msg) in enumerate(flags):
        if i:
            body.append("\n")
        body.append(msg, style=color.get(sev, "white"))
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


_BROWSERS = ("chrome", "firefox", "edge", "brave", "safari", "chromium", "opera", "vivaldi")


def _installed_browsers():
    """The browsers we support that actually look installed on this machine, so
    the unlock prompt only offers real choices. Best-effort, cross-platform."""
    import shutil
    home = os.path.expanduser("~")
    found = []
    if sys.platform == "darwin":
        apps = {"chrome": "Google Chrome.app", "firefox": "Firefox.app",
                "edge": "Microsoft Edge.app", "brave": "Brave Browser.app",
                "opera": "Opera.app", "vivaldi": "Vivaldi.app", "chromium": "Chromium.app"}
        for name, app in apps.items():
            if os.path.exists(f"/Applications/{app}") or os.path.exists(f"{home}/Applications/{app}"):
                found.append(name)
        if os.path.exists("/Applications/Safari.app"):
            found.append("safari")
    elif sys.platform.startswith("win"):
        bases = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")]
        rels = {"chrome": r"Google\Chrome\Application\chrome.exe",
                "edge": r"Microsoft\Edge\Application\msedge.exe",
                "brave": r"BraveSoftware\Brave-Browser\Application\brave.exe",
                "firefox": r"Mozilla Firefox\firefox.exe",
                "opera": r"Opera\launcher.exe", "vivaldi": r"Vivaldi\Application\vivaldi.exe"}
        for name, rel in rels.items():
            if any(b and os.path.exists(os.path.join(b, rel)) for b in bases):
                found.append(name)
    else:
        bins = {"chrome": ("google-chrome", "google-chrome-stable", "chrome"),
                "chromium": ("chromium", "chromium-browser"), "firefox": ("firefox",),
                "brave": ("brave-browser", "brave"),
                "edge": ("microsoft-edge", "microsoft-edge-stable"),
                "opera": ("opera",), "vivaldi": ("vivaldi", "vivaldi-stable")}
        for name, names in bins.items():
            if any(shutil.which(b) for b in names):
                found.append(name)
    return found


def offer_unlock(entry, data, session):
    """When a locked (audience-controls) account comes back and no login is set
    yet, offer to read it with the user's own browser login right here, no env
    var or README needed. Returns (data, session): re-fetched and unlocked if it
    worked, otherwise unchanged. The cookie is read locally and never shown."""
    if not (isinstance(data, dict) and data.get("type") == "limited"):
        return data, session
    if _session_configured():
        return data, session
    browsers = _installed_browsers() or list(_BROWSERS)
    console.print(
        "\n  [dim]This one is locked. If you are signed into TikTok in a browser on this\n"
        "  computer, the tool can read it with that login. The login is used only on your\n"
        "  machine, only for this lookup, and is never saved, sent anywhere, or shared. Your\n"
        "  computer may ask you to approve reading your browser cookies, that is normal, click Allow.[/]")
    while True:
        try:
            pick = Prompt.ask(
                f"  [dim]read it with[/] [{TIKTOK_CYAN}]{' / '.join(browsers)}[/]"
                " [dim](or Enter to skip)[/]", default="", show_default=False).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return data, session
        if pick not in browsers:
            return data, session
        with console.status(f"[cyan]reading your {pick} login…[/]", spinner="dots"):
            ok = use_browser_session(pick)
        if ok:
            break
        console.print(
            f"  [yellow]No TikTok login found in {pick}. Open TikTok in {pick} and sign in,\n"
            f"  then type {pick} again to try once more[/] [dim](or Enter to skip).[/]")
    session = new_session()
    with console.status(f"[cyan]reading {entry} with your login…[/]", spinner="dots"):
        _, data = lookup(entry, session)
    render(data)
    return data, session


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
        # anywhere: the lookup prompt, the fetch, or the extras menu, not just
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
            data, session = offer_unlock(entry, data, session)
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
        console.print("\n[yellow]Nothing worth saving. Every lookup errored. No report written.[/]")
    console.print("\n[dim]bye[/]\n")


if __name__ == "__main__":
    main()
