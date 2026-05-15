import argparse
import csv
import sys
from collections import defaultdict

from common import artist_tokens, latin_normalize, load_json, loose_title, save_json


def normalized_artist_tokens(s):
    return artist_tokens(s) | artist_tokens(latin_normalize(s or ""))


def normalized_titles(s):
    base = loose_title(s)
    return {base, latin_normalize(base)}


def build_index(entries):
    index = defaultdict(list)
    for e in entries:
        tokens = normalized_artist_tokens(e.get("artists", ""))
        for title in normalized_titles(e.get("name", "")):
            if title:
                index[title].append((tokens, e))
    return index


def find_match(track, index):
    tokens = normalized_artist_tokens(track.get("artists", ""))
    for title in normalized_titles(track.get("name", "")):
        for cand_tokens, cand_entry in index.get(title, []):
            if tokens & cand_tokens:
                return cand_entry
    return None


def from_youtube(track):
    return {
        "artists": track.get("artists", ""),
        "name": track.get("name", ""),
        "album": track.get("album", ""),
        "duration_seconds": track.get("duration_seconds") or 0,
        "sources": ["youtube"],
        "youtube_video_id": track.get("video_id"),
    }


def from_yandex(track):
    return {
        "artists": track.get("artists", ""),
        "name": track.get("name", ""),
        "album": track.get("album", ""),
        "duration_seconds": int((track.get("duration_ms") or 0) / 1000),
        "sources": ["yandex"],
        "yandex_id": track.get("yandex_id") or track.get("id", "").replace("yandex:track:", ""),
        "added_at": track.get("added_at", ""),
    }


def from_soundcloud(track):
    return {
        "artists": track.get("artists", ""),
        "name": track.get("name", ""),
        "album": track.get("album", ""),
        "duration_seconds": int((track.get("duration_ms") or 0) / 1000),
        "sources": ["soundcloud"],
        "soundcloud_id": track.get("soundcloud_id") or track.get("id", "").replace("soundcloud:track:", ""),
        "soundcloud_url": track.get("soundcloud_url", ""),
        "added_at": track.get("added_at", ""),
    }


def merge_into(canonical, canonical_entry, addition_entry, source_name):
    if source_name not in canonical_entry["sources"]:
        canonical_entry["sources"].append(source_name)
    for k, v in addition_entry.items():
        if k in ("sources", "artists", "name", "album"):
            continue
        if v and not canonical_entry.get(k):
            canonical_entry[k] = v


def main():
    parser = argparse.ArgumentParser(description="Build a canonical deduplicated track list from YouTube Music + Yandex Music + SoundCloud caches.")
    parser.add_argument("--youtube", default="youtube_likes.json")
    parser.add_argument("--yandex", default="yandex_likes.json")
    parser.add_argument("--soundcloud", default="soundcloud_likes.json")
    parser.add_argument("--output", default="canonical_library.json")
    parser.add_argument("--csv", default="canonical_library.csv", help="Also write a CSV view")
    args = parser.parse_args()

    yt = load_json(args.youtube) or []
    ya = load_json(args.yandex) or []
    sc = load_json(args.soundcloud) or []

    print(f"Loaded: youtube={len(yt)}  yandex={len(ya)}  soundcloud={len(sc)}\n")

    canonical = [from_youtube(t) for t in yt]
    index = build_index([{"artists": e["artists"], "name": e["name"], "_ref": e} for e in canonical])

    def reindex():
        nonlocal index
        index = build_index([{"artists": e["artists"], "name": e["name"], "_ref": e} for e in canonical])

    yandex_added = 0
    yandex_merged = 0
    for t in ya:
        match = find_match(t, index)
        ya_entry = from_yandex(t)
        if match:
            merge_into(canonical, match["_ref"], ya_entry, "yandex")
            yandex_merged += 1
        else:
            canonical.append(ya_entry)
            yandex_added += 1
    reindex()

    soundcloud_added = 0
    soundcloud_merged = 0
    for t in sc:
        match = find_match(t, index)
        sc_entry = from_soundcloud(t)
        if match:
            merge_into(canonical, match["_ref"], sc_entry, "soundcloud")
            soundcloud_merged += 1
        else:
            canonical.append(sc_entry)
            soundcloud_added += 1

    print(f"Build summary:")
    print(f"  YouTube (canonical base):       {len(yt)}")
    print(f"  Yandex merged into YT entries:  {yandex_merged}")
    print(f"  Yandex-only added:              {yandex_added}")
    print(f"  SoundCloud merged into entries: {soundcloud_merged}")
    print(f"  SoundCloud-only added:          {soundcloud_added}")
    print(f"  Total canonical tracks:         {len(canonical)}")

    by_n_sources = defaultdict(int)
    for e in canonical:
        by_n_sources[len(e["sources"])] += 1
    print(f"\n  By number of sources:")
    for n in sorted(by_n_sources):
        print(f"    {n} source(s): {by_n_sources[n]}")

    save_json(args.output, canonical)
    print(f"\nWrote {args.output}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["artists", "name", "album", "duration_seconds", "sources", "youtube_video_id", "yandex_id", "soundcloud_url"])
            for e in canonical:
                w.writerow([
                    e.get("artists", ""),
                    e.get("name", ""),
                    e.get("album", ""),
                    e.get("duration_seconds", ""),
                    "|".join(e.get("sources", [])),
                    e.get("youtube_video_id", ""),
                    e.get("yandex_id", ""),
                    e.get("soundcloud_url", ""),
                ])
        print(f"Wrote {args.csv}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
