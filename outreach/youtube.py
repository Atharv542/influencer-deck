
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)
BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeError(Exception):
    pass


class QuotaExceeded(YouTubeError):
    pass


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class YouTubeClient:
    def __init__(self, api_key: str, timeout: int = 20, retries: int = 3, session=None):
        if not api_key:
            raise YouTubeError("YOUTUBE_API_KEY is missing. Add it to your .env file.")
        self.api_key, self.timeout, self.retries = api_key, timeout, retries
        self.session = session or requests.Session()

    def _get(self, endpoint: str, **params) -> dict:
        params["key"] = self.api_key
        last = ""
        for attempt in range(self.retries):
            try:
                r = self.session.get(f"{BASE}/{endpoint}", params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last = str(exc)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 403 and "quota" in r.text.lower():
                raise QuotaExceeded("YouTube API daily quota exhausted (resets midnight Pacific time).")
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"{r.status_code}"
                time.sleep(2 ** attempt)
                continue
            raise YouTubeError(f"{endpoint} failed: {r.status_code} {r.text[:300]}")
        raise YouTubeError(f"{endpoint} failed after {self.retries} retries ({last})")

    # ---- discovery -----------------------------------------------------
    def search_channel_ids(self, query: str, kind: str = "channel", pages: int = 2,
                           region: str | None = None, lang: str | None = None) -> list[str]:
        """kind='channel' searches channels directly; kind='video' harvests channels behind top videos."""
        params = dict(part="snippet", q=query, type=kind, maxResults=50)
        if region:
            params["regionCode"] = region
        if lang:
            params["relevanceLanguage"] = lang
        ids: list[str] = []
        for _ in range(pages):
            data = self._get("search", **params)
            for it in data.get("items", []):
                cid = it.get("snippet", {}).get("channelId") or it.get("id", {}).get("channelId")
                if cid and cid not in ids:
                    ids.append(cid)
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return ids

    def get_channels(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            data = self._get("channels", part="snippet,statistics,contentDetails,brandingSettings",
                             id=",".join(batch), maxResults=50)
            out.extend(data.get("items", []))
        return out

    def get_recent_videos(self, uploads_playlist: str, n: int = 10) -> list[dict]:
        pl = self._get("playlistItems", part="contentDetails", playlistId=uploads_playlist,
                       maxResults=min(n, 50))
        vids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        if not vids:
            return []
        data = self._get("videos", part="snippet,statistics", id=",".join(vids))
        videos = []
        for v in data.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            videos.append({
                "id": v["id"],
                "title": sn.get("title", ""),
                "description": (sn.get("description") or "")[:1500],
                "tags": sn.get("tags", [])[:25],
                "published_at": sn.get("publishedAt"),
                "views": _int(st.get("viewCount")),
                "likes": _int(st.get("likeCount")),       
                "comments": _int(st.get("commentCount")), 
            })
        videos.sort(key=lambda x: x["published_at"] or "", reverse=True)
        return videos
