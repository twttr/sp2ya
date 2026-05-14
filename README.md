# sp2ya

Transfer liked songs between music streaming services.

Originally built for Spotify → Yandex Music. Now also supports Yandex Music → YouTube Music (using a Spotify CSV backup enriched with any newer Yandex-only likes).

## Features

- Two-pass matching: exact search, then fuzzy matching with configurable threshold
- Resumable transfers with progress tracking — safe to interrupt and restart
- Source tracks cached locally to avoid repeated API calls
- Unmatched tracks logged to CSV for manual review
- Dry-run mode for testing without side effects

## Prerequisites

- Python 3.8+
- For the Spotify → Yandex flow: a [Spotify Developer](https://developer.spotify.com/dashboard) account and a [Yandex Music](https://music.yandex.com) account
- For the Yandex → YouTube Music flow: a [Yandex Music](https://music.yandex.com) account (to fetch current likes), a Spotify CSV backup, and a logged-in YouTube Music browser session

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create `.env`

```
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
YANDEX_MUSIC_TOKEN=your_yandex_token
SC_OAUTH_TOKEN=your_soundcloud_oauth_token        # only if --soundcloud
SC_CLIENT_ID=your_soundcloud_client_id            # only if --soundcloud
```

YouTube Music doesn't need any `.env` entries — it uses a `browser.json` file generated from your logged-in browser session (see below).

Per-service setup details below — only fill in what you need for the flow you're using.

### Spotify (live API)

1. Go to https://developer.spotify.com/dashboard
2. Create App, set redirect URI to `http://127.0.0.1:8888/callback`, enable Web API
3. Copy the **Client ID** → `SPOTIFY_CLIENT_ID`

### Yandex Music

1. Install the token browser extension: https://github.com/MarshalX/yandex-music-token
2. Log in to Yandex Music in your browser
3. Copy the displayed OAuth token → `YANDEX_MUSIC_TOKEN`

Tokens may expire after ~90 days.

### SoundCloud (optional, browser auth)

The official SoundCloud API requires a paid Artist Pro subscription to register an app, so we use the internal v2 API the website itself calls.

1. Open https://soundcloud.com in an **incognito** window and sign in.
2. Open dev tools → **Network** tab.
3. Click around (open your library, play something) to generate API requests to `api-v2.soundcloud.com`.
4. Click any such request. In the headers, find:
   - `Authorization: OAuth <token>` — copy the token (everything after `OAuth `) into `SC_OAUTH_TOKEN`.
   - The request URL contains `client_id=<32-char string>` — copy into `SC_CLIENT_ID`.
5. Add both to `.env`, then run `enrich.py --soundcloud`.

Tokens last a few weeks to months; refresh when the script reports auth failure.

### YouTube Music (browser auth)

Google [disabled OAuth](https://github.com/sigma67/ytmusicapi/issues/676) for third-party YouTube Music clients in late 2024, so we use browser cookies instead. The cookies typically last weeks to months.

1. Open https://music.youtube.com in an **incognito / private** window and sign in. (Incognito so regular browsing doesn't rotate the cookies you're about to capture.)
2. Open dev tools (F12 or Cmd+Option+I) → **Network** tab.
3. Click around in YT Music to generate requests. Find a POST request whose URL contains `/youtubei/v1/` (e.g. `next`, `browse`, `search`).
4. Right-click that request → **Copy → Copy as cURL** (Chrome/Edge) or **Copy Request Headers** (Firefox/Safari).
5. Generate `browser.json`:

```bash
source .venv/bin/activate
ytmusicapi browser
```

Paste the headers when prompted; press Enter, then Ctrl-D, then Enter to finish.

If your terminal eats Ctrl-D after a paste, dump the clipboard to a file and feed it directly:

```bash
pbpaste > /tmp/yt_headers.txt
python -c "from ytmusicapi.auth.browser import setup_browser; setup_browser(filepath='browser.json', headers_raw=open('/tmp/yt_headers.txt').read())"
rm /tmp/yt_headers.txt
```

When the cookies eventually expire, the transfer script will start returning auth errors — just repeat the steps above to refresh `browser.json`.

## Usage

All commands assume the venv is active. Activate it once per shell session:

```bash
source .venv/bin/activate
```

### Spotify → Yandex Music (live)

```bash
source .venv/bin/activate
python transfer.py              # full transfer
python transfer.py --dry-run    # match only, don't like on Yandex
python transfer.py --refresh    # re-fetch Spotify (ignores cache)
python transfer.py --fuzzy-threshold 85
```

On first run, a browser window opens for Spotify authorization. After that, the token is cached.

### Yandex Music → YouTube Music (via Spotify backup + Yandex enrichment)

Step 1 — build a unified list from your Spotify CSV backup plus current Yandex likes (and optionally SoundCloud):

```bash
source .venv/bin/activate
python enrich.py --spotify-csv ~/Downloads/spotify_playlists_2026_02_06/Liked_Songs.csv
python enrich.py --spotify-csv ~/Downloads/spotify_playlists_2026_02_06/Liked_Songs.csv --soundcloud
```

This writes `unified_likes.json` (deduped by normalized artist + title, oldest-first). The Yandex fetch is cached in `yandex_likes.json`; pass `--refresh-yandex` to re-fetch. SoundCloud is cached in `soundcloud_likes.json`; pass `--refresh-soundcloud` to re-fetch.

Step 2 — push the unified list to YouTube Music:

```bash
source .venv/bin/activate
python transfer_youtube.py                    # full transfer
python transfer_youtube.py --dry-run          # match only, don't like
python transfer_youtube.py --fuzzy-threshold 85
```

Progress is saved to `youtube_transfer_progress.json` after each batch — safe to interrupt and restart.

## Output files

| File | Description |
|---|---|
| `spotify_likes.json` | Cached Spotify liked tracks (Spotify → Yandex flow) |
| `transfer_progress.json` | Yandex transfer progress |
| `failed_matches.csv` | Tracks not found on Yandex |
| `yandex_likes.json` | Cached current Yandex liked tracks |
| `soundcloud_likes.json` | Cached current SoundCloud liked tracks (if `--soundcloud`) |
| `unified_likes.json` | Merged Spotify CSV + Yandex (+ SoundCloud) likes (input to YouTube transfer) |
| `youtube_transfer_progress.json` | YouTube Music transfer progress |
| `youtube_failed_matches.csv` | Tracks not found on YouTube Music |

## How matching works

1. **Exact search**: queries the target service with `"Artist - Track Name"` and takes the first result
2. **Fuzzy search**: normalizes strings (strips feat., brackets, punctuation) and compares the top 5 results using token-sorted fuzzy matching on artist + title. Accepts matches scoring above the threshold (default 75/100)

YouTube Music also falls back from the `songs` filter to the `videos` filter, since some niche or older tracks only exist as user-uploaded videos.

## Limitations

- Some tracks may not exist on the target service due to licensing differences
- Artists with different names across platforms (e.g. Latin vs Cyrillic) may not match automatically — these are logged to the failures CSV
- Tokens expire periodically (Yandex ~90 days; YouTube Music browser cookies usually last weeks-to-months)
- Large libraries (5000+ tracks) take a while due to rate limiting

## License

MIT
