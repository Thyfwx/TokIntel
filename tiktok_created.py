#!/usr/bin/env python3
"""
tiktok_created.py: when was a TikTok account created? (no API key needed)

TikTok's public profile page embeds the account's `createTime` (and a lot of
other profile data) in a JSON blob. We read that directly, so there is no API
key to set up. Works from a username, an @handle, or a profile URL.

Bonus: a TikTok *video* URL/ID decodes to its upload time via the Snowflake
timestamp baked into video IDs (id >> 32). (Note: many *user* IDs are NOT
snowflakes, so we trust the embedded createTime for accounts, not id>>32.)

Usage:
  ./venv/bin/python tiktok_created.py --input charlidamelio
  ./venv/bin/python tiktok_created.py --input https://www.tiktok.com/@nasa
  ./venv/bin/python tiktok_created.py --input https://www.tiktok.com/@x/video/7076288989640055298
  ./venv/bin/python tiktok_created.py --file usernames.txt

Part of TokIntel (https://github.com/HackUnderway/TokIntel) by Victor Bancayan /
Hack Underway. This account-lookup addition (no API key needed) was contributed
by @Thyfwx.
"""
import argparse
import json
import os
import re
import random
import string
import time
from datetime import datetime, UTC
from urllib.parse import quote

import requests

try:
    from colorama import Fore, init
    init(autoreset=True)
except Exception:                       # colorama is optional
    class _Nope:
        def __getattr__(self, _): return ""
    Fore = _Nope()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = 20
DELAY = 1.5  # be polite between requests

REHYDRATION_RE = re.compile(
    r'id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


# ------------------------------------------------------------------ helpers
def fmt(ts):
    try:
        return datetime.fromtimestamp(int(ts), UTC).strftime('%Y-%m-%d %H:%M:%S UTC')
    except Exception:
        return None


def classify(value):
    """Return ('video', video_id) | ('user', username) | ('id', number)."""
    v = value.strip()

    vid = re.search(r'/video/(\d+)', v)
    if vid:
        return "video", vid.group(1)

    handle = re.search(r'tiktok\.com/@([\w.\-]+)', v)
    if handle:
        return "user", handle.group(1)

    if v.lstrip('@').replace('.', '').replace('_', '').replace('-', '').isalnum() and not v.isdigit():
        return "user", v.lstrip('@')

    if v.isdigit():
        return "id", v

    return "user", v.lstrip('@')


def decode_snowflake(num):
    """id >> 32 -> upload/creation unix seconds (only valid for 64-bit snowflakes)."""
    n = int(num)
    if n < (1 << 40):     # too small to be a snowflake — would decode to ~1970
        return None
    return n >> 32


# ------------------------------------------------------------------ fetchers
def fetch_user(username, session):
    # quote() leaves valid username characters (letters, digits, _ . - ~) as-is
    # but percent-encodes anything unusual, so a crafted target can't alter the
    # request structure. The host and scheme are already a fixed literal prefix.
    url = f"https://www.tiktok.com/@{quote(username, safe='')}"
    r = session.get(url, timeout=TIMEOUT)
    m = REHYDRATION_RE.search(r.text)
    if not m:
        return {"error": f"no_data (http_{r.status_code}, {len(r.text)}B) — rate-limited/blocked or layout changed"}
    try:
        scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
    except ValueError:
        return {"error": "json_parse_failed"}

    detail = scope.get("webapp.user-detail")
    if not detail:
        return {"error": "user-detail missing — profile not returned (blocked, or no such page)"}

    status = detail.get("statusCode")
    info = detail.get("userInfo", {})
    user = info.get("user", {})
    # statsV2 carries correct string counts; the legacy `stats` object stores
    # 32-bit ints that overflow (negative likes) for accounts past ~2.1B, so
    # prefer statsV2 and fall back to stats only if it's absent.
    stats = info.get("statsV2") or info.get("stats") or {}
    if not user:
        return {"error": f"user not found (statusCode={status})"}

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    ct = user.get("createTime")
    uid = user.get("id")
    id_est = decode_snowflake(uid) if uid and str(uid).isdigit() else None

    return {
        "type": "account",
        "username": user.get("uniqueId") or username,
        "nickname": user.get("nickname"),
        "account_created": fmt(ct) if ct else None,
        "account_created_unix": ct,
        "created_estimate_from_id": fmt(id_est) if id_est else None,
        "user_id": uid,
        "sec_uid": user.get("secUid"),
        "verified": user.get("verified"),
        "private": user.get("privateAccount"),
        "bio": user.get("signature"),
        "region": user.get("region"),
        "followers": as_int(stats.get("followerCount")),
        "following": as_int(stats.get("followingCount")),
        "likes": as_int(stats.get("heartCount")),
        "videos": as_int(stats.get("videoCount")),
    }


def decode_video(video_id):
    ts = decode_snowflake(video_id)
    return {
        "type": "video",
        "video_id": video_id,
        "uploaded": fmt(ts) if ts else None,
        "note": "This is the VIDEO upload time, not the account creation date.",
    }


def decode_id(num):
    ts = decode_snowflake(num)
    return {
        "type": "raw_id",
        "id": num,
        "decoded": fmt(ts) if ts else None,
        "note": ("Decoded from snowflake id>>32. Works for video IDs and "
                 "snowflake user IDs; small/legacy user IDs can't be decoded "
                 "this way — fetch the @username instead for createTime."),
    }


# ------------------------------------------------------------------ reports
def reports_dir():
    if not os.path.exists("reports"):
        os.makedirs("reports")
    return "reports"


def save_reports(results, prefix):
    rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(reports_dir(), f"created_{prefix}_{stamp}_{rand}")

    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4, ensure_ascii=False)

    with open(base + ".txt", "w", encoding="utf-8") as f:
        f.write("TikTok account creation report\n" + "=" * 44 + "\n\n")
        for r in results:
            f.write(f"Target : {r['target']}\n")
            d = r["data"]
            if "error" in d:
                f.write(f"  ERROR: {d['error']}\n\n")
                continue
            if d.get("type") == "account":
                f.write(f"  Username       : @{d.get('username')}\n")
                f.write(f"  Account created: {d.get('account_created')}\n")
                f.write(f"  Nickname       : {d.get('nickname')}\n")
                f.write(f"  Verified       : {d.get('verified')}\n")
                f.write(f"  Private        : {d.get('private')}\n")
                f.write(f"  Followers      : {d.get('followers')}\n")
                f.write(f"  Likes          : {d.get('likes')}\n")
                f.write(f"  Videos         : {d.get('videos')}\n")
                f.write(f"  Bio            : {d.get('bio')}\n")
                f.write(f"  User ID        : {d.get('user_id')}\n")
            elif d.get("type") == "video":
                f.write(f"  Video uploaded : {d.get('uploaded')}\n")
                f.write(f"  (video id {d.get('video_id')})\n")
            else:
                f.write(f"  Decoded id     : {d.get('decoded')}\n")
            f.write("\n")
    return base + ".json", base + ".txt"


# ------------------------------------------------------------------ main
def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    return s


def lookup(target, session):
    """Resolve one target to its result dict. Never raises."""
    kind, val = classify(target)
    try:
        if kind == "video":
            return kind, decode_video(val)
        if kind == "id":
            return kind, decode_id(val)
        return kind, fetch_user(val, session)
    except Exception as e:
        return kind, {"error": f"{type(e).__name__}: {e}"}


def show(data, indent="    "):
    """Print a one-line human-readable result."""
    if "error" in data:
        print(Fore.RED + f"{indent}⚠️  {data['error']}")
    elif data.get("type") == "account":
        if data.get("account_created"):
            print(Fore.GREEN + f"{indent}📅 created: {data['account_created']}  "
                  + Fore.WHITE + f"(@{data['username']}, {data.get('followers')} followers)")
        else:
            print(Fore.YELLOW + f"{indent}🟡 profile found but no createTime; "
                  f"id-estimate: {data.get('created_estimate_from_id')}")
    elif data.get("type") == "video":
        print(Fore.GREEN + f"{indent}📅 uploaded: {data['uploaded']}")
    else:
        print(Fore.GREEN + f"{indent}📅 decoded: {data['decoded']}")


def run_batch(targets, session):
    print(Fore.CYAN + f"\n[+] Targets: {len(targets)}  (free mode — no API key)\n")
    results = []
    for i, target in enumerate(targets, 1):
        print(Fore.WHITE + f"[{i}/{len(targets)}] {target}")
        kind, data = lookup(target, session)
        show(data)
        results.append({"target": target, "data": data})
        if kind == "user" and i < len(targets):
            time.sleep(DELAY)
    return results


def run_interactive(session):
    print(Fore.CYAN + "\n🔎 TikTok creation-date lookup  (free — no API key)")
    print(Fore.WHITE + "   Type a username, @handle, or profile/video URL.")
    print(Fore.WHITE + "   Press Enter on an empty line (or type 'q') to quit.\n")
    results = []
    while True:
        try:
            entry = input("🔎 username> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not entry or entry.lower() in {"q", "quit", "exit"}:
            break
        _, data = lookup(entry, session)
        show(data, indent="   ")
        results.append({"target": entry, "data": data})
    return results


def main():
    p = argparse.ArgumentParser(
        description="Get a TikTok account's creation date — free, no API key. "
                    "Run with no arguments for interactive mode.")
    p.add_argument("targets", nargs="*",
                   help="one or more usernames / @handles / profile or video URLs")
    p.add_argument("--input", help="a single target (same as a positional arg)")
    p.add_argument("--file", help="text file with one target per line")
    args = p.parse_args()

    # Command-line targets: zsh (unlike bash) does NOT strip inline '# comments',
    # so a '#' token arrives as a literal arg. Treat it as start-of-comment and
    # ignore it plus everything after — a pasted "user  # note" stays clean.
    cli = []
    for t in [*args.targets, *( [args.input] if args.input else [] )]:
        if t.strip().startswith("#"):
            break
        cli.append(t)

    # File targets: skip blank lines and whole-line '#' comments.
    file_lines = []
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            file_lines = [ln.strip() for ln in f
                          if ln.strip() and not ln.strip().startswith("#")]

    # de-dupe in order, drop blanks
    seen, targets = set(), []
    for t in [*cli, *file_lines]:
        t = t.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        targets.append(t)

    session = new_session()

    if targets:
        results = run_batch(targets, session)
        prefix = "single" if len(targets) == 1 else "batch"
    else:
        results = run_interactive(session)
        prefix = "interactive"

    if results:
        jp, tp = save_reports(results, prefix)
        print(Fore.CYAN + f"\n📁 Reports:\n   {jp}\n   {tp}\n")


if __name__ == "__main__":
    main()
