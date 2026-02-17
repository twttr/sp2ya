import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyPKCE
from yandex_music import Client as YMClient
from thefuzz import fuzz

FUZZY_THRESHOLD = 75

SETUP_INSTRUCTIONS = """
SETUP
=====

1. Install dependencies:
   pip install -r requirements.txt

2. Spotify:
   - Go to https://developer.spotify.com/dashboard
   - Click "Create App"
   - App name: anything (e.g. "sp2ya")
   - Redirect URI: http://127.0.0.1:8888/callback
   - Check "Web API" under "Which API/SDKs are you planning to use?"
   - Copy the Client ID into .env as SPOTIFY_CLIENT_ID
   - No client secret needed (we use PKCE flow)

3. Yandex Music:
   - Install browser extension: https://github.com/MarshalX/yandex-music-token
   - Log in to Yandex Music in your browser
   - The extension will show your token — copy it into .env as YANDEX_MUSIC_TOKEN

4. Run:
   python transfer.py              # full transfer
   python transfer.py --dry-run    # match only, don't like on Yandex
""".strip()


def get_spotify_client():
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id:
        print("SPOTIFY_CLIENT_ID is not set in .env\n")
        print(SETUP_INSTRUCTIONS)
        sys.exit(1)

    auth_manager = SpotifyPKCE(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope="user-library-read",
        cache_path=".spotify_cache",
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def get_yandex_client():
    token = os.getenv("YANDEX_MUSIC_TOKEN")
    if not token:
        print("YANDEX_MUSIC_TOKEN is not set in .env\n")
        print(SETUP_INSTRUCTIONS)
        sys.exit(1)

    client = YMClient(token).init()
    return client


def fetch_spotify_liked_tracks(sp):
    tracks = []
    offset = 0
    limit = 50

    while True:
        results = sp.current_user_saved_tracks(limit=limit, offset=offset)
        items = results.get("items", [])
        if not items:
            break

        for item in items:
            track = item["track"]
            if track is None:
                continue
            artists = ", ".join(a["name"] for a in track["artists"])
            tracks.append({
                "name": track["name"],
                "artists": artists,
                "album": track.get("album", {}).get("name", ""),
                "isrc": track.get("external_ids", {}).get("isrc", ""),
                "duration_ms": track.get("duration_ms", 0),
                "spotify_uri": track.get("uri", ""),
            })

        print(f"  Fetched {len(tracks)} tracks...")
        offset += limit

        if not results.get("next"):
            break

    return tracks


CACHE_PATH = "spotify_likes.json"


def save_tracks_cache(tracks):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(tracks)} tracks to {CACHE_PATH}")


def load_tracks_cache():
    if not os.path.exists(CACHE_PATH):
        return None
    with open(CACHE_PATH, "r", encoding="utf-8") as f:
        tracks = json.load(f)
    print(f"  Loaded {len(tracks)} tracks from {CACHE_PATH}")
    return tracks


def normalize(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.lower().strip()
    text = re.sub(r"\(feat\..*?\)", "", text)
    text = re.sub(r"\(ft\..*?\)", "", text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def find_track_on_yandex(ym, track_info):
    artist = track_info["artists"].split(",")[0].strip()
    name = track_info["name"]

    query = f"{artist} - {name}"
    search_result = ym.search(query, type_="track")

    if search_result and search_result.tracks and search_result.tracks.results:
        t = search_result.tracks.results[0]
        album_id = t.albums[0].id if t.albums else None
        return f"{t.id}:{album_id}" if album_id else str(t.id)

    query_normalized = f"{normalize(artist)} {normalize(name)}"
    search_result = ym.search(query_normalized, type_="track")

    if search_result and search_result.tracks and search_result.tracks.results:
        for candidate in search_result.tracks.results[:5]:
            candidate_artist = candidate.artists[0].name if candidate.artists else ""
            candidate_title = candidate.title or ""

            artist_score = fuzz.token_sort_ratio(
                normalize(artist), normalize(candidate_artist)
            )
            title_score = fuzz.token_sort_ratio(
                normalize(name), normalize(candidate_title)
            )

            combined = (artist_score + title_score) / 2
            if combined >= FUZZY_THRESHOLD:
                album_id = candidate.albums[0].id if candidate.albums else None
                return f"{candidate.id}:{album_id}" if album_id else str(candidate.id)

    return None


PROGRESS_PATH = "transfer_progress.json"


def save_progress(matched):
    data = {m["track"]["spotify_uri"]: m["yandex_id"] for m in matched}
    with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_progress():
    if not os.path.exists(PROGRESS_PATH):
        return {}
    with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def write_failures_csv(failed_tracks, path="failed_matches.csv"):
    fieldnames = ["artists", "name", "album", "isrc", "spotify_uri"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(failed_tracks)
    print(f"\nFailed matches written to {path}")


def like_tracks_on_yandex(ym, track_ids):
    uid = ym.me.account.uid
    fetched = ym.tracks(track_ids)
    for t in fetched:
        t.like()


def transfer_likes(sp, ym, dry_run=False, refresh=False):
    cached = None if refresh else load_tracks_cache()
    if cached:
        tracks = cached
        print(f"Using cached Spotify tracks ({len(tracks)} tracks). Use --refresh to re-fetch.\n")
    else:
        print("Fetching liked tracks from Spotify...")
        tracks = fetch_spotify_liked_tracks(sp)
        save_tracks_cache(tracks)
        print(f"Found {len(tracks)} liked tracks on Spotify.\n")

    if not tracks:
        print("No liked tracks found. Nothing to transfer.")
        return [], []

    progress = load_progress()
    if progress:
        print(f"Found progress file with {len(progress)} previously matched tracks.\n")

    all_matched = []
    pending_like = []
    skipped = []
    failed = []
    batch_size = 20

    for i, track in enumerate(tracks, 1):
        label = f"{track['artists']} - {track['name']}"
        uri = track["spotify_uri"]

        if uri in progress:
            skipped.append({"track": track, "yandex_id": progress[uri]})
            continue

        print(f"[{i}/{len(tracks)}] {label}", end=" ")

        try:
            ym_track_id = find_track_on_yandex(ym, track)
        except Exception as e:
            print(f"-> ERROR ({e})")
            failed.append(track)
            continue

        if ym_track_id:
            print("-> MATCHED")
            entry = {"track": track, "yandex_id": ym_track_id}
            all_matched.append(entry)
            pending_like.append(entry)
        else:
            print("-> NOT FOUND")
            failed.append(track)

        if not dry_run and len(pending_like) >= batch_size:
            track_ids = [str(m["yandex_id"]) for m in pending_like]
            try:
                like_tracks_on_yandex(ym, track_ids)
                print(f"  >> Liked {len(track_ids)} tracks on Yandex Music")
            except Exception as e:
                print(f"  >> Warning: like failed: {e}")
            save_progress(skipped + all_matched)
            pending_like = []
            time.sleep(1)

        if i % 10 == 0:
            time.sleep(0.3)

    if not dry_run and pending_like:
        track_ids = [str(m["yandex_id"]) for m in pending_like]
        try:
            like_tracks_on_yandex(ym, track_ids)
            print(f"  >> Liked {len(track_ids)} tracks on Yandex Music")
        except Exception as e:
            print(f"  >> Warning: like failed: {e}")

    if not dry_run:
        save_progress(skipped + all_matched)

    total = len(tracks)
    print(f"\nDone!")
    print(f"  Matched & liked: {len(all_matched)}")
    print(f"  Previously done:  {len(skipped)}")
    print(f"  Failed:           {len(failed)}")
    print(f"  Total:            {total}")

    if dry_run:
        print("\n(Dry run — nothing was liked on Yandex Music)")

    if failed:
        write_failures_csv(failed)

    return all_matched, failed


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Transfer liked songs from Spotify to Yandex Music",
        epilog=SETUP_INSTRUCTIONS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Match tracks but don't like them on Yandex Music",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch tracks from Spotify instead of using cache",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=75,
        help="Minimum fuzzy match score 0-100 (default: 75)",
    )
    args = parser.parse_args()

    global FUZZY_THRESHOLD
    FUZZY_THRESHOLD = args.fuzzy_threshold

    sp = get_spotify_client()
    ym = get_yandex_client()

    transfer_likes(sp, ym, dry_run=args.dry_run, refresh=args.refresh)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Partial results may have been saved.")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
