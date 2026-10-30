
from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime, timezone

GENERIC_TAGS = {"shorts", "vlog", "youtube", "video", "videos", "subscribe", "new", "2024", "2025", "2026",
                "tutorial", "review", "how to", "diy"}


def parse_ts(s: str | None):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def build_text(infl: dict) -> str:
    parts = [infl.get("name") or "", infl.get("description") or "", infl.get("keywords") or ""]
    for v in infl.get("recent_videos") or []:
        parts += [v.get("title", ""), " ".join(v.get("tags") or []), (v.get("description") or "")[:300]]
    return "\n".join(parts).lower()


def score_keywords(text: str, keyword_map: dict, cap: int = 5) -> dict[str, int]:
    
    scores = {}
    for niche, kws in keyword_map.items():
        total = 0
        for kw in kws:
            pat = r"(?<!\w)" + re.escape(str(kw).lower()) + r"(?!\w)"
            total += min(len(re.findall(pat, text)), cap)
        scores[niche] = total
    return scores


def compute_engagement(videos: list[dict], min_videos: int = 3):
    
    rates = []
    for v in videos:
        views, likes = v.get("views"), v.get("likes")
        if views and views > 0 and likes is not None:
            rates.append((likes + (v.get("comments") or 0)) / views * 100)
    if len(rates) < min_videos:
        return None, len(rates)
    return round(statistics.median(rates), 2), len(rates)


def days_since_last_upload(videos: list[dict], now: datetime | None = None):
    dates = [parse_ts(v.get("published_at")) for v in videos if v.get("published_at")]
    if not dates:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (now - max(dates)).days)


def extract_themes(infl: dict, scores: dict[str, int], max_labels: int = 3, max_tags: int = 3) -> list[str]:
    labels = [k.replace("_", " ") for k, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0][:max_labels]
    tags = Counter()
    for v in infl.get("recent_videos") or []:
        for t in v.get("tags") or []:
            t = t.strip().lower()
            if 2 < len(t) <= 30 and t not in GENERIC_TAGS:
                tags[t] += 1
        for h in re.findall(r"#(\w{3,30})", v.get("title", "")):
            tags[h.lower()] += 1
    return labels + [t for t, _ in tags.most_common(max_tags)]
