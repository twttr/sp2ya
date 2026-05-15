import argparse
import csv
import glob
import os
import sys
import time

from dotenv import load_dotenv

from common import load_json
from transfer_youtube import (
    DEFAULT_FUZZY_THRESHOLD,
    find_track_on_youtube,
    get_youtube_client,
)

PROGRESS_PATH = "youtube_transfer_progress.json"


def parse_csv(path):
    tracks = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uri = row.get("Track URI") or ""
            if not uri:
                continue
            artists = (row.get("Artist Name(s)") or "").replace(";", ", ")
            tracks.append({
                "spotify_uri": uri,
                "name": row.get("Track Name") or "",
                "artists": artists,
                "album": row.get("Album Name") or "",
            })
    return tracks


def derive_title(csv_path, prefix):
    base = os.path.splitext(os.path.basename(csv_path))[0]
    nice = base.replace("_", " ")
    if prefix:
        return f"{prefix} - {nice}".strip(" -")
    return nice


def resolve_video_id(track, progress, yt, threshold, title_only_threshold):
    entry = progress.get(track["spotify_uri"])
    if entry and entry.get("video_id"):
        return entry["video_id"], "cache"
    vid = find_track_on_youtube(yt, track, threshold, title_only_threshold)
    return vid, ("search" if vid else None)


def process_one(csv_path, yt, progress, args):
    tracks = parse_csv(csv_path)
    title = args.title_template.format(name=derive_title(csv_path, args.prefix))
    print(f"\n=== {title} ===")
    print(f"  Loaded {len(tracks)} tracks from {os.path.basename(csv_path)}")

    video_ids = []
    cache_hits = 0
    search_hits = 0
    not_found = []

    for i, t in enumerate(tracks, 1):
        try:
            vid, source = resolve_video_id(t, progress, yt, args.fuzzy_threshold, args.title_only_threshold)
        except Exception as e:
            print(f"  [{i}/{len(tracks)}] ERROR on {t['artists']} - {t['name']}: {e}")
            not_found.append(t)
            time.sleep(1)
            continue
        if not vid:
            not_found.append(t)
            print(f"  [{i}/{len(tracks)}] NOT FOUND: {t['artists'][:30]} - {t['name'][:40]}")
            continue
        video_ids.append(vid)
        if source == "cache":
            cache_hits += 1
        else:
            search_hits += 1
            time.sleep(0.2)

    print(f"  Resolved: {len(video_ids)} (cache: {cache_hits}, fresh search: {search_hits})")
    print(f"  Not found: {len(not_found)}")

    if args.dry_run:
        print("  (dry-run, not creating playlist)")
        return

    if not video_ids:
        print("  No tracks to add, skipping playlist creation.")
        return

    description = f"Recreated from {os.path.basename(csv_path)} via sp2ya."
    try:
        playlist_id = yt.create_playlist(
            title=title,
            description=description,
            privacy_status=args.privacy,
            video_ids=video_ids,
        )
        if isinstance(playlist_id, dict):
            playlist_id = playlist_id.get("playlistId") or str(playlist_id)
        print(f"  Created playlist: {playlist_id}")
    except Exception as e:
        print(f"  ERROR creating playlist: {e}")


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Recreate Spotify playlist CSVs as YouTube Music playlists.")
    parser.add_argument("csv_paths", nargs="+", help="One or more CSV paths (globs OK)")
    parser.add_argument("--prefix", default="", help="Optional prefix for playlist titles")
    parser.add_argument("--title-template", default="{name}", help="Title template, e.g. '[Spotify] {name}'")
    parser.add_argument("--privacy", choices=("PUBLIC", "PRIVATE", "UNLISTED"), default="PRIVATE")
    parser.add_argument("--dry-run", action="store_true", help="Resolve tracks but don't create playlists.")
    parser.add_argument("--fuzzy-threshold", type=int, default=DEFAULT_FUZZY_THRESHOLD)
    parser.add_argument(
        "--title-only-threshold",
        type=int,
        default=None,
        help="Optional title-only fallback threshold (e.g. 80) for fresh searches.",
    )
    args = parser.parse_args()

    expanded = []
    for p in args.csv_paths:
        matches = sorted(glob.glob(os.path.expanduser(p)))
        if not matches:
            print(f"No files match: {p}", file=sys.stderr)
            continue
        expanded.extend(matches)
    if not expanded:
        print("No CSVs to process.")
        sys.exit(1)

    yt = get_youtube_client()
    progress = load_json(PROGRESS_PATH) or {}
    print(f"Loaded {sum(1 for v in progress.values() if v.get('video_id'))} cached video IDs from {PROGRESS_PATH}.")

    for csv_path in expanded:
        process_one(csv_path, yt, progress, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
