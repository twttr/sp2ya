# sp2ya

Transfer liked songs from Spotify to Yandex Music.

Fetches your Spotify liked songs library, matches each track on Yandex Music using exact and fuzzy search, and adds them to your Yandex Music favorites.

## Features

- Two-pass matching: exact search, then fuzzy matching with configurable threshold
- Resumable transfers with progress tracking — safe to interrupt and restart
- Spotify tracks cached locally to avoid repeated API calls
- Unmatched tracks logged to CSV for manual review
- Dry-run mode for testing without side effects

## Prerequisites

- Python 3.8+
- A [Spotify Developer](https://developer.spotify.com/dashboard) account
- A [Yandex Music](https://music.yandex.com) account

## Setup

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Spotify

1. Go to https://developer.spotify.com/dashboard
2. Click **Create App**
3. Set the redirect URI to `http://127.0.0.1:8888/callback`
4. Check **Web API** under "Which API/SDKs are you planning to use?"
5. Copy the **Client ID**

> **Note:** Your Spotify app starts in Development Mode. You need to add your Spotify account email under **Settings > User Management** in the dashboard.

### 3. Configure Yandex Music

1. Install the token browser extension: https://github.com/MarshalX/yandex-music-token
2. Log in to Yandex Music in your browser
3. The extension will display your OAuth token — copy it

> **Note:** Yandex Music has no official public API. This tool uses the community-maintained [yandex-music](https://github.com/MarshalX/yandex-music-api) library. Tokens may expire after ~90 days.

### 4. Create `.env` file

```
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
YANDEX_MUSIC_TOKEN=your_yandex_token_here
```

## Usage

```bash
# Full transfer
python transfer.py

# Test matching without liking anything
python transfer.py --dry-run

# Re-fetch tracks from Spotify (ignores cache)
python transfer.py --refresh

# Stricter matching (default: 75)
python transfer.py --fuzzy-threshold 85
```

On first run, a browser window opens for Spotify authorization. After that, the token is cached.

## Resuming interrupted transfers

The script saves progress to `transfer_progress.json` after each batch. If the transfer is interrupted (crash, network error, Ctrl+C), just run `python transfer.py` again — it will skip already-processed tracks and continue where it left off.

## Output files

| File | Description |
|---|---|
| `spotify_likes.json` | Cached Spotify liked tracks (use `--refresh` to update) |
| `transfer_progress.json` | Tracks already matched and liked on Yandex Music |
| `failed_matches.csv` | Tracks that could not be found on Yandex Music |

## How matching works

1. **Exact search**: queries Yandex Music with `"Artist - Track Name"` and takes the first result
2. **Fuzzy search**: normalizes strings (strips feat., brackets, punctuation), searches again, and compares the top 5 results using token-sorted fuzzy matching on artist and title. Accepts matches scoring above the threshold (default 75/100)

## Limitations

- Some tracks may not exist on Yandex Music due to licensing differences
- Artists with different names across platforms (e.g. Latin vs Cyrillic) may not match automatically — these are logged to `failed_matches.csv`
- Yandex Music token expires periodically (~90 days) and must be re-extracted
- Large libraries (5000+ tracks) take a while due to rate limiting

## License

MIT
