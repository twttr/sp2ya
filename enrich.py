import argparse
import csv
import os
import sys
import time

import requests
from dotenv import load_dotenv
from yandex_music import Client as YMClient

from common import artist_tokens, load_json, loose_title, save_json

UNIFIED_CACHE_PATH = "unified_likes.json"
YANDEX_CACHE_PATH = "yandex_likes.json"
SOUNDCLOUD_CACHE_PATH = "soundcloud_likes.json"
SOUNDCLOUD_API = "https://api-v2.soundcloud.com"


def get_yandex_client():
    token = os.getenv("YANDEX_MUSIC_TOKEN")
    if not token:
        print("YANDEX_MUSIC_TOKEN is not set in .env")
        sys.exit(1)
    return YMClient(token).init()


def parse_spotify_csv(csv_path):
    tracks = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uri = row.get("Track URI") or ""
            if not uri:
                continue
            artists = (row.get("Artist Name(s)") or "").replace(";", ", ")
            duration_ms = row.get("Duration (ms)") or "0"
            tracks.append({
                "id": uri,
                "name": row.get("Track Name") or "",
                "artists": artists,
                "album": row.get("Album Name") or "",
                "duration_ms": int(duration_ms) if duration_ms.isdigit() else 0,
                "added_at": row.get("Added At") or "",
                "source": "spotify",
            })
    return tracks


def fetch_yandex_liked_tracks(ym):
    print("Fetching liked tracks from Yandex Music...")
    short_list = ym.users_likes_tracks()
    if not short_list or not short_list.tracks:
        print("  No liked tracks found on Yandex.")
        return []

    short_tracks = short_list.tracks
    print(f"  Found {len(short_tracks)} liked tracks on Yandex.")

    track_meta = {}
    track_ids = []
    for t in short_tracks:
        album_part = t.album_id if t.album_id is not None else ""
        tid = f"{t.id}:{album_part}" if album_part != "" else str(t.id)
        track_ids.append(tid)
        track_meta[tid] = {"timestamp": t.timestamp or ""}

    tracks = []
    batch_size = 50
    for start in range(0, len(track_ids), batch_size):
        batch = track_ids[start : start + batch_size]
        try:
            full_batch = ym.tracks(batch)
        except Exception as e:
            print(f"  Warning: batch fetch failed at {start}: {e}")
            time.sleep(2)
            continue
        for tid, full in zip(batch, full_batch):
            if full is None:
                continue
            artists = ", ".join(a.name for a in (full.artists or []) if a and a.name)
            album = ""
            if full.albums:
                album = full.albums[0].title or ""
            tracks.append({
                "id": f"yandex:track:{tid}",
                "name": full.title or "",
                "artists": artists,
                "album": album,
                "duration_ms": full.duration_ms or 0,
                "added_at": track_meta[tid]["timestamp"],
                "source": "yandex",
                "yandex_id": tid,
            })
        print(f"  Fetched details for {min(start + batch_size, len(track_ids))} / {len(track_ids)}")
        time.sleep(0.3)
    return tracks


def fetch_soundcloud_liked_tracks():
    token = os.getenv("SC_OAUTH_TOKEN")
    client_id = os.getenv("SC_CLIENT_ID")
    if not token or not client_id:
        print("SC_OAUTH_TOKEN / SC_CLIENT_ID not set in .env — skipping SoundCloud.")
        return []

    print("Fetching liked tracks from SoundCloud...")
    session = requests.Session()
    session.headers.update({"Authorization": f"OAuth {token}"})

    me = session.get(f"{SOUNDCLOUD_API}/me", params={"client_id": client_id})
    if me.status_code != 200:
        print(f"  Auth failed ({me.status_code}). Refresh SC_OAUTH_TOKEN / SC_CLIENT_ID.")
        return []
    user_id = me.json().get("id")
    if not user_id:
        print("  Could not resolve SoundCloud user id.")
        return []

    url = f"{SOUNDCLOUD_API}/users/{user_id}/track_likes"
    params = {"client_id": client_id, "limit": 200}

    tracks = []
    while url:
        resp = session.get(url, params=params if params else None)
        if resp.status_code != 200:
            print(f"  Request failed ({resp.status_code}); stopping at {len(tracks)} fetched.")
            break
        data = resp.json()
        for item in data.get("collection", []):
            track = item.get("track") or {}
            track_id = track.get("id")
            if not track_id:
                continue
            user = track.get("user") or {}
            tracks.append({
                "id": f"soundcloud:track:{track_id}",
                "name": track.get("title") or "",
                "artists": user.get("username") or "",
                "album": "",
                "duration_ms": track.get("duration") or 0,
                "added_at": item.get("created_at") or "",
                "source": "soundcloud",
                "soundcloud_id": track_id,
                "soundcloud_url": track.get("permalink_url") or "",
            })
        print(f"  Fetched {len(tracks)} tracks...")
        url = data.get("next_href")
        params = {}
        time.sleep(0.3)
    return tracks


def merge_tracks(*source_lists):
    title_index = {}
    unified = []
    stats = {}

    for source_tracks in source_lists:
        for t in source_tracks:
            source = t.get("source", "unknown")
            bucket = stats.setdefault(source, {"added": 0, "dupes": 0})
            title = loose_title(t["name"])
            tokens = artist_tokens(t["artists"])
            if any(tokens & artist_tokens(c["artists"]) for c in title_index.get(title, [])):
                bucket["dupes"] += 1
                continue
            title_index.setdefault(title, []).append(t)
            unified.append(t)
            bucket["added"] += 1

    unified.sort(key=lambda t: t.get("added_at") or "")

    print(f"\nMerge summary:")
    for source, counts in stats.items():
        print(f"  {source:12s} added={counts['added']:5d}  dupes={counts['dupes']:5d}")
    print(f"  Total unified: {len(unified)}")
    return unified


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Build a unified likes list from a Spotify CSV backup and current Yandex Music likes."
    )
    parser.add_argument(
        "--spotify-csv",
        required=True,
        help="Path to Spotify 'Liked Songs' CSV from a backup export",
    )
    parser.add_argument(
        "--refresh-yandex",
        action="store_true",
        help="Re-fetch Yandex likes (default: reuse yandex_likes.json if present)",
    )
    parser.add_argument(
        "--soundcloud",
        action="store_true",
        help="Also fetch likes from SoundCloud (needs SC_OAUTH_TOKEN and SC_CLIENT_ID in .env)",
    )
    parser.add_argument(
        "--refresh-soundcloud",
        action="store_true",
        help="Re-fetch SoundCloud likes (default: reuse soundcloud_likes.json if present)",
    )
    parser.add_argument(
        "--output",
        default=UNIFIED_CACHE_PATH,
        help=f"Output path for the unified list (default: {UNIFIED_CACHE_PATH})",
    )
    args = parser.parse_args()

    if not os.path.exists(args.spotify_csv):
        print(f"Spotify CSV not found: {args.spotify_csv}")
        sys.exit(1)

    print(f"Reading Spotify CSV: {args.spotify_csv}")
    spotify_tracks = parse_spotify_csv(args.spotify_csv)
    print(f"  Loaded {len(spotify_tracks)} tracks from Spotify CSV.\n")

    cached_yandex = None if args.refresh_yandex else load_json(YANDEX_CACHE_PATH)
    if cached_yandex:
        print(f"Using cached Yandex likes ({len(cached_yandex)} tracks). Use --refresh-yandex to re-fetch.")
        yandex_tracks = cached_yandex
    else:
        ym = get_yandex_client()
        yandex_tracks = fetch_yandex_liked_tracks(ym)
        save_json(YANDEX_CACHE_PATH, yandex_tracks)
        print(f"  Saved Yandex likes to {YANDEX_CACHE_PATH}")

    soundcloud_tracks = []
    if args.soundcloud:
        cached_sc = None if args.refresh_soundcloud else load_json(SOUNDCLOUD_CACHE_PATH)
        if cached_sc:
            print(f"Using cached SoundCloud likes ({len(cached_sc)} tracks). Use --refresh-soundcloud to re-fetch.")
            soundcloud_tracks = cached_sc
        else:
            soundcloud_tracks = fetch_soundcloud_liked_tracks()
            if soundcloud_tracks:
                save_json(SOUNDCLOUD_CACHE_PATH, soundcloud_tracks)
                print(f"  Saved SoundCloud likes to {SOUNDCLOUD_CACHE_PATH}")

    unified = merge_tracks(spotify_tracks, yandex_tracks, soundcloud_tracks)
    save_json(args.output, unified)
    print(f"\nWrote {len(unified)} unified tracks to {args.output}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
