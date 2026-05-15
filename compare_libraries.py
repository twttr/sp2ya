import argparse
import csv
import sys

from dotenv import load_dotenv
from ytmusicapi import YTMusic

from common import artist_tokens, latin_normalize, load_json, loose_title, normalize, save_json

YT_LIKES_CACHE = "youtube_likes.json"


def get_youtube_client():
    return YTMusic("browser.json")


def fetch_yt_liked_songs(yt):
    print("Fetching YouTube Music liked songs...")
    data = yt.get_liked_songs(limit=50000)
    tracks = []
    for t in data.get("tracks", []):
        artists = ", ".join((a or {}).get("name", "") for a in (t.get("artists") or []) if a)
        tracks.append({
            "name": t.get("title") or "",
            "artists": artists,
            "video_id": t.get("videoId"),
            "album": (t.get("album") or {}).get("name") if t.get("album") else "",
            "duration_seconds": t.get("duration_seconds") or 0,
        })
    print(f"  Fetched {len(tracks)} tracks.")
    return tracks


def normalized_artist_tokens(artists):
    base = artist_tokens(artists)
    latin = artist_tokens(latin_normalize(artists or ""))
    return base | latin


def normalized_titles(name):
    return {loose_title(name), latin_normalize(loose_title(name))}


def build_index(tracks):
    index = {}
    for t in tracks:
        tokens = normalized_artist_tokens(t.get("artists", ""))
        for title in normalized_titles(t.get("name", "")):
            index.setdefault(title, []).append((tokens, t))
    return index


def find_match(track, index):
    tokens = normalized_artist_tokens(track.get("artists", ""))
    for title in normalized_titles(track.get("name", "")):
        for cand_tokens, cand_track in index.get(title, []):
            if tokens & cand_tokens:
                return cand_track
    return None


def write_csv(path, tracks, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(tracks)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Compare current YouTube Music liked songs against unified_likes.json."
    )
    parser.add_argument("--unified", default="unified_likes.json")
    parser.add_argument(
        "--refresh-yt",
        action="store_true",
        help="Re-fetch YT Music likes (default: reuse youtube_likes.json if present)",
    )
    parser.add_argument(
        "--max-print",
        type=int,
        default=30,
        help="Max items to print per category (full lists go to CSV).",
    )
    args = parser.parse_args()

    unified = load_json(args.unified) or []
    if not unified:
        print(f"No tracks in {args.unified}.")
        sys.exit(1)

    cached_yt = None if args.refresh_yt else load_json(YT_LIKES_CACHE)
    if cached_yt:
        print(f"Using cached YT Music likes ({len(cached_yt)} tracks). Use --refresh-yt to re-fetch.")
        yt_likes = cached_yt
    else:
        yt = get_youtube_client()
        yt_likes = fetch_yt_liked_songs(yt)
        save_json(YT_LIKES_CACHE, yt_likes)
        print(f"  Saved YT likes to {YT_LIKES_CACHE}")

    unified_index = build_index(unified)
    yt_index = build_index(yt_likes)

    only_in_unified = [t for t in unified if not find_match(t, yt_index)]
    only_in_yt = [t for t in yt_likes if not find_match(t, unified_index)]

    print(f"\nSummary:")
    print(f"  unified_likes.json:           {len(unified)}")
    print(f"  YT Music liked songs:         {len(yt_likes)}")
    print(f"  In unified, not in YT:        {len(only_in_unified)}  (failed to migrate or unliked since)")
    print(f"  In YT, not in unified:        {len(only_in_yt)}  (added on YT directly, or our matcher picked a different version)")

    by_source = {}
    for t in only_in_unified:
        by_source.setdefault(t.get("source", "?"), 0)
        by_source[t.get("source", "?")] += 1
    if by_source:
        print(f"\n  Breakdown of 'in unified, not in YT' by source:")
        for s, n in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {s}: {n}")

    print(f"\n--- In unified, not in YT (first {args.max_print}) ---")
    for t in only_in_unified[:args.max_print]:
        src = (t.get("source") or "?")[:3]
        print(f"  [{src}] {t['artists'][:42]:42s} - {t['name']}")

    print(f"\n--- In YT, not in unified (first {args.max_print}) ---")
    for t in only_in_yt[:args.max_print]:
        print(f"  {t['artists'][:42]:42s} - {t['name']}")

    write_csv("compare_only_in_unified.csv", only_in_unified, ["source", "artists", "name", "album", "id", "added_at"])
    write_csv("compare_only_in_yt.csv", only_in_yt, ["artists", "name", "album", "video_id", "duration_seconds"])
    print(f"\nFull lists written to compare_only_in_unified.csv ({len(only_in_unified)}) and compare_only_in_yt.csv ({len(only_in_yt)}).")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
