<h1 align="center">TokIntel: free TikTok account lookup</h1>

<p align="center">
  <img src="assets/banner.png" alt="TokIntel: free TikTok account lookup" width="660">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/API%20key-not%20needed-25F4EE" alt="No API key">
  <img src="https://img.shields.io/badge/license-MIT-3FB950" alt="MIT License">
</p>

<p align="center">Find when a TikTok account was created. No API key, no signup, just a username.</p>

---

## What it does

- **Account creation date** from a username, `@handle`, or profile URL, plus followers, likes, bio, verified, and private status.
- **Video upload time** from a video URL or id (the snowflake timestamp, `id >> 32`).
- **Optional OSINT pivots** (opt in): real leads to find a person's other accounts, never a guess. For any account it gives you the finders that always work: a reverse image search of the avatar (find the same face anywhere online), a web search of the exact handle, and a Wayback snapshot when one exists. And it pulls the real accounts they connected themselves: the whole list off a Linktree (or hoo.be, Beacons, Carrd, and similar), the social accounts off a personal website they linked, and any handles they spell out in their bio text ("IG: @them"). A same username existing on some other site is never shown, because that proves nothing about who owns it.
- **Optional integrity flags** (opt in): heuristic signals for bought followers, follow farms, rapid growth, and recent handle or display name changes, shown as neutral context rather than accusations.
- **Reports** saved to `reports/` as JSON and TXT.
- A clean terminal UI, or a single command. No RapidAPI, no key, no card.

<details>
<summary><b>More screenshots</b></summary>

<br>

<p align="center"><i>A full account lookup</i><br><img src="assets/account.png" alt="account lookup result" width="660"></p>

<p align="center"><i>OSINT pivots: the finders that work for any account, plus the real accounts off their own link-in-bio page</i><br><img src="assets/pivots.png" alt="OSINT pivots panel" width="660"></p>

<p align="center"><i>Integrity flags</i><br><img src="assets/flags.png" alt="integrity flags on a bought-follower account" width="660"></p>

<p align="center"><i>An account with audience controls on</i><br><img src="assets/locked.png" alt="a locked account before unlocking" width="660"></p>

</details>

## Get it

**Easiest, no tools needed:** click the green **`< > Code`** button near the top of this page, choose **Download ZIP**, then unzip it.

**Or with git:**
```bash
git clone https://github.com/Thyfwx/TokIntel.git
cd TokIntel
```

The only thing you need installed yourself is **Python 3.11 or newer** ([get it from python.org](https://www.python.org/downloads/) if you don't have it). Everything else (`requests`, `colorama`, `rich`) is installed for you automatically the first time you run it.

## Run it

| Your system | How to start |
| --- | --- |
| **macOS** | double click `TokIntel.app` (or `TokIntel.command`) |
| **Windows** | double click `start.bat` |
| **Linux / any terminal** | run `./start.sh` |

The launcher builds its own virtual environment and installs `requests`, `colorama`, and `rich` on first run, so there is nothing to set up by hand.

> **macOS:** if you downloaded the ZIP and a double-click is blocked ("unidentified developer"), right-click `TokIntel.app` → **Open** → **Open** once, and it will trust it from then on. Cloning with git avoids this entirely.

Prefer the command line?

```bash
python3 tiktok_created.py charlidamelio
python3 tiktok_created.py @nasa https://www.tiktok.com/@zachking

# Optional extras (off by default)
python3 tiktok_created.py charlidamelio --osint    # add pivot links
python3 tiktok_created.py charlidamelio --flags    # add integrity heuristics
python3 tiktok_created.py charlidamelio --all      # both
```

In the interactive UI, a short numbered menu appears after each card so you can pull the extras up only when you want them.

## How it works

TikTok embeds the account `createTime` in the JSON on every public profile page, so one request to the profile is enough to read it. Video IDs are snowflakes, so a video's upload time comes from `id >> 32`. No login and no third party API for public accounts, which is almost all of them.

## Accounts with audience controls on (optional)

A few accounts turn on TikTok's audience controls, for example the "18 and older" setting. TikTok then refuses to show that profile to anyone who is not signed in, so a normal lookup gets no date or stats back. That is the account owner's setting, not a limit of this tool, and most accounts have it off and need nothing.

You can still read these with your own TikTok login, and there is nothing to install or paste. Just look the account up in the app. When it is locked, it asks:

```
read it with  chrome / firefox / edge / brave / safari
```

Pick the browser you are already signed into TikTok on, and it reads the account with that login. If you are not signed in, it just tells you to sign in and try again, no errors and no dead ends.

Your privacy, plainly: the login is read only on your own computer, only for that one lookup. It is never saved, sent anywhere, shown, or written into the code, so a clone or a fork has nothing of yours in it.

Prefer the command line? Set `TIKTOK_COOKIES_FROM_BROWSER=chrome` (or `firefox`, `edge`, `brave`, `safari`) before running, or drop your `sessionid` value into a gitignored `tiktok_session.txt`.

## Requirements

Python 3.11+ and `requests`, `colorama`, `rich` (installed automatically by the launcher, or `pip install -r requirements.txt`).

## Credit

Built on top of [TokIntel](https://github.com/HackUnderway/TokIntel) by Victor Bancayan (Hack Underway). The original does more, including email and phone lookups through RapidAPI. This build is a free option that needs no key, for looking up creation dates. Licensed under MIT, see [LICENSE](LICENSE).

## Disclaimer

For educational and OSINT research only. It reads public profile data. Do not use it for anything illegal.
