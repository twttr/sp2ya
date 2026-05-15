import argparse
import csv
import sys
from collections import defaultdict

from thefuzz import fuzz

from common import artist_tokens, latin_normalize, load_json, loose_title, normalize

UNIFIED = "unified_likes.json"
YT_LIKES = "youtube_likes.json"
PROGRESS = "youtube_transfer_progress.json"


def artist_match_score(a, b):
    return max(
        fuzz.token_set_ratio(normalize(a or ""), normalize(b or "")),
        fuzz.token_set_ratio(latin_normalize(a or ""), latin_normalize(b or "")),
    )


def title_match_score(a, b):
    return max(
        fuzz.token_set_ratio(normalize(a or ""), normalize(b or "")),
        fuzz.token_set_ratio(latin_normalize(a or ""), latin_normalize(b or "")),
    )


def find_suspicious_matches(unified, yt_likes, progress, artist_threshold):
    yt_by_id = {t.get("video_id"): t for t in yt_likes}
    unified_by_id = {t.get("id"): t for t in unified}

    suspicious = []
    for unified_id, entry in progress.items():
        if not entry.get("liked"):
            continue
        vid = entry.get("video_id")
        if not vid:
            continue
        yt_track = yt_by_id.get(vid)
        if not yt_track:
            continue
        u = unified_by_id.get(unified_id)
        if not u:
            continue
        artist_score = artist_match_score(u.get("artists", ""), yt_track.get("artists", ""))
        if artist_score < artist_threshold:
            title_score = title_match_score(u.get("name", ""), yt_track.get("name", ""))
            suspicious.append({
                "unified_artist": u.get("artists", ""),
                "unified_name": u.get("name", ""),
                "yt_artist": yt_track.get("artists", ""),
                "yt_name": yt_track.get("name", ""),
                "artist_score": artist_score,
                "title_score": title_score,
                "video_id": vid,
                "source": u.get("source", ""),
            })

    suspicious.sort(key=lambda r: (r["artist_score"], r["title_score"]))
    return suspicious


def find_duplicates(yt_likes, title_threshold=88):
    by_first_artist = defaultdict(list)
    for t in yt_likes:
        artists = t.get("artists", "")
        first = (artists or "").split(",")[0].strip()
        for key in {normalize(first), latin_normalize(first)}:
            if key:
                by_first_artist[key].append(t)

    grouped_ids = set()
    groups = []

    for artist_key, tracks in by_first_artist.items():
        if len(tracks) < 2:
            continue
        for i, t1 in enumerate(tracks):
            id1 = t1.get("video_id")
            if id1 in grouped_ids:
                continue
            cluster = [t1]
            for t2 in tracks[i + 1:]:
                id2 = t2.get("video_id")
                if id2 in grouped_ids or id2 == id1:
                    continue
                n1, n2 = t1.get("name", ""), t2.get("name", "")
                score = max(
                    fuzz.token_sort_ratio(normalize(n1), normalize(n2)),
                    fuzz.token_sort_ratio(latin_normalize(n1), latin_normalize(n2)),
                )
                if score >= title_threshold:
                    cluster.append(t2)
            if len(cluster) >= 2:
                for t in cluster:
                    grouped_ids.add(t.get("video_id"))
                groups.append(cluster)

    groups.sort(key=lambda g: -len(g))
    return groups


def write_suspicious_csv(rows, path):
    fieldnames = ["source", "unified_artist", "unified_name", "yt_artist", "yt_name", "artist_score", "title_score", "video_id"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def write_duplicates_csv(groups, path):
    fieldnames = ["group", "artists", "name", "album", "video_id", "duration_seconds"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for i, group in enumerate(groups, 1):
            for t in group:
                row = dict(t)
                row["group"] = i
                w.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="Audit YouTube Music library: suspicious matches and duplicates.")
    parser.add_argument("--artist-threshold", type=int, default=40, help="Flag migrations where YT artist match score < this (default: 40)")
    parser.add_argument("--dup-title-threshold", type=int, default=88, help="Title fuzz score required to flag duplicates within same first-artist group (default: 88)")
    parser.add_argument("--max-print", type=int, default=40)
    args = parser.parse_args()

    unified = load_json(UNIFIED) or []
    yt_likes = load_json(YT_LIKES) or []
    progress = load_json(PROGRESS) or {}
    if not (unified and yt_likes and progress):
        print("Missing one of unified_likes.json / youtube_likes.json / youtube_transfer_progress.json")
        sys.exit(1)

    print(f"=== Suspicious matches (low artist similarity, likely title-only fallback hits) ===")
    suspicious = find_suspicious_matches(unified, yt_likes, progress, args.artist_threshold)
    print(f"  Found {len(suspicious)} suspicious matches (artist_score < {args.artist_threshold}).\n")
    for r in suspicious[:args.max_print]:
        print(f"  [{r['source'][:3]}] a={r['artist_score']:3d} t={r['title_score']:3d}")
        print(f"        UNIFIED: {r['unified_artist'][:45]:45s} - {r['unified_name'][:55]}")
        print(f"        YT:      {r['yt_artist'][:45]:45s} - {r['yt_name'][:55]}")
    if len(suspicious) > args.max_print:
        print(f"  ... and {len(suspicious) - args.max_print} more.")
    write_suspicious_csv(suspicious, "audit_suspicious_matches.csv")

    print(f"\n=== Duplicates in YouTube Music Liked Music ===")
    groups = find_duplicates(yt_likes, title_threshold=args.dup_title_threshold)
    total_extra = sum(len(g) - 1 for g in groups)
    print(f"  {len(groups)} duplicate groups, {total_extra} extra entries that could be unliked.\n")
    for i, g in enumerate(groups[:args.max_print], 1):
        print(f"  Group {i} ({len(g)} entries):")
        for t in g:
            print(f"    {t['artists'][:42]:42s} - {t['name'][:55]}  [{t.get('video_id')}]")
    if len(groups) > args.max_print:
        print(f"  ... and {len(groups) - args.max_print} more groups.")
    write_duplicates_csv(groups, "audit_duplicates.csv")

    print(f"\nFull lists: audit_suspicious_matches.csv ({len(suspicious)}), audit_duplicates.csv")


if __name__ == "__main__":
    main()
