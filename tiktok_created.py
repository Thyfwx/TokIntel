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
import unicodedata
try:
    import readline  # noqa: F401  enables ← → line editing + ↑ history in the prompt
except ImportError:
    readline = None  # Windows without pyreadline; basic input still works
from datetime import datetime, UTC
from urllib.parse import quote, unquote, urlparse

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


def _is_web_url(value):
    """True only for http(s) URLs. These are the only links we ever turn into a
    clickable terminal hyperlink. A hostile profile can put any scheme in its
    bio link (file:, smb:, ssh:, javascript:, a custom app scheme); a one-click
    open of those could leak credentials, reach an internal host, or launch an
    app. Non-web links are still shown in full, just as plain text to copy."""
    return isinstance(value, str) and bool(re.match(r"(?i)^https?://", value))


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
# window: the musical.ly/TikTok era through today. Anything outside that we
# report as unknown rather than printing a confidently wrong date.
_SNOWFLAKE_MIN_TS = 1_388_534_400   # 2014-01-01 UTC, musical.ly-era floor


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

def _is_blank_name(name):
    """True if a display name is empty or shows as nothing. Covers whitespace,
    the control / format / separator / unassigned Unicode categories, and the
    invisible glyphs people use for a 'no-name' TikTok profile. That last group
    (Hangul fillers, the Braille blank) are letters or symbols by category, so
    we spot them by their Unicode name instead. The account is still real."""
    if not name:
        return True
    invisible_cats = {"Cc", "Cf", "Cn", "Co", "Cs", "Zs", "Zl", "Zp"}
    for ch in name:
        if ch.isspace() or unicodedata.category(ch) in invisible_cats:
            continue
        nm = unicodedata.name(ch, "")
        if "FILLER" in nm or nm == "BRAILLE PATTERN BLANK":
            continue
        return False
    return True


def _oembed(username, session):
    """Confirm an account exists through TikTok's oEmbed endpoint, which still
    answers for accounts the logged-out profile API refuses to serve (statusCode
    209002, e.g. accounts with audience controls). Returns the parsed JSON for a real
    account, or None for a nonexistent handle or any failure. oEmbed carries
    only the handle and display name, never a creation date."""
    try:
        r = session.get(
            "https://www.tiktok.com/oembed?url=https://www.tiktok.com/@"
            + quote(username, safe=""), timeout=8)
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def fetch_user(username, session):
    if not USERNAME_RE.match(username):
        return {"error": "That doesn't look like a TikTok username. "
                         "Try just the @handle (e.g. 'nasa') or a "
                         "tiktok.com profile URL."}

    # quote() leaves valid username characters (letters, digits, _ . - ~) as-is
    # but percent-encodes anything unusual, so a crafted target can't alter the
    # request structure. The host and scheme are already a fixed literal prefix.
    url = f"https://www.tiktok.com/@{quote(username, safe='')}"

    # One brief retry on the "TikTok served a stub" case, usually a soft throttle
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
            # reach the user. Every other error path here is already friendly.
            if attempt == 1:
                time.sleep(2.5)
                continue
            return {"error": "Couldn't reach TikTok. Check your internet connection, then try again."}
        m = REHYDRATION_RE.search(r.text)
        if m:
            break
        if attempt == 1:
            time.sleep(2.5)
    if not m:
        return {"error": "TikTok throttled this lookup. Wait a moment, then try again."}
    try:
        scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
    except ValueError:
        return {"error": "json_parse_failed"}

    detail = scope.get("webapp.user-detail")
    if not detail:
        return {"error": "user-detail missing, profile not returned (blocked, or no such page)"}

    status = detail.get("statusCode")
    info = detail.get("userInfo", {})
    user = info.get("user", {})
    # statsV2 carries correct string counts; the legacy `stats` object stores
    # 32-bit ints that overflow (negative likes) for accounts past ~2.1B, so
    # prefer statsV2 and fall back to stats only if it's absent.
    stats = info.get("statsV2") or info.get("stats") or {}
    if not user:
        # 10221 is the only code that means the handle simply doesn't exist.
        # Any other code means TikTok recognizes the handle but won't serve its
        # profile to a logged-out request. We can NOT tell why from the outside
        # and it usually is not a ban or deletion, so we don't guess: say that
        # plainly, point at the real profile, and keep the raw code (in the data,
        # not on screen) for diagnosis.
        if status == 10221:
            return {"error": "TikTok has no account with that username. "
                             "Check the spelling, or it may have been deleted."}
        # The logged-out profile API returned no user, almost always because the
        # account has audience controls on (TikTok's "18 and older" setting gives
        # exactly this 209002 wall). Don't guess: confirm through oEmbed, which
        # still answers for these accounts. If it's real, say so and explain how
        # to read it (a logged-in session, or the owner turning the setting off),
        # instead of calling a real account an error.
        oe = _oembed(username, session)
        if oe:
            name = oe.get("author_name") or ""
            hidden = _is_blank_name(name)
            return {
                "type": "limited",
                "username": _clean(username),
                "name_hidden": hidden,
                "nickname": None if hidden else _clean(name),
                "tiktok_status": status,
                "session_active": _session_configured(),
            }
        return {"error": "TikTok wouldn't return this profile to a logged-out "
                         "lookup, and oEmbed couldn't confirm the account either. "
                         "It may be temporarily unavailable. Open it on tiktok.com "
                         "to check.",
                "state": "unavailable",
                "tiktok_status": status}

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
    # Strip control bytes BEFORE the scheme check below. Otherwise a value like
    # "\tfile:///etc/passwd" hides its scheme (the regex wants a leading letter),
    # gets https:// prepended, and ends up a mangled but clickable link that also
    # dodges the risky-scheme flag. Cleaning first keeps "file:" visible as "file:".
    bio_link = _clean(bio_link)
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
        "bio_link": bio_link,  # already control-byte-stripped above, before the scheme check
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
        "note": ("Read straight from the ID number. TikTok bakes the post "
                 "time into video IDs. For an account's creation date, type "
                 "its @username instead (user IDs are too small to hold a "
                 "timestamp)."),
    }


# ----------------------------------------------------------- OSINT extras
def osint_pivots(data):
    """Return an ordered list of (label, url) pivots for an account result.
    URLs only, no fetches happen here: the same handle on platforms we can
    verify by HTTP (YouTube, GitHub, Linktree, Snapchat, SoundCloud, Patreon,
    Tumblr, Roblox), a web search of the handle, reverse image searches of the
    avatar (Google Lens, Yandex, TinEye), the account's own bio link, and its
    Wayback history. Platforms we can't tell real from fake on without a login
    (Instagram, X, Twitch, Reddit, ...) are left out instead of guessed."""
    pivots = []
    if not isinstance(data, dict) or data.get("type") != "account":
        return pivots

    username = data.get("username")
    avatar = data.get("avatar")
    bio_link = data.get("bio_link")
    # Encode the handle: it comes from TikTok's (attacker-controllable) profile
    # JSON, so a crafted value shouldn't be able to bend the path or produce a
    # misleading link.
    u = quote(username, safe="") if username else None

    if username:
        # Same-name platforms that 404 for a fake handle, so we can verify each
        # one exists before showing it (see probe_pivots / _PROBE_LABELS). Only
        # the ones that actually resolve get shown.
        pivots.append(("YouTube", f"https://www.youtube.com/@{u}"))
        pivots.append(("GitHub", f"https://github.com/{u}"))
        pivots.append(("Linktree", f"https://linktr.ee/{u}"))
        pivots.append(("Snapchat", f"https://www.snapchat.com/add/{u}"))
        pivots.append(("SoundCloud", f"https://soundcloud.com/{u}"))
        pivots.append(("Patreon", f"https://www.patreon.com/{u}"))
        pivots.append(("Tumblr", f"https://www.tumblr.com/{u}"))
        pivots.append(("Roblox", f"https://www.roblox.com/user.aspx?username={u}"))
        # A web search for the exact handle: surfaces real mentions and linked
        # accounts across the web, not just same-name guesses.
        pivots.append(("Google search", f"https://www.google.com/search?q=%22{u}%22"))
    if avatar:
        # Reverse image searches of the avatar. Lens is best for objects, Yandex
        # for finding a real person by face, TinEye for exact copies elsewhere.
        avq = quote(avatar, safe="")
        pivots.append(("Google Lens", f"https://lens.google.com/uploadbyurl?url={avq}"))
        pivots.append(("Yandex image", f"https://yandex.com/images/search?rpt=imageview&url={avq}"))
        pivots.append(("TinEye", f"https://tineye.com/search?url={avq}"))
    if bio_link:
        pivots.append(("Bio link", bio_link))
    if username:
        # Shown only when a snapshot actually exists (verified in probe_pivots).
        pivots.append(("Wayback", f"https://web.archive.org/web/*/tiktok.com/@{u}"))

    return pivots


# Pivots that point at the target itself, kept apart from the same handle on
# other sites so we never imply a namesake is the same person.
_OWN_PIVOTS = {"Bio link", "Wayback", "Google Lens", "Yandex image", "TinEye", "Google search"}

# Host for each same-handle platform. Used to confirm one as really theirs when
# the account's own bio link points to it (the owner linked it themselves).
_PLATFORM_HOST = {
    "YouTube": "youtube.com", "GitHub": "github.com", "Linktree": "linktr.ee",
    "Snapchat": "snapchat.com", "SoundCloud": "soundcloud.com",
    "Patreon": "patreon.com", "Tumblr": "tumblr.com", "Roblox": "roblox.com",
}


def grouped_pivots(probed, bio_link=None):
    """Split probed pivots for display. Returns (own, same_name, found_handle):
      own         : the target's own links, its bio link, Wayback snapshot, avatar
                    reverse image search, plus any same-handle account the profile
                    itself links to from its bio (so we know it really is them).
      same_name   : the same handle found on other platforms that we can't tie to
                    this person, so a possible namesake.
      found_handle: whether the handle turned up on any other platform at all.
    Only confirmed or resolving links are returned; misses are dropped."""
    bio_host = ""
    if bio_link:
        try:
            bio_host = (urlparse(bio_link).hostname or "").lower()
            if bio_host.startswith("www."):
                bio_host = bio_host[4:]
        except Exception:
            bio_host = ""
    own, same_name, found_handle = [], [], False
    for label, url, status in probed:
        if label in _OWN_PIVOTS:
            # Bio link and Google Lens always apply; Wayback only when a real
            # snapshot is confirmed (its availability API rate-limits and can
            # fail, so we never claim a 'none' we might be wrong about).
            if label == "Wayback" and status != "exists":
                continue
            own.append((label, url, status))
        elif status == "exists":
            found_handle = True
            host = _PLATFORM_HOST.get(label, "")
            # If the profile's own bio link points to this platform, the owner
            # linked it themselves, so it is confirmed theirs, not a namesake.
            confirmed = bool(host and bio_host and
                             (bio_host == host or bio_host.endswith("." + host)))
            (own if confirmed else same_name).append((label, url, status))
    return own, same_name, found_handle


# Platforms we can actually verify before showing a link: they 404 for a fake
# handle, so the HTTP status is a real yes/no. Wayback is verified through its
# availability API instead. Platforms that answer the same for real and fake
# names (login walls, SPA 200s, or blocks) carry no signal and aren't listed.
# Checked empirically (2026-06):
#   verifiable -> youtube, github, linktr.ee, snapchat, soundcloud, patreon,
#                 tumblr, roblox   (404 for a fake handle, 200 for a real one)
#   no signal  -> instagram, x, twitch, reddit, telegram, steam, pinterest, kick
_PROBE_LABELS = {"YouTube", "GitHub", "Linktree", "Snapchat", "SoundCloud",
                 "Patreon", "Tumblr", "Roblox", "Wayback"}


def probe_pivots(pivots, session=None):
    """HEAD-check pivot URLs whose platforms reliably distinguish real vs
    fake usernames via HTTP status. Currently that is only YouTube. See the
    comment on _PROBE_LABELS. Returns 3-tuples (label, url, status):
      'exists'  : 200 (account is there)
      'missing' : 404 (definitive no)
      'unknown' : anything else (timeout, 5xx, etc.)
      None      : not a probed platform; just show the URL."""
    sess = session or new_session()

    def check(item):
        label, url = item
        if label not in _PROBE_LABELS:
            return (label, url, None)
        try:
            if label == "Wayback":
                # The pivot URL ends in ".../web/*/tiktok.com/@handle"; ask the
                # availability API whether any snapshot exists for that target.
                # Decode the handle (percent-encoded upstream) and let requests
                # re-encode the query param exactly once; the availability API
                # reports no match unless ?url= is encoded. When a snapshot
                # exists, show that real capture URL (guaranteed to load) rather
                # than a wildcard calendar that may not resolve.
                target = unquote(url.split("/web/*/", 1)[-1])
                # The availability API is flaky under load and can return empty
                # for a profile that IS archived, so try twice before believing
                # there's no snapshot.
                snap = {}
                for attempt in (1, 2):
                    try:
                        r = sess.get("https://archive.org/wayback/available",
                                     params={"url": target}, timeout=6)
                        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
                    except Exception:
                        snap = {}
                    if snap.get("url"):
                        break
                    if attempt == 1:
                        time.sleep(0.6)
                if snap.get("url"):
                    return (label, snap["url"], "exists")
                return (label, url, "missing")
            # GET with the body streamed (never read) is more reliable across
            # these sites than HEAD, which some answer wrongly or reject.
            r = sess.get(url, timeout=6, allow_redirects=True, stream=True)
            code = r.status_code
            r.close()
            if code == 200:
                return (label, url, "exists")
            if code in (404, 410):
                return (label, url, "missing")
            return (label, url, "unknown")
        except Exception:
            return (label, url, "unknown")

    if not any(label in _PROBE_LABELS for label, _ in pivots):
        return [(label, url, None) for label, url in pivots]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(check, pivots))


# Bio-link schemes that are never merely informational: opening one could leak
# credentials, reach a local file, or launch an app. We never make them
# clickable; in flags mode we also call them out so the investigator notices.
_RISKY_LINK_SCHEMES = {"file", "smb", "ssh", "ftp", "vnc", "data", "javascript", "jar", "telnet"}


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
                f"0 videos but {followers:,} followers, possibly bought or farmed"))
        elif videos is not None and 0 < videos < 5 and followers >= 100_000:
            flags.append(("warn",
                f"only {videos} videos but {followers:,} followers, an unusual ratio"))

    # Rapid-growth signal.
    if age_days is not None and followers is not None:
        if age_days < 180 and followers >= 100_000:
            flags.append(("warn",
                f"account is only {age_days} days old but has {followers:,} followers, rapid growth"))

    # Follow-farm pattern.
    if following is not None and followers is not None and following > 0:
        if following >= 1000 and following > max(followers, 1) * 3:
            flags.append(("info",
                f"follows {following:,} but only {followers:,} followers, a follow-back farm pattern"))

    # Handle / nickname change signals (the data is already in the page JSON).
    now = datetime.now(UTC).timestamp()
    uid_mod = data.get("unique_id_modify_time")
    if isinstance(uid_mod, (int, float)) and uid_mod > 0:
        days_since = int((now - uid_mod) / 86400)
        if days_since < 90 and age_days is not None and age_days > 730:
            yrs = age_days // 365
            flags.append(("info",
                f"handle changed {days_since} days ago on a {yrs}-year-old account "
                "(normal on its own; only worth a look alongside other odd signals)"))

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
            "default display name, no bio, and 0 videos, so it looks like an empty or placeholder account"))

    # A bio link with a risky scheme is shown but never made clickable (see
    # _is_web_url); this flag makes sure an investigator who only opens the
    # integrity view still notices the account tried to plant such a link.
    bio_link = data.get("bio_link")
    if isinstance(bio_link, str) and bio_link and not _is_web_url(bio_link):
        scheme = bio_link.split(":", 1)[0].lower()
        if scheme in _RISKY_LINK_SCHEMES:
            flags.append(("warn",
                f"bio link uses a risky '{scheme}:' scheme, shown but not made clickable"))

    if not flags:
        flags.append(("ok",
            "Followers, age, handle history, and growth all look normal. No red flags."))
    return flags


def print_pivots_plain(data, session=None):
    """Plain-text pivots for CLI mode, in two groups: the target's own links
    (bio link, Wayback snapshot, avatar reverse image search, plus any same-handle
    account confirmed through their bio), then the same handle found on other
    sites. Links are shown as full URLs to copy or open, nothing is made
    clickable. Anything we couldn't confirm or that didn't resolve is left out."""
    pivots = osint_pivots(data)
    if not pivots:
        return
    print(Fore.CYAN + "    🧭 OSINT pivots  " + Fore.WHITE + "(checking…)")
    own, same_name, found_handle = grouped_pivots(
        probe_pivots(pivots, session=session), data.get("bio_link"))

    def show(group):
        for i, (label, url, _) in enumerate(group):
            if i:
                print()   # a little breathing room between links
            print(Fore.WHITE + f"       {label:<12} {url.translate(_CTRL_BYTES)}")

    if own:
        print(Fore.CYAN + "\n    From this profile")
        show(own)
    if same_name:
        print(Fore.CYAN + "\n    Same handle on other sites")
        show(same_name)
    elif not found_handle:
        print(Fore.CYAN + "\n    Same handle on other sites")
        print(Fore.YELLOW + "       Nothing found with this handle")


def print_flags_plain(data):
    """Plain-text rendering of integrity flags for CLI mode."""
    flags = integrity_flags(data)
    if not flags:
        return
    print(Fore.CYAN + "    🚩 Integrity flags")
    color = {"warn": Fore.YELLOW, "info": Fore.WHITE, "ok": Fore.GREEN}
    for sev, msg in flags:
        print(color.get(sev, Fore.WHITE) + f"       {msg}")


# ------------------------------------------------------------------ reports
def reports_dir():
    if not os.path.exists("reports"):
        os.makedirs("reports")
    return "reports"


def limited_lines(data):
    """Two human lines for a real account TikTok won't serve to a logged-out
    request (audience controls, e.g. the 18+ setting): a 'who' label and a hint
    on how to read it. Shared by the CLI, the saved report, and the Rich UI."""
    u = data.get("username")
    if data.get("name_hidden"):
        who = f"@{u} (display name hidden, an invisible character)"
    elif data.get("nickname"):
        who = f"@{u} ({data.get('nickname')})"
    else:
        who = f"@{u}"
    if data.get("session_active"):
        hint = ("Your saved TikTok session didn't unlock it. It may be logged "
                "out or expired, or the account is restricted beyond the usual "
                "audience controls.")
    else:
        hint = ("TikTok has audience controls on this account (like the 18 and "
                "older setting), so looking it up without a login can't read its "
                "date or stats. Sign into TikTok in your browser and the tool can "
                "read it with that login (see the README), or the owner can turn "
                "that setting off.")
    return who, hint


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
            elif d.get("type") == "limited":
                _, hint = limited_lines(d)
                f.write(f"  Username       : @{d.get('username')}\n")
                f.write(f"  Display name   : {'hidden / blank' if d.get('name_hidden') else d.get('nickname')}\n")
                f.write("  Account created: not available without a logged-in session\n")
                f.write(f"  Note           : {hint}\n")
            else:
                f.write(f"  Decoded id     : {d.get('decoded')}\n")
            f.write("\n")
    return base + ".json", base + ".txt"


# ------------------------------------------------------------------ main
_session_value = (None, None)   # cached (cookie_header, sessionid)
_session_read = False           # whether we've resolved the session yet


def _cookies_from_browser(browser):
    """Read the user's own TikTok cookies from their local browser (opt-in via
    TIKTOK_COOKIES_FROM_BROWSER=chrome|firefox|edge|brave|safari|...). Returns a
    Cookie header string, or None. Read locally at runtime with browser_cookie3,
    used in memory for the request only, never stored, logged, printed, or put in
    the repo. Never raises."""
    try:
        import browser_cookie3 as bc3
    except ImportError:
        print("  Browser reader not available. Start TokIntel with start.sh or "
              "TokIntel.app and it installs itself.")
        return None
    loader = getattr(bc3, browser, None)
    if loader is None:
        print(f"  Unknown browser '{browser}'. Try: chrome, firefox, edge, brave, safari.")
        return None
    try:
        jar = loader(domain_name="tiktok.com")
        pairs = [f"{c.name}={c.value}" for c in jar]
        return "; ".join(pairs) if pairs else None
    except Exception as e:
        print(f"  Couldn't read {browser} cookies ({type(e).__name__}); "
              "make sure you're logged into TikTok in that browser.")
        return None


def _read_session():
    """Optional TikTok session for reading accounts behind audience controls
    (e.g. the 18+ setting). Returns (cookie_header, sessionid), either may be
    None. Sources, in order: TIKTOK_COOKIE env, TIKTOK_SESSIONID env,
    TIKTOK_COOKIES_FROM_BROWSER env (your own browser's login, read locally),
    then a local gitignored tiktok_session.txt. Read once and cached. Used only
    in memory; never stored, logged, printed, or committed to the repo."""
    global _session_value, _session_read
    if _session_read:
        return _session_value
    result = (None, None)
    cookie = os.environ.get("TIKTOK_COOKIE", "").strip()
    sid = os.environ.get("TIKTOK_SESSIONID", "").strip()
    browser = os.environ.get("TIKTOK_COOKIES_FROM_BROWSER", "").strip().lower()
    if cookie:
        result = (cookie, None)
    elif sid:
        result = (None, sid)
    elif browser:
        bc = _cookies_from_browser(browser)
        if bc:
            result = (bc, None)
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_session.txt")
        try:
            with open(path, encoding="utf-8") as f:
                val = f.read().strip()
        except OSError:
            val = ""
        if val:
            # A pasted full Cookie header has "name=value; ..."; a bare sessionid
            # doesn't. Handle either form.
            if ";" in val or val.lower().startswith("sessionid="):
                result = (val, None)
            else:
                result = (None, val)
    _session_value = result
    _session_read = True
    return result


def use_browser_session(browser):
    """Turn on the browser-login source at runtime (for the app's guided
    unlock): set it, clear the cached session, and report whether a TikTok
    login was actually found. Never raises, never exposes the cookie."""
    global _session_read, _session_value
    os.environ["TIKTOK_COOKIES_FROM_BROWSER"] = browser
    _session_read = False
    _session_value = (None, None)
    cookie, sid = _read_session()
    return bool(cookie or sid)


def _session_configured():
    """True if the user has provided an optional session (only used to word the
    message on a gated account, never to reveal the session itself)."""
    cookie, sid = _read_session()
    return bool(cookie or sid)


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    # Opt-in unlock for accounts behind TikTok's audience controls (e.g. the
    # "18 and older" setting), which a logged-out request can't read. The session
    # is read only at runtime and attached to this in-memory session; it is never
    # written to a report, a log, or the screen. Normal public accounts need none
    # of this. Use a throwaway account, never your main one (see the README).
    cookie, sid = _read_session()
    # Attach the login as tiktok.com-scoped cookies in the jar, never as a blanket
    # Cookie header. The same session also probes the OSINT platforms (youtube,
    # github, archive.org, ...), and a blanket header would send your TikTok login
    # to every one of them. Scoped jar cookies are only ever sent to tiktok.com.
    if cookie:
        for part in cookie.split(";"):
            name, sep, value = part.strip().partition("=")
            if name and sep:
                s.cookies.set(name, value, domain=".tiktok.com")
    elif sid:
        s.cookies.set("sessionid", sid, domain=".tiktok.com")
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
        # Amber for "couldn't load" (the account may be fine), red for a true miss.
        color = Fore.YELLOW if data.get("state") == "unavailable" else Fore.RED
        print(color + f"{indent}⚠️  {data['error']}")
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
    elif data.get("type") == "limited":
        who, hint = limited_lines(data)
        print(Fore.CYAN + f"{indent}🔵 {who} is a real account.")
        print(Fore.WHITE + f"{indent}   {hint}")
    elif data.get("type") == "video":
        print(Fore.GREEN + f"{indent}📅 uploaded: {data['uploaded']}")
    else:
        print(Fore.GREEN + f"{indent}📅 decoded: {data['decoded']}")


def run_batch(targets, session, show_osint=False, show_flags=False):
    print(Fore.CYAN + f"\n[+] Targets: {len(targets)}  (free mode, no API key)\n")
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
    print(Fore.CYAN + "\n🔎 TikTok creation-date lookup  (free, no API key)")
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
        description="Get a TikTok account's creation date. Free, no API key. "
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
    # ignore it plus everything after, so a pasted "user  # note" stays clean.
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
        print(Fore.YELLOW + "\n📭 Nothing worth saving. Every lookup this session errored. No report written.\n")


if __name__ == "__main__":
    main()
