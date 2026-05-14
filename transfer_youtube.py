import argparse
import csv
import os
import sys
import time

from dotenv import load_dotenv
from thefuzz import fuzz
from ytmusicapi import YTMusic, OAuthCredentials

from common import latin_normalize, load_json, normalize, save_json

UNIFIED_CACHE_PATH = "unified_likes.json"
PROGRESS_PATH = "youtube_transfer_progress.json"
FAILURES_PATH = "youtube_failed_matches.csv"
BROWSER_PATH = "browser.json"
OAUTH_PATH = "oauth.json"

DEFAULT_FUZZY_THRESHOLD = 75

SETUP_INSTRUCTIONS = """
YouTube Music setup (browser auth — recommended)
================================================

Google disabled OAuth for many third-party YT Music clients in late 2024;
browser auth is the working path across most projects (yt-dlp, ytmusicapi, etc).

1. Open https://music.youtube.com in your browser (use a private/incognito
   window so the cookies don't get rotated by normal browsing) and sign in.

2. Open dev tools (F12) -> Network tab.

3. In the YT Music tab, click around / search anything to generate requests.
   Find a POST request to `/youtubei/v1/...` (e.g. `browse`, `next`, `search`).

4. Right-click that request -> Copy -> Copy request headers (Firefox) or
   Copy as cURL (Chrome -- ytmusicapi can parse either).

5. Run:
       ytmusicapi browser
   Paste the headers when prompted, press Enter on an empty line to finish.
   This writes `browser.json` in the current directory.

6. Run the transfer:
       python transfer_youtube.py

Browser cookies typically last weeks-to-months. If they expire, repeat steps 1-5.
""".strip()


def get_youtube_client():
    if os.path.exists(BROWSER_PATH):
        return YTMusic(BROWSER_PATH)

    if os.path.exists(OAUTH_PATH):
        client_id = os.getenv("YT_CLIENT_ID")
        client_secret = os.getenv("YT_CLIENT_SECRET")
        if not client_id or not client_secret:
            print("YT_CLIENT_ID / YT_CLIENT_SECRET are not set in .env\n")
            print(SETUP_INSTRUCTIONS)
            sys.exit(1)
        return YTMusic(
            OAUTH_PATH,
            oauth_credentials=OAuthCredentials(
                client_id=client_id, client_secret=client_secret
            ),
        )

    print(f"Neither {BROWSER_PATH} nor {OAUTH_PATH} found.\n")
    print(SETUP_INSTRUCTIONS)
    sys.exit(1)


def load_progress():
    return load_json(PROGRESS_PATH, default={}) or {}


def save_progress(progress):
    save_json(PROGRESS_PATH, progress)


def write_failures_csv(failed_tracks):
    fieldnames = ["source", "artists", "name", "album", "id", "added_at"]
    with open(FAILURES_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(failed_tracks)
    print(f"\nFailed matches written to {FAILURES_PATH}")


def score_candidate(target_artist, target_title, candidate):
    title = candidate.get("title") or ""
    artists_list = candidate.get("artists") or []
    artist_name = artists_list[0]["name"] if artists_list else ""

    artist_score = max(
        fuzz.token_sort_ratio(normalize(target_artist), normalize(artist_name)),
        fuzz.token_sort_ratio(latin_normalize(target_artist), latin_normalize(artist_name)),
    )
    title_score = max(
        fuzz.token_sort_ratio(normalize(target_title), normalize(title)),
        fuzz.token_sort_ratio(latin_normalize(target_title), latin_normalize(title)),
    )
    return (artist_score + title_score) / 2


def best_match(results, target_artist, target_title, threshold):
    if not results:
        return None, 0
    best_id = None
    best_score = 0
    for r in results:
        video_id = r.get("videoId")
        if not video_id:
            continue
        score = score_candidate(target_artist, target_title, r)
        if score > best_score:
            best_score = score
            best_id = video_id
    if best_score >= threshold:
        return best_id, best_score
    return None, best_score


def best_title_only_match(results, target_title, threshold):
    norm_target = normalize(target_title)
    latin_target = latin_normalize(target_title)
    if len(norm_target.split()) < 3 and len(latin_target.split()) < 3:
        return None
    best_id = None
    best_score = 0
    for r in results:
        video_id = r.get("videoId")
        if not video_id:
            continue
        candidate_raw = r.get("title") or ""
        for target, candidate in (
            (norm_target, normalize(candidate_raw)),
            (latin_target, latin_normalize(candidate_raw)),
        ):
            set_score = fuzz.token_set_ratio(target, candidate)
            sort_score = fuzz.token_sort_ratio(target, candidate)
            score = min(set_score, sort_score + 25)
            if score > best_score:
                best_score = score
                best_id = video_id
    return best_id if best_score >= threshold else None


def find_track_on_youtube(yt, track_info, threshold, title_only_threshold=None):
    first_artist = (track_info.get("artists") or "").split(",")[0].strip()
    name = track_info.get("name") or ""
    if not first_artist and not name:
        return None

    query = f"{first_artist} - {name}".strip(" -")

    try:
        results = yt.search(query, filter="songs", limit=5)
    except Exception:
        results = []
    match_id, _ = best_match(results, first_artist, name, threshold)
    if match_id:
        return match_id

    try:
        videos_results = yt.search(query, filter="videos", limit=5)
    except Exception:
        videos_results = []
    match_id, _ = best_match(videos_results, first_artist, name, threshold)
    if match_id:
        return match_id

    if title_only_threshold is None:
        return None

    try:
        title_results = yt.search(name, filter="videos", limit=10)
    except Exception:
        title_results = []
    match_id = best_title_only_match(title_results, name, title_only_threshold)
    if match_id:
        return match_id

    try:
        title_results = yt.search(name, filter="songs", limit=10)
    except Exception:
        title_results = []
    return best_title_only_match(title_results, name, title_only_threshold)


def transfer_to_youtube(yt, tracks, dry_run, threshold, title_only_threshold=None):
    progress = load_progress()
    if progress:
        liked = sum(1 for v in progress.values() if v.get("liked"))
        pending = sum(1 for v in progress.values() if v.get("video_id") and not v.get("liked"))
        print(f"Found progress: {liked} liked, {pending} matched but not yet liked.\n")

    pending_like = [
        (tid, entry["video_id"])
        for tid, entry in progress.items()
        if entry.get("video_id") and not entry.get("liked")
    ]
    if not dry_run and pending_like:
        print(f"Retrying {len(pending_like)} previously matched but unliked tracks...")
        for i, (tid, video_id) in enumerate(pending_like, 1):
            try:
                yt.rate_song(video_id, "LIKE")
                progress[tid]["liked"] = True
            except Exception as e:
                print(f"  >> Warning: like failed for {video_id}: {e}")
            if i % 10 == 0:
                save_progress(progress)
                print(f"  >> Liked {i} / {len(pending_like)}")
                time.sleep(0.5)
        save_progress(progress)

    new_matched = 0
    new_liked = 0
    skipped = 0
    failed = []

    for i, track in enumerate(tracks, 1):
        tid = track["id"]
        label = f"{track['artists']} - {track['name']}"

        existing = progress.get(tid)
        if existing and existing.get("liked"):
            skipped += 1
            continue
        if existing and existing.get("video_id") and not existing.get("liked"):
            continue

        print(f"[{i}/{len(tracks)}] {label}", end=" ")

        try:
            video_id = find_track_on_youtube(yt, track, threshold, title_only_threshold)
        except Exception as e:
            print(f"-> ERROR ({e})")
            failed.append(track)
            time.sleep(1)
            continue

        if not video_id:
            print("-> NOT FOUND")
            failed.append(track)
            progress[tid] = {"video_id": None, "liked": False}
        else:
            print(f"-> MATCHED ({video_id})")
            new_matched += 1
            progress[tid] = {"video_id": video_id, "liked": False}

            if not dry_run:
                try:
                    yt.rate_song(video_id, "LIKE")
                    progress[tid]["liked"] = True
                    new_liked += 1
                except Exception as e:
                    print(f"  >> Warning: like failed: {e}")

        if i % 20 == 0:
            save_progress(progress)
            time.sleep(0.5)
        else:
            time.sleep(0.15)

    save_progress(progress)

    print(f"\nDone!")
    print(f"  New matched:        {new_matched}")
    print(f"  New liked:          {new_liked}")
    print(f"  Already done:       {skipped}")
    print(f"  Failed to match:    {len(failed)}")
    print(f"  Total in unified:   {len(tracks)}")

    if dry_run:
        print("\n(Dry run — nothing was liked on YouTube Music)")

    if failed:
        write_failures_csv(failed)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Transfer the unified likes list to YouTube Music.",
        epilog=SETUP_INSTRUCTIONS,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        default=UNIFIED_CACHE_PATH,
        help=f"Unified tracks JSON (default: {UNIFIED_CACHE_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Match tracks but don't like them on YouTube Music",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=DEFAULT_FUZZY_THRESHOLD,
        help=f"Minimum fuzzy match score 0-100 (default: {DEFAULT_FUZZY_THRESHOLD})",
    )
    parser.add_argument(
        "--title-only-threshold",
        type=int,
        default=None,
        help="Enable title-only fallback (artist ignored) when strict matching fails, with this token_set_ratio threshold (e.g. 80). Requires target title to have ≥3 normalized words. Off by default — recommended only for retrying failures (false-positive risk is higher than strict mode).",
    )
    args = parser.parse_args()

    tracks = load_json(args.input)
    if not tracks:
        print(f"No tracks found at {args.input}. Run `python enrich.py --spotify-csv <path>` first.")
        sys.exit(1)
    print(f"Loaded {len(tracks)} tracks from {args.input}.\n")

    yt = get_youtube_client()
    transfer_to_youtube(
        yt,
        tracks,
        dry_run=args.dry_run,
        threshold=args.fuzzy_threshold,
        title_only_threshold=args.title_only_threshold,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Partial progress saved.")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
