
from __future__ import annotations

import logging
import re
import time
import urllib.robotparser
from collections import Counter
from urllib.parse import urljoin, urlparse

import requests

from .db import now_iso

log = logging.getLogger(__name__)

NOT_FOUND = "Not Found"
NOT_AVAILABLE = "Not Available (public API does not expose it)"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
JUNK_EMAIL_DOMAINS = {"example.com", "domain.com", "email.com", "sentry.io", "wixpress.com", "yourdomain.com",
                      "sentry-next.wixpress.com", "godaddy.com"}
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?", re.I)
IG_RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "about", "directory"}
TT_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]+)", re.I)
NON_WEBSITE_DOMAINS = ("youtube.com", "youtu.be", "instagram.com", "tiktok.com", "facebook.com", "fb.com",
                       "twitter.com", "x.com", "twitch.tv", "pinterest.com", "snapchat.com", "discord.gg",
                       "discord.com", "amzn.to", "amazon.", "bit.ly", "shopmy.us", "ltk.app", "liketk.it",
                       "rstyle.me", "tinyurl.com", "t.co", "spotify.com", "apple.com", "goo.gl", "patreon.com")
NO_SCRAPE_DOMAINS = ("linktr.ee", "beacons.ai", "bio.link", "carrd.co", "lnk.bio", "stan.store", "tap.bio")


def _deobfuscate(text: str) -> str:
    
    text = re.sub(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", "@", text, flags=re.I)
    text = re.sub(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", ".", text, flags=re.I)
    return text


def is_valid_email(e: str | None) -> bool:
    if not e or e == NOT_FOUND:
        return False
    return bool(re.fullmatch(EMAIL_RE.pattern, e)) and not e.lower().endswith(JUNK_SUFFIXES) \
        and e.split("@")[1].lower() not in JUNK_EMAIL_DOMAINS


def extract_emails(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for m in EMAIL_RE.findall(_deobfuscate(text)):
        e = m.strip(".").lower()
        if is_valid_email(e) and e not in seen:
            seen.append(e)
    return seen


def extract_socials(text: str | None) -> dict:
    out = {"instagram_url": None, "instagram_handle": None, "tiktok_url": None, "website": None}
    if not text:
        return out
    for m in IG_RE.finditer(text):
        handle = m.group(1).rstrip(".")
        if handle.lower() not in IG_RESERVED:
            out["instagram_handle"], out["instagram_url"] = handle, f"https://www.instagram.com/{handle}/"
            break
    m = TT_RE.search(text)
    if m:
        out["tiktok_url"] = f"https://www.tiktok.com/@{m.group(1).rstrip('.')}"
    for url in URL_RE.findall(text):
        url = url.rstrip(".,;:!?")
        host = urlparse(url).netloc.lower()
        if host and not any(d in host for d in NON_WEBSITE_DOMAINS):
            out["website"] = url
            break
    return out


def find_email(infl: dict, min_video_hits: int = 2) -> tuple[str, str]:
    
    emails = extract_emails(infl.get("description"))
    if emails:
        return emails[0], "channel description"
    counts: Counter = Counter()
    for v in infl.get("recent_videos") or []:
        for e in set(extract_emails(v.get("description"))):
            counts[e] += 1
    if counts:
        email, n = counts.most_common(1)[0]
        if n >= min_video_hits:
            return email, f"video descriptions (repeated in {n} videos)"
    return NOT_FOUND, "-"


def scrape_site_email(url: str, user_agent: str, timeout: int = 10) -> tuple[str | None, str | None]:
    
    host = urlparse(url).netloc.lower()
    if any(d in host for d in NO_SCRAPE_DOMAINS):
        return None, None
    base = f"{urlparse(url).scheme}://{host}"
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = requests.get(urljoin(base, "/robots.txt"), headers={"User-Agent": user_agent}, timeout=timeout)
        rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
    except requests.RequestException:
        return None, None
    for path in ("", "/contact", "/about"):
        page = urljoin(base, path) if path else url
        if not rp.can_fetch(user_agent, page):
            continue
        try:
            r = requests.get(page, headers={"User-Agent": user_agent}, timeout=timeout)
            if r.status_code != 200 or "text/html" not in r.headers.get("content-type", ""):
                continue
            emails = extract_emails(r.text[:500_000])
            if emails:
                return emails[0], page
        except requests.RequestException:
            continue
        finally:
            time.sleep(1)
    return None, None


def enrich(infl: dict, cfg: dict, scrape: bool = False) -> dict:
    e_cfg = cfg.get("enrichment", {})
    blob = (infl.get("description") or "") + "\n" + "\n".join(
        (v.get("description") or "") for v in infl.get("recent_videos") or [])
    socials = extract_socials(infl.get("description"))
    if not socials["instagram_url"] or not socials["website"]:
        fallback = extract_socials(blob)  # fall back to links repeated in video descriptions
        for k, v in fallback.items():
            socials[k] = socials[k] or v

    email, source = find_email(infl, e_cfg.get("min_video_desc_email_hits", 2))
    if email == NOT_FOUND and scrape and socials["website"]:
        found, page = scrape_site_email(socials["website"], e_cfg.get("user_agent", "InfluencerOutreachBot/1.0"))
        if found:
            email, source = found, f"website: {page}"

    country = infl.get("country")
    return {
        "email": email, "email_source": source, **socials,
        "audience_age": NOT_AVAILABLE,
        "audience_gender": NOT_AVAILABLE,
        "audience_geography": (f"Creator country: {country} (proxy only; audience split not public)"
                               if country else NOT_AVAILABLE),
        "enriched_at": now_iso(),
    }


def run_enrichment(cfg: dict, db, scrape: bool | None = None) -> dict:
    scrape = cfg.get("enrichment", {}).get("scrape_websites", False) if scrape is None else scrape
    rows = db.all("influencers", "status='PASS'")
    found = 0
    for r in rows:
        try:
            data = enrich(r, cfg, scrape)
        except Exception as exc:  # never let one bad record kill the batch
            log.exception("Enrichment failed for %s: %s", r.get("name"), exc)
            data = {"email": NOT_FOUND, "email_source": f"error: {exc}"}
        db.upsert("influencers", {"channel_id": r["channel_id"], **data}, "channel_id")
        found += data["email"] != NOT_FOUND
    log.info("Enrichment: %d shortlisted, %d with email", len(rows), found)
    return {"shortlisted": len(rows), "with_email": found, "email_not_found": len(rows) - found}
