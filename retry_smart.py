import argparse
import csv
import re
import sys
import time

from dotenv import load_dotenv

from transfer_youtube import (
    DEFAULT_FUZZY_THRESHOLD,
    FAILURES_PATH,
    find_track_on_youtube,
    get_youtube_client,
    load_progress,
    save_progress,
    write_failures_csv,
)


def rewrite_title(name):
    if not name:
        return None
    cleaned = name.replace("—", "-")
    cleaned = re.sub(r"\.(mp3|wav|m4a|flac)$", "", cleaned, flags=re.IGNORECASE).strip()
    parts = re.split(r"\s+-\s+", cleaned, maxsplit=1)
    if len(parts) != 2:
        return None
    artist, title = parts[0].strip(), parts[1].strip()
    if not artist or not title or len(artist) > 80 or len(title) > 120:
        return None
    return artist, title


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Retry failed YT Music matches by parsing 'Artist - Title' patterns embedded in the track name (common for SoundCloud re-uploads)."
    )
    parser.add_argument(
        "--input",
        default=FAILURES_PATH,
        help=f"Failures CSV to retry (default: {FAILURES_PATH}). Will be overwritten with the still-failed subset.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Match only; don't like on YouTube Music.",
    )
    parser.add_argument(
        "--fuzzy-threshold",
        type=int,
        default=DEFAULT_FUZZY_THRESHOLD,
        help=f"Minimum fuzzy match score (default: {DEFAULT_FUZZY_THRESHOLD})",
    )
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Loaded {len(rows)} failed tracks from {args.input}.\n")

    yt = get_youtube_client()
    progress = load_progress()

    recovered = 0
    unparseable = 0
    still_failed = []

    for i, row in enumerate(rows, 1):
        original_name = row.get("name", "")
        original_artist = row.get("artists", "")
        parsed = rewrite_title(original_name)
        if not parsed:
            unparseable += 1
            still_failed.append(row)
            continue

        new_artist, new_title = parsed
        rewritten = {"artists": new_artist, "name": new_title}

        print(f"[{i}/{len(rows)}] {original_artist} - {original_name}")
        print(f"    => {new_artist} - {new_title}", end=" ")

        try:
            video_id = find_track_on_youtube(yt, rewritten, args.fuzzy_threshold)
        except Exception as e:
            print(f"-> ERROR ({e})")
            still_failed.append(row)
            time.sleep(1)
            continue

        if not video_id:
            print("-> NOT FOUND")
            still_failed.append(row)
            time.sleep(0.2)
            continue

        print(f"-> MATCHED ({video_id})")
        recovered += 1
        tid = row.get("id") or f"retry:{i}"
        progress[tid] = {"video_id": video_id, "liked": False}

        if not args.dry_run:
            try:
                yt.rate_song(video_id, "LIKE")
                progress[tid]["liked"] = True
            except Exception as e:
                print(f"    Warning: like failed: {e}")

        if i % 10 == 0:
            save_progress(progress)
        time.sleep(0.2)

    save_progress(progress)
    write_failures_csv(still_failed)

    print(f"\nRecovered:                 {recovered}")
    print(f"Unparseable (no ' - '):    {unparseable}")
    print(f"Still failed:              {len(still_failed) - unparseable}")
    print(f"Total still in failures:   {len(still_failed)}")

    if args.dry_run:
        print("\n(Dry run — nothing was liked on YouTube Music)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted. Partial progress saved.")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
