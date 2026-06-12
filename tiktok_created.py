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
  python3 tiktok_created.py charlidamelio
  python3 tiktok_created.py @nasa https://www.tiktok.com/@zachking
  python3 tiktok_created.py charlidamelio --all      # add OSINT pivots + flags
  python3 tiktok_created.py --file usernames.txt

Part of TokIntel (https://github.com/HackUnderway/TokIntel) by Victor Bancayan /
Hack Underway. This account-lookup addition (no API key needed) was contributed
by @Thyfwx.
"""
import argparse
import concurrent.futures
import json
import os
import re
import random
import string
import time
try:
    import readline  # noqa: F401  enables ← → line editing + ↑ history in the prompt
except ImportError:
    readline = None  # Windows without pyreadline; basic input still works
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


def human_age(created_unix):
    """Short 'how long ago' label for a unix timestamp: 'today', '5 days ago',
    '3 months ago', '7 years ago'. Returns None if the input isn't usable, so
    callers can simply skip it. Months/years are approximate (30/365 days),
    which is exactly how people read an 'account age' at a glance."""
    if not isinstance(created_unix, (int, float)) or created_unix <= 0:
        return None
    try:
        days = (datetime.now(UTC) - datetime.fromtimestamp(int(created_unix), UTC)).days
    except (ValueError, OverflowError, OSError):
        return None
    if days < 0:
        return None
    if days == 0:
        return "today"
    if days < 31:
        n, unit = days, "day"
    elif days < 365:
        # Cap months at 11 so a 360-364 day account reads "11 months", never
        # "12 months ago" one day before it rolls over to "1 year ago".
        n, unit = min(days // 30, 11), "month"
    else:
        n, unit = days // 365, "year"
    return f"{n} {unit}{'s' if n != 1 else ''} ago"


# ASCII control bytes (0x00-0x1f and 0x7f). Any untrusted profile text
# (nickname, bio, region, the resolved username) is stripped of these before it
# is printed, written to a report, or wrapped in a terminal escape, so a hostile
# account can't smuggle OSC/CSI sequences (e.g. an OSC 52 clipboard write) into
# the investigator's terminal or saved report. The Rich UI escapes markup on top
# of this; the CLI and the report writer rely on it directly.
_CTRL_BYTES = {**{i: None for i in range(0x20)}, 0x7f: None}


def _clean(value):
    """Strip control bytes from an untrusted string field (passthrough None / non-str)."""
    return value.translate(_CTRL_BYTES) if isinstance(value, str) else value


def _count(x):
    """Comma-group an integer count for display; '—' for missing, str otherwise."""
    return f"{x:,}" if isinstance(x, int) else ("—" if x in (None, "") else str(x))


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
        # A bare number is ambiguous: a video/snowflake ID, or an all-digit
        # username (TikTok allows those). If it's too small to be a real video
        # snowflake, treat it as a username so all-digit handles stay reachable.
        return ("id", v) if decode_snowflake(v) is not None else ("user", v)

    return "user", v.lstrip('@')


# TikTok *video* IDs are snowflakes whose high bits hold the post time in unix
# seconds, so `id >> 32` recovers it. Many *user* IDs are NOT snowflakes: old
# accounts use small sequential ids (e.g. 107955), and some newer ids decode to
# a nonsense ~1970 date. So we only trust a decode that lands in a believable
# window — the musical.ly/TikTok era through today. Anything outside that we
# report as unknown rather than printing a confidently wrong date.
_SNOWFLAKE_MIN_TS = 1_388_534_400   # 2014-01-01 UTC — musical.ly-era floor


def decode_snowflake(num):
    """id >> 32 -> unix seconds, but only when the result is a believable date.
    Returns None for non-snowflake ids (which would otherwise decode to ~1970)."""
    try:
        n = int(num)
    except (TypeError, ValueError):
        return None
    ts = n >> 32
    if _SNOWFLAKE_MIN_TS <= ts <= datetime.now(UTC).timestamp() + 86_400:
        return ts
    return None


# ------------------------------------------------------------------ fetchers
# TikTok usernames are letters / digits / underscore / period, up to 24 chars.
# Anything else means the user pasted a sentence, a wrong URL, or random text;
# refuse early with a clear hint instead of wasting a request and returning
# the cryptic "user not found" code from TikTok.
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,24}$")


def fetch_user(username, session):
    if not USERNAME_RE.match(username):
        return {"error": "That doesn't look like a TikTok username. "
                         "Try just the @handle (e.g. 'nasa') or a "
                         "tiktok.com profile URL."}

    # quote() leaves valid username characters (letters, digits, _ . - ~) as-is
    # but percent-encodes anything unusual, so a crafted target can't alter the
    # request structure. The host and scheme are already a fixed literal prefix.
    url = f"https://www.tiktok.com/@{quote(username, safe='')}"

    # One brief retry on the "TikTok served a stub" case — usually a soft throttle
    # that clears in a couple of seconds. Avoids the user seeing a scary technical
    # error for what is really just "wait a moment."
    m = None
    for attempt in (1, 2):
        try:
            r = session.get(url, timeout=TIMEOUT)
        except requests.RequestException:
            # No internet, DNS failure, connection reset, TLS error, etc. Retry
            # once for a transient blip, then fail with a plain-English message
            # rather than letting a raw HTTPSConnectionPool traceback string
            # reach the user — every other error path here is already friendly.
            if attempt == 1:
                time.sleep(2.5)
                continue
            return {"error": "Couldn't reach TikTok — check your internet connection, then try again."}
        m = REHYDRATION_RE.search(r.text)
        if m:
            break
        if attempt == 1:
            time.sleep(2.5)
    if not m:
        return {"error": "TikTok throttled this lookup — wait a moment, then try again."}
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
        # TikTok's statusCode 10221 specifically means "no such user". Other
        # codes are rare; keep the number for those cases but lead with English.
        if status == 10221:
            return {"error": "TikTok has no account with that username — check the spelling, or the account may have been deleted."}
        return {"error": f"TikTok did not return a profile (status code {status})."}

    def as_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v

    ct = user.get("createTime")
    uid = user.get("id")
    id_est = decode_snowflake(uid) if uid and str(uid).isdigit() else None

    # Extra fields that power OSINT pivots and integrity heuristics.
    raw_bio_link = user.get("bioLink")
    bio_link = raw_bio_link.get("link") if isinstance(raw_bio_link, dict) else (
        raw_bio_link if isinstance(raw_bio_link, str) else None)
    # TikTok stores some bio links without a scheme (e.g. "linktr.ee/tiktok"),
    # which then isn't clickable. Add https:// only when there's truly no scheme,
    # so we don't mangle "mailto:"/"tel:" or protocol-relative "//host".
    if bio_link:
        if bio_link.startswith("//"):
            bio_link = "https:" + bio_link
        elif not re.match(r"(?i)^[a-z][a-z0-9+.\-]*:", bio_link):
            bio_link = "https://" + bio_link

    return {
        "type": "account",
        "username": _clean(user.get("uniqueId") or username),
        "nickname": _clean(user.get("nickname")),
        "account_created": fmt(ct) if ct else None,
        "account_created_unix": ct,
        "created_estimate_from_id": fmt(id_est) if id_est else None,
        "user_id": uid,
        "sec_uid": user.get("secUid"),
        "verified": user.get("verified"),
        "private": user.get("privateAccount"),
        "bio": _clean(user.get("signature")),
        "bio_link": bio_link,
        "region": _clean(user.get("region")),
        "avatar": user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb"),
        "unique_id_modify_time": user.get("uniqueIdModifyTime"),
        "nick_name_modify_time": user.get("nickNameModifyTime"),
        "is_organization": user.get("isOrganization"),
        "tt_seller": user.get("ttSeller"),
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
        "note": ("This is when the video was posted. TikTok bakes the upload "
                 "time into the video ID itself, so no fetch was needed. For "
                 "the account's creation date, type the @username."),
    }


def decode_id(num):
    ts = decode_snowflake(num)
    return {
        "type": "raw_id",
        "id": num,
        "decoded": fmt(ts) if ts else None,
        "note": ("Read straight from the ID number — TikTok bakes the post "
                 "time into video IDs. For an account's creation date, type "
                 "its @username instead (user IDs are too small to hold a "
                 "timestamp)."),
    }


# ----------------------------------------------------------- OSINT extras
def osint_pivots(data):
    """Return an ordered list of (label, url) pivots for an account result.
    URLs only, no fetches happen here, the user clicks them. The order groups
    them: the same handle on other platforms first, then a reverse image search
    of the avatar (the "who is this really?" / impostor check), then the
    profile's own bio link."""
    pivots = []
    if not isinstance(data, dict) or data.get("type") != "account":
        return pivots

    username = data.get("username")
    avatar = data.get("avatar")
    bio_link = data.get("bio_link")

    if username:
        pivots.append(("Instagram", f"https://www.instagram.com/{username}/"))
        pivots.append(("X / Twitter", f"https://x.com/{username}"))
        pivots.append(("YouTube", f"https://www.youtube.com/@{username}"))
        pivots.append(("Twitch", f"https://www.twitch.tv/{username}"))
        pivots.append(("Reddit", f"https://www.reddit.com/user/{username}"))
        pivots.append(("Wayback", f"https://web.archive.org/web/*/tiktok.com/@{quote(username, safe='')}"))

    if avatar:
        # One reverse-image link is enough for the "who is this?" check, and
        # Google Lens is the strongest (it tends to name the actual person).
        avq = quote(avatar, safe="")
        pivots.append(("Google Lens", f"https://lens.google.com/uploadbyurl?url={avq}"))

    if bio_link:
        pivots.append(("Bio link", bio_link))

    return pivots


# Which section a pivot belongs to, used for the headings in the panel.
_REVERSE_IMAGE = {"Google Lens"}


def _pivot_section(label):
    if label in _REVERSE_IMAGE:
        return "who is this?  (reverse image search of the avatar)"
    if label == "Bio link":
        return "their own bio link"
    return "same handle on other platforms"


# Only YouTube reliably 404s for nonexistent usernames. Reddit / Instagram /
# X / Twitch all serve JS-routed SPAs that return HTTP 200 with a "no such
# user" page rendered client-side, so the HTTP status carries no useful signal.
# Rather than print misleading ✓ marks, we don't probe them at all and let the
# user click through to verify by eye. This was checked empirically:
#   curl twitch.tv/zzz_fake     -> 200   (200 for real too)
#   curl reddit.com/user/zzz    -> 200   (200 for real too)
#   curl instagram.com/zzz/     -> 200   (200 for real too)
#   curl x.com/zzz              -> 403   (403 for real too — UA-blocked)
#   curl youtube.com/@zzz       -> 404   (200 for real)  <-- only reliable one
_PROBE_LABELS = {"YouTube"}


def probe_pivots(pivots, session=None):
    """HEAD-check pivot URLs whose platforms reliably distinguish real vs
    fake usernames via HTTP status. Currently that is only YouTube — see the
    comment on _PROBE_LABELS. Returns 3-tuples (label, url, status):
      'exists'  — 200 (account is there)
      'missing' — 404 (definitive no)
      'unknown' — anything else (timeout, 5xx, etc.)
      None      — not a probed platform; just show the URL."""
    sess = session or new_session()

    def check(item):
        label, url = item
        if label not in _PROBE_LABELS:
            return (label, url, None)
        try:
            r = sess.head(url, timeout=5, allow_redirects=True)
            if r.status_code == 200:
                return (label, url, "exists")
            if r.status_code == 404:
                return (label, url, "missing")
            return (label, url, "unknown")
        except Exception:
            return (label, url, "unknown")

    if not any(label in _PROBE_LABELS for label, _ in pivots):
        return [(label, url, None) for label, url in pivots]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(check, pivots))


def integrity_flags(data):
    """
    Heuristic flags spotted purely from the data we already pulled. Returns a
    list of (severity, message) tuples where severity is 'warn' | 'info' | 'ok'.
    No new network calls. Heuristics are inference, not proof.
    """
    flags = []
    if not isinstance(data, dict) or data.get("type") != "account":
        return flags

    followers = data.get("followers") if isinstance(data.get("followers"), int) else None
    following = data.get("following") if isinstance(data.get("following"), int) else None
    videos    = data.get("videos")    if isinstance(data.get("videos"), int)    else None
    created_unix = data.get("account_created_unix")
    age_days = None
    if isinstance(created_unix, (int, float)) and created_unix > 0:
        try:
            age_days = (datetime.now(UTC) - datetime.fromtimestamp(int(created_unix), UTC)).days
        except Exception:
            age_days = None

    # Bought / farmed signals.
    if videos is not None and followers is not None:
        if videos == 0 and followers >= 10_000:
            flags.append(("warn",
                f"0 videos but {followers:,} followers — possibly bought or farmed"))
        elif videos is not None and 0 < videos < 5 and followers >= 100_000:
            flags.append(("warn",
                f"only {videos} videos but {followers:,} followers — unusual ratio"))

    # Rapid-growth signal.
    if age_days is not None and followers is not None:
        if age_days < 180 and followers >= 100_000:
            flags.append(("warn",
                f"account is only {age_days} days old but has {followers:,} followers — rapid growth"))

    # Follow-farm pattern.
    if following is not None and followers is not None and following > 0:
        if following >= 1000 and following > max(followers, 1) * 3:
            flags.append(("info",
                f"follows {following:,} but only {followers:,} followers — follow-back farm pattern"))

    # Handle / nickname change signals (the data is already in the page JSON).
    now = datetime.now(UTC).timestamp()
    uid_mod = data.get("unique_id_modify_time")
    if isinstance(uid_mod, (int, float)) and uid_mod > 0:
        days_since = int((now - uid_mod) / 86400)
        if days_since < 90 and age_days is not None and age_days > 730:
            yrs = age_days // 365
            flags.append(("warn",
                f"handle changed {days_since} days ago on a {yrs}-year-old account — possible rebrand, sale, or takeover"))

    nick_mod = data.get("nick_name_modify_time")
    if isinstance(nick_mod, (int, float)) and nick_mod > 0:
        days_since = int((now - nick_mod) / 86400)
        if days_since < 30:
            flags.append(("info", f"display name changed {days_since} days ago"))

    # Empty / placeholder shell. TikTok auto-assigns a "user1234567890" display
    # name to accounts that never set one, so a default name plus no posts and
    # no bio is the signature of an unused, throwaway, or bot-staging account.
    # Requiring all three keeps it conservative: a real lurker usually still
    # sets a name or a bio, so this won't fire on them.
    nickname = (data.get("nickname") or "").strip()
    if re.match(r"^user\d{6,}$", nickname, re.I) and videos == 0 and not data.get("bio"):
        flags.append(("info",
            "default display name, no bio, and 0 videos — looks like an empty or placeholder account"))

    if not flags:
        flags.append(("ok",
            "Followers, age, handle history, and growth all look normal — no red flags."))
    return flags


def _osc8(url, text=None):
    """Wrap a URL with OSC 8 escape codes so modern terminals (Terminal.app,
    iTerm2, kitty, Warp, GNOME, WezTerm) render it as a clickable hyperlink.
    Old/unsupported terminals strip the escapes and just show `text`.

    Security: strips ASCII control bytes (NUL..0x1f and 0x7f) from both the
    URL and the visible text. Without this, a hostile field embedded with
    \\x1b or \\x07 could break out of the OSC 8 wrapper and inject other
    terminal escapes (e.g. OSC 52, which writes to the user's clipboard on
    some terminals)."""
    safe_url = url.translate(_CTRL_BYTES)
    safe_text = (text or safe_url).translate(_CTRL_BYTES)
    return f"\033]8;;{safe_url}\033\\{safe_text}\033]8;;\033\\"


_STATUS_ICON = {"exists": "✓", "missing": "✗", "unknown": "?"}


def print_pivots_plain(data, session=None):
    """Plain-text rendering of pivot links for CLI mode. Prints each pivot as a
    visible URL grouped by purpose. The URL is wrapped in an OSC 8 hyperlink so
    modern terminals make it clickable, and it also shows in full so it stays
    readable and copyable in any terminal. YouTube is marked ✓ / ✗ when its
    existence can be confirmed; the others can't be probed reliably."""
    pivots = osint_pivots(data)
    if not pivots:
        return
    print(Fore.CYAN + "    🧭 OSINT pivots  " + Fore.WHITE + "(checking platforms…)")
    probed = probe_pivots(pivots, session=session)

    section = None
    for label, url, status in probed:
        sect = _pivot_section(label)
        if sect != section:
            section = sect
            print(Fore.CYAN + f"\n    {sect}")
        mark = _STATUS_ICON.get(status or "", " ")
        print(Fore.WHITE + f"       {mark} {label:<14}{_osc8(url)}")


def print_flags_plain(data):
    """Plain-text rendering of integrity flags for CLI mode."""
    flags = integrity_flags(data)
    if not flags:
        return
    print(Fore.CYAN + "    🚩 Integrity flags")
    icon = {"warn": "⚠️ ", "info": "ℹ️ ", "ok": "✅"}
    for sev, msg in flags:
        print(f"       {icon.get(sev, '·')} {msg}")


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
                f.write(f"  Followers      : {_count(d.get('followers'))}\n")
                f.write(f"  Likes          : {_count(d.get('likes'))}\n")
                f.write(f"  Videos         : {_count(d.get('videos'))}\n")
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
            age = human_age(data.get("account_created_unix"))
            age_str = f" · {age}" if age else ""
            print(Fore.GREEN + f"{indent}📅 created: {data['account_created']}{age_str}  "
                  + Fore.WHITE + f"(@{data['username']}, {_count(data.get('followers'))} followers)")
        else:
            est = data.get('created_estimate_from_id')
            if est:
                print(Fore.YELLOW + f"{indent}🟡 profile found, but TikTok didn't return a creation "
                      f"date. Best estimate from the user ID: {est}")
            else:
                print(Fore.YELLOW + f"{indent}🟡 profile found, but TikTok didn't return a creation "
                      f"date (and the user ID isn't a decodable timestamp).")
    elif data.get("type") == "video":
        print(Fore.GREEN + f"{indent}📅 uploaded: {data['uploaded']}")
    else:
        print(Fore.GREEN + f"{indent}📅 decoded: {data['decoded']}")


def run_batch(targets, session, show_osint=False, show_flags=False):
    print(Fore.CYAN + f"\n[+] Targets: {len(targets)}  (free mode — no API key)\n")
    results = []
    for i, target in enumerate(targets, 1):
        print(Fore.WHITE + f"[{i}/{len(targets)}] {target}")
        kind, data = lookup(target, session)
        show(data)
        if data.get("type") == "account":
            if show_flags:
                print_flags_plain(data)
            if show_osint:
                print_pivots_plain(data, session=session)
        results.append({"target": target, "data": data})
        if kind == "user" and i < len(targets):
            time.sleep(DELAY)
    return results


def run_interactive(session, show_osint=False, show_flags=False):
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
        if data.get("type") == "account":
            if show_flags:
                print_flags_plain(data)
            if show_osint:
                print_pivots_plain(data, session=session)
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
    p.add_argument("--osint", action="store_true",
                   help="also print OSINT pivot links (reverse image search, Wayback, cross-platform handles, bio link)")
    p.add_argument("--flags", action="store_true",
                   help="also print integrity heuristics (bought-followers / takeover / rebrand signals)")
    p.add_argument("--all", action="store_true",
                   help="shorthand for --osint --flags")
    args = p.parse_args()
    if args.all:
        args.osint = args.flags = True

    # Command-line targets: zsh (unlike bash) does NOT strip inline '# comments',
    # so a '#' token arrives as a literal arg. Treat it as start-of-comment and
    # ignore it plus everything after — a pasted "user  # note" stays clean.
    cli = []
    for t in [*args.targets, *( [args.input] if args.input else [] )]:
        if t.strip().startswith("#"):
            break
        cli.append(t)

    # File targets: skip blank lines and whole-line '#' comments. Fail with a
    # friendly message (not a Python traceback) if the path is wrong or sealed.
    file_lines = []
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as f:
                file_lines = [ln.strip() for ln in f
                              if ln.strip() and not ln.strip().startswith("#")]
        except FileNotFoundError:
            print(Fore.RED + f"❌ Could not open file: {args.file} (no such file)")
            return
        except PermissionError:
            print(Fore.RED + f"❌ Could not open file: {args.file} (permission denied)")
            return
        except OSError as e:
            print(Fore.RED + f"❌ Could not open file: {args.file} ({e})")
            return

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
        results = run_batch(targets, session,
                            show_osint=args.osint, show_flags=args.flags)
        prefix = "single" if len(targets) == 1 else "batch"
    else:
        results = run_interactive(session,
                                  show_osint=args.osint, show_flags=args.flags)
        prefix = "interactive"

    successes = [r for r in results if isinstance(r.get("data"), dict) and "error" not in r["data"]]
    if successes:
        jp, tp = save_reports(results, prefix)
        print(Fore.CYAN + f"\n📁 Reports:\n   {jp}\n   {tp}\n")
    elif results:
        print(Fore.YELLOW + "\n📭 Nothing worth saving — every lookup this session errored. No report written.\n")


if __name__ == "__main__":
    main()
