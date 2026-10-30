
from __future__ import annotations

import logging
from collections import Counter

from .analysis import build_text, extract_themes, score_keywords

log = logging.getLogger(__name__)


def evaluate(infl: dict, cfg: dict) -> dict:
    f = cfg["filters"]
    reasons: list[str] = []
    notes: list[str] = []


    followers = infl.get("followers")
    in_range = False
    if followers is None:
        reasons.append("follower count hidden by creator")
    elif followers < f["min_followers"]:
        reasons.append(f"followers {followers:,} below minimum {f['min_followers']:,}")
    elif followers > f["max_followers"]:
        reasons.append(f"followers {followers:,} above micro-influencer maximum {f['max_followers']:,}")
    else:
        in_range = True


    scores = score_keywords(build_text(infl), cfg["keywords"])
    targets = f.get("target_subniches") or list(scores)
    niche_score = sum(scores.get(k, 0) for k in targets)
    primary = max(scores, key=scores.get) if any(scores.values()) else "unclassified"

    videos = infl.get("recent_videos") or []
    if in_range:
        if not videos:
            reasons.append("no recent video data available")
        if niche_score < f["min_niche_score"]:
            reasons.append(f"weak niche relevance (score {niche_score} < {f['min_niche_score']})")
        elif primary not in targets:
            reasons.append(f"primary niche '{primary}' outside target categories")

    
        er = infl.get("engagement_rate")
        if videos:
            if er is None:
                reasons.append("engagement unavailable (likes hidden or fewer than "
                               f"{f.get('min_videos_for_engagement', 3)} usable videos)")
            elif er < f["min_engagement_rate"]:
                reasons.append(f"engagement {er:.2f}% below minimum {f['min_engagement_rate']}%")
            else:
                notes.append(f"engagement {er:.2f}% meets threshold")

        
        days = infl.get("last_upload_days")
        if videos and days is not None and days > f["max_days_since_last_upload"]:
            reasons.append(f"inactive: last upload {days} days ago (max {f['max_days_since_last_upload']})")

    
    text = build_text(infl)
    for term in f.get("blocked_keywords", []):
        if term.lower() in text:
            reasons.append(f"brand-fit: contains blocked term '{term}'")
            break

    
    allowed = [c.upper() for c in f.get("allowed_countries", [])]
    country = (infl.get("country") or "").upper()
    if allowed:
        if not country:
            notes.append("country not listed on channel - geography unverified")
        elif country not in allowed:
            reasons.append(f"country {country} not in allowed list {allowed}")

    return {
        "status": "FAIL" if reasons else "PASS",
        "fail_reasons": reasons,
        "notes": notes,
        "primary_niche": primary.replace("_", " "),
        "niche_score": niche_score,
        "themes": extract_themes(infl, scores),
    }


def reason_group(reason: str) -> str:
    for prefix, group in (("followers", "follower range"), ("follower count", "follower range"),
                          ("weak niche", "niche relevance"), ("primary niche", "niche relevance"),
                          ("engagement", "engagement"), ("inactive", "inactivity"),
                          ("brand-fit", "brand fit"), ("country", "geography"),
                          ("no recent", "no video data")):
        if reason.startswith(prefix):
            return group
    return "other"


def run_filtering(cfg: dict, db) -> dict:
    rows = db.all("influencers")
    reason_hist: Counter = Counter()
    passed = 0
    for r in rows:
        res = evaluate(r, cfg)
        db.upsert("influencers", {"channel_id": r["channel_id"], **res}, "channel_id")
        if res["status"] == "PASS":
            passed += 1
        for reason in res["fail_reasons"]:
            reason_hist[reason_group(reason)] += 1
    log.info("Filtering: %d evaluated, %d PASS, %d FAIL", len(rows), passed, len(rows) - passed)
    return {"evaluated": len(rows), "passed": passed, "failed": len(rows) - passed,
            "fail_reason_groups": dict(reason_hist.most_common())}
