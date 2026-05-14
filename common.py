import json
import os
import re
import unicodedata


def normalize(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower().strip()
    text = re.sub(r"\(feat\..*?\)", "", text)
    text = re.sub(r"\(ft\..*?\)", "", text)
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def loose_title(text):
    if not text:
        return ""
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\s+-\s+.*$", "", text)
    return normalize(text)


def artist_tokens(text):
    if not text:
        return set()
    parts = re.split(
        r"[,&;/]|\s+feat\.?\s+|\s+ft\.?\s+|\s+vs\.?\s+|\s+x\s+",
        text,
        flags=re.IGNORECASE,
    )
    return {normalize(p) for p in parts if normalize(p)}


def dedup_key(artists, name):
    first_artist = (artists or "").split(",")[0].strip()
    return f"{normalize(first_artist)}::{normalize(name)}"


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
