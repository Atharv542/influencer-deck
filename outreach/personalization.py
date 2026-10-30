
from __future__ import annotations

import hashlib
import json
import logging
import re

from .config import env
from .db import now_iso

log = logging.getLogger(__name__)

EMAIL_WORDS = (60, 90)
DM_WORDS = (15, 30)

SYSTEM_PROMPT = """You write short, genuine, non-spammy influencer outreach for a brand partnerships team.
Rules:
- Use ONLY the facts supplied in the creator signals. Never invent facts, numbers, past collaborations or claims about the creator.
- You may reference video titles as things that "caught your eye"; do not describe their contents beyond the title.
- Sound like a real person: warm, specific, no hype words ("amazing opportunity", "exciting"), no emojis in the email, at most one emoji in the DM.
- Vary your wording; do not open with "I hope this email finds you well".
- Never use placeholders such as [Name]. Sign off with the sender name and brand only.
- Return ONLY a JSON object, no markdown fences."""

USER_PROMPT = """BRAND
{brand}

CREATOR SIGNALS
{signals}

Write:
1. "subject": max 8 words, specific to the creator, not clickbait.
2. "email_body": {elo}-{ehi} words INCLUDING greeting and sign-off. Must (a) greet them using greeting_name, (b) reference their niche/theme and one recent video title, (c) propose the collaboration_angle, (d) state the brand's value_prop, (e) end with a soft call to action.
3. "instagram_dm": {dlo}-{dhi} words, casual, no links, personalised to their content and niche, mentions the brand once.

JSON keys exactly: subject, email_body, instagram_dm.{feedback}"""


ANGLES = {
    "affiliate": "an affiliate partnership",
    "barter": "a gifted-product UGC collaboration",
    "ugc_sponsored": "a UGC and sponsored-content partnership",
    "sponsorship": "a paid sponsorship",
}


def wc(text: str) -> int:
    return len(text.split())


def pick(options: list, key: str):
    return options[int(hashlib.md5(key.encode()).hexdigest(), 16) % len(options)]


def clean_name(title: str) -> str:
    t = re.sub(r"[^\w\s'&.-]", " ", title or "", flags=re.UNICODE)
    t = re.sub(r"\b(official|channel|tv|vlogs?|youtube)\b", "", t, flags=re.I)
    words = t.split()
    if not words:
        return title or "there"
    return " ".join(words[:2]) if len(words) > 3 else " ".join(words)


def clip_title(title: str, n: int = 7) -> str:
    t = re.split(r"[|\u2022]", title or "")[0]
    t = re.sub(r"#\w+", "", t)
    t = re.sub(r"[^\w\s'&,.!?:-]", "", t, flags=re.UNICODE).strip()
    return " ".join(t.split()[:n])


def choose_angle(infl: dict) -> tuple[str, str]:
    er, f = infl.get("engagement_rate") or 0, infl.get("followers") or 0
    niche = (infl.get("primary_niche") or "").lower()
    if er >= 6:
        key = "affiliate"                  
    elif f < 15_000:
        key = "barter"                        
    elif niche in {"skincare", "makeup", "haircare", "nails", "fragrance", "beauty general"}:
        key = "ugc_sponsored"
    else:
        key = "sponsorship"
    return key, ANGLES[key]


def build_signals(infl: dict, cfg: dict) -> dict:
    vids = sorted(infl.get("recent_videos") or [], key=lambda v: v.get("published_at") or "", reverse=True)
    titles = [clip_title(v["title"], 12) for v in vids[:3] if v.get("title")]
    top = max(vids, key=lambda v: v.get("views") or 0, default=None)
    themes = infl.get("themes") or []
    angle_key, angle_phrase = choose_angle(infl)
    b = cfg["brand"]
    return {
        "greeting_name": clean_name(infl.get("name", "")),
        "channel_name": infl.get("name"),
        "platform": infl.get("platform", "YouTube"),
        "niche": infl.get("primary_niche"),
        "main_theme": (themes[0] if themes else infl.get("primary_niche")) or "content",
        "content_themes": themes,
        "latest_video_titles": titles,
        "most_viewed_recent_video": clip_title(top["title"], 12) if top else None,
        "channel_bio_excerpt": (infl.get("description") or "")[:240].replace("\n", " "),
        "followers": infl.get("followers"),
        "engagement_rate_pct": infl.get("engagement_rate"),
        "creator_country": infl.get("country"),
        "collaboration_angle": angle_phrase,
        "angle_key": angle_key,
        "sender_name": b["sender_name"],
        "brand_name": b["name"],
    }



def fallback_messages(sig: dict, cfg: dict) -> dict:
    """Deterministic but varied: sentence variants chosen per-creator from real signals only."""
    b, seed = cfg["brand"], sig["channel_name"] or "x"
    theme, niche = sig["main_theme"], sig["niche"] or sig["main_theme"]
    video = clip_title(sig["latest_video_titles"][0], 7) if sig["latest_video_titles"] else ""
    name, angle = sig["greeting_name"], sig["collaboration_angle"]

    openers = [f'Your recent video "{video}" caught my eye, and your {theme} content feels genuine and useful.',
               f'I came across "{video}" and liked how practical your {theme} content is.',
               f'Your {theme} videos, like "{video}", stand out for how approachable they feel.'] if video else \
              [f'Your {theme} content stood out to me, and it feels genuine and useful.',
               f'I came across your channel and liked how practical your {theme} content is.']
    pitch = [f"I'm {b['sender_name']} from {b['name']}, {b['product']}. Your {niche} audience looks like a great fit for {angle} with us.",
             f"I work on partnerships at {b['name']}, {b['product']}. We think your {niche} community would love {angle}."]
    value = f"We'd offer {b['value_prop']}."
    extra = "We keep briefs flexible so the content still sounds like you."
    cta = ["Would you be open to a quick chat this week?", "Happy to send details if you're interested.",
           "Would it be okay if I shared more details?"]
    sign = f"Best,\n{b['sender_name']}\n{b['name']}"

    parts = [f"Hi {name},", pick(openers, seed + "o"), pick(pitch, seed + "p"), value, pick(cta, seed + "c"), sign]
    body = "\n\n".join(parts)
    if wc(body) > EMAIL_WORDS[1]:     
        parts.remove(value)
        body = "\n\n".join(parts)
    if wc(body) < EMAIL_WORDS[0]:
        parts.insert(4, extra)
        body = "\n\n".join(parts)

    dm_templates = [
        'Hi {name}, loved your {theme} content. Your {niche} audience looks like a great fit for {angle} with {brand}. Open to chatting?',
        'Hey {name}! Your {theme} content really stands out. We\'d love to explore {angle} between you and {brand}. Interested?',
        'Hi {name}, your {niche} content caught my eye{vidtxt}. {brand} would love to explore {angle} with you. Open to details?',
    ]
    dm = ""
    tpl = pick(dm_templates, seed + "d")
    for vlen in (5, 3, 0):
        vtxt = f' ("{clip_title(video, vlen)}")' if video and vlen else ""
        dm = tpl.format(name=name, theme=theme, niche=niche, angle=angle, brand=b["name"], vidtxt=vtxt)
        if wc(dm) <= DM_WORDS[1]:
            break

    return {"subject": f"{b['name']} x {name}: {sig['angle_key'].replace('_', ' ')} idea",
            "email_body": body, "instagram_dm": dm}


def _valid(out: dict) -> str | None:
    """Return None if valid, else a feedback string for the retry."""
    try:
        eb, dm, sub = out["email_body"].strip(), out["instagram_dm"].strip(), out["subject"].strip()
    except (KeyError, AttributeError):
        return "Response must be JSON with keys subject, email_body, instagram_dm."
    problems = []
    if not EMAIL_WORDS[0] <= wc(eb) <= EMAIL_WORDS[1]:
        problems.append(f"email_body had {wc(eb)} words; it must be {EMAIL_WORDS[0]}-{EMAIL_WORDS[1]}")
    if not DM_WORDS[0] <= wc(dm) <= DM_WORDS[1]:
        problems.append(f"instagram_dm had {wc(dm)} words; it must be {DM_WORDS[0]}-{DM_WORDS[1]}")
    if re.search(r"\[[^\]]+\]|\{[^}]+\}", eb + dm + sub):
        problems.append("remove all placeholders")
    if not sub:
        problems.append("subject is empty")
    return "; ".join(problems) or None


def _parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def llm_messages(sig: dict, cfg: dict, client=None, model: str | None = None, attempts: int = 3) -> dict | None:
    """Ask the configured LLM (see outreach/llm.py). `client` = injected Anthropic-style client (used by tests)."""
    from . import llm
    if client is None and not llm.is_configured():
        return None
    brand = {k: cfg["brand"][k] for k in ("name", "product", "value_prop", "sender_name", "sender_title")}
    feedback = ""
    for attempt in range(1, attempts + 1):
        prompt = USER_PROMPT.format(brand=json.dumps(brand, ensure_ascii=False),
                                    signals=json.dumps(sig, ensure_ascii=False, indent=1),
                                    elo=EMAIL_WORDS[0], ehi=EMAIL_WORDS[1], dlo=DM_WORDS[0], dhi=DM_WORDS[1],
                                    feedback=feedback)
        try:
            if client is not None:
                used = model or "injected-client"
                resp = client.messages.create(model=used, max_tokens=800, system=SYSTEM_PROMPT,
                                              messages=[{"role": "user", "content": prompt}])
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            else:
                text, used = llm.complete(SYSTEM_PROMPT, prompt)
        except Exception as exc:
            log.warning("LLM call failed (%s) - using fallback", exc)
            return None
        out = _parse_json(text)
        problem = _valid(out) if out else "Return valid JSON only."
        if problem is None:
            out["_model"] = used
            return out
        log.info("LLM output rejected (attempt %d): %s", attempt, problem)
        feedback = f"\n\nYour previous answer was rejected: {problem}. Fix this and return JSON only."
    return None



def generate_for(infl: dict, cfg: dict, use_llm: bool = True, client=None) -> dict:
    sig = build_signals(infl, cfg)
    out = llm_messages(sig, cfg, client=client) if use_llm else None
    if out:
        source = f"llm:{out.pop('_model')}"
    else:
        out, source = fallback_messages(sig, cfg), "rule_based"
    return {
        "channel_id": infl["channel_id"],
        "subject": out["subject"].strip(),
        "email_body": out["email_body"].strip(),
        "instagram_dm": out["instagram_dm"].strip(),
        "angle": sig["angle_key"],
        "source": source,
        "email_words": wc(out["email_body"]),
        "dm_words": wc(out["instagram_dm"]),
        "status": "pending_review",
        "generated_at": now_iso(),
        "signals": sig,
    }


def run_generation(cfg: dict, db, use_llm: bool = True, regenerate: bool = False) -> dict:
    import time
    from . import llm
    stats = {"generated": 0, "skipped_existing": 0, "llm": 0, "rule_based": 0}
    enabled = use_llm and llm.is_configured()
    if use_llm and not enabled:
        log.warning("No LLM configured (set LLM_PROVIDER + key in .env) - using rule-based fallback generator.")
    elif enabled:
        log.info("Using LLM: %s/%s", llm.provider(), llm.model_name(llm.provider()))
    delay = float(env("LLM_DELAY_SECONDS", "4" if llm.provider() != "anthropic" else "0"))
    for infl in db.all("influencers", "status='PASS'"):
        if not regenerate and db.exists("messages", "channel_id", infl["channel_id"]):
            stats["skipped_existing"] += 1
            continue
        try:
            msg = generate_for(infl, cfg, use_llm=enabled)
        except Exception as exc:
            log.exception("Message generation failed for %s: %s", infl.get("name"), exc)
            continue
        db.upsert("messages", msg, "channel_id")
        stats["generated"] += 1
        stats["llm" if msg["source"].startswith("llm") else "rule_based"] += 1
        if enabled and delay:
            time.sleep(delay)          
    log.info("Generation: %s", stats)
    return stats