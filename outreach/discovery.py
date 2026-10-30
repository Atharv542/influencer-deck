
from __future__ import annotations

import logging

from .analysis import compute_engagement, days_since_last_upload
from .db import now_iso
from .youtube import QuotaExceeded, YouTubeError

log = logging.getLogger(__name__)


def parse_channel(item: dict) -> dict:
    sn, st = item.get("snippet", {}), item.get("statistics", {})
    cd = item.get("contentDetails", {})
    br = item.get("brandingSettings", {}).get("channel", {})
    hidden = st.get("hiddenSubscriberCount", False)
    subs = None if hidden or "subscriberCount" not in st else int(st["subscriberCount"])
    custom = sn.get("customUrl") or ""
    url = (f"https://www.youtube.com/{custom}" if custom.startswith("@")
           else f"https://www.youtube.com/channel/{item['id']}")
    return {
        "channel_id": item["id"],
        "name": sn.get("title", ""),
        "platform": "YouTube",
        "profile_url": url,
        "handle": custom or None,
        "followers": subs,
        "total_views": int(st["viewCount"]) if "viewCount" in st else None,
        "video_count": int(st["videoCount"]) if "videoCount" in st else None,
        "country": sn.get("country") or br.get("country"),
        "description": sn.get("description", ""),
        "keywords": br.get("keywords", ""),
        "uploads_playlist": cd.get("relatedPlaylists", {}).get("uploads"),
        "fetched_at": now_iso(),
    }


def in_micro_range(followers, cfg) -> bool:
    f = cfg["filters"]
    return followers is not None and f["min_followers"] <= followers <= f["max_followers"]


def run_discovery(cfg: dict, db, yt, target: int | None = None) -> dict:
    d, f = cfg["discovery"], cfg["filters"]
    target = target or d["target_in_range"]
    stats = {"queries_run": 0, "channels_seen": 0, "new_channels": 0, "in_range_total": 0, "stopped": "queries exhausted"}

    def in_range_total() -> int:
        return db.count("influencers", "followers BETWEEN ? AND ?", (f["min_followers"], f["max_followers"]))

    try:
        for query in d["queries"]:
            for kind in d.get("search_kinds", ["channel"]):
                if in_range_total() >= target:
                    stats["stopped"] = f"target of {target} in-range channels reached"
                    raise StopIteration
                log.info("Searching %s: %r", kind, query)
                try:
                    ids = yt.search_channel_ids(query, kind, d["pages_per_query"],
                                                d.get("region_code"), d.get("relevance_language"))
                except QuotaExceeded:
                    raise
                except YouTubeError as exc:
                    log.warning("Search failed for %r (%s): %s", query, kind, exc)
                    continue
                stats["queries_run"] += 1
                stats["channels_seen"] += len(ids)
                new_ids = [i for i in ids if not db.exists("influencers", "channel_id", i)]
                if not new_ids:
                    continue
                try:
                    items = yt.get_channels(new_ids)
                except QuotaExceeded:
                    raise
                except YouTubeError as exc:
                    log.warning("Channel lookup failed: %s", exc)
                    continue
                for item in items:
                    rec = parse_channel(item)
                    rec["discovered_via"] = f"youtube:{kind}:{query}"
                    if in_micro_range(rec["followers"], cfg) and rec["uploads_playlist"]:
                        try:
                            videos = yt.get_recent_videos(rec["uploads_playlist"], d["recent_videos"])
                        except QuotaExceeded:
                            raise
                        except YouTubeError as exc:
                            log.warning("Videos failed for %s: %s", rec["name"], exc)
                            videos = []
                        rec["recent_videos"] = videos
                        er, n = compute_engagement(videos, f.get("min_videos_for_engagement", 3))
                        rec.update(engagement_rate=er, engagement_videos=n,
                                   last_upload_days=days_since_last_upload(videos))
                    db.upsert("influencers", rec, "channel_id")
                    stats["new_channels"] += 1
    except StopIteration:
        pass
    except QuotaExceeded as exc:
        stats["stopped"] = f"quota exhausted: {exc}"
        log.error(stats["stopped"])
    stats["in_range_total"] = in_range_total()
    log.info("Discovery finished: %s", stats)
    return stats
