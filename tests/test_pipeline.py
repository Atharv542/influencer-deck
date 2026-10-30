"""Unit + end-to-end tests. All data here is SYNTHETIC TEST FIXTURE DATA used only to exercise code paths;
it is never written to the real database or the deliverable CSVs."""
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outreach.analysis import compute_engagement, score_keywords
from outreach.config import load_config
from outreach.db import Database
from outreach.discovery import run_discovery
from outreach.enrichment import NOT_FOUND, extract_emails, extract_socials, find_email, run_enrichment
from outreach.exporter import export_all, tracker_rows
from outreach.filtering import evaluate, run_filtering
from outreach.personalization import (DM_WORDS, EMAIL_WORDS, build_signals, fallback_messages, run_generation, wc)
from outreach.sender import approve, run_sending


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["paths"]["db"] = str(tmp_path / "t.db")
    c["paths"]["output_dir"] = str(tmp_path / "out")
    c["paths"]["suppression_list"] = str(tmp_path / "supp.txt")
    c["sending"]["delay_seconds"] = 0
    return c


DEFAULT_DESC = "Business: collab@testcreator.com\nIG https://instagram.com/testcreator"


def vids(n=6, views=1000, likes=60, comments=10, title="My skincare routine serum review", days=5,
         desc=DEFAULT_DESC, tags=("skincare", "serum", "grwm")):
    now = datetime.now(timezone.utc)
    return [{"id": f"v{i}", "title": f"{title} {i}", "description": desc,
             "tags": list(tags), "published_at": (now - timedelta(days=days + i)).isoformat(),
             "views": views, "likes": likes, "comments": comments} for i in range(n)]


def infl(cid="c1", name="Test Creator", followers=20000, **kw):
    base = {"channel_id": cid, "name": name, "platform": "YouTube", "profile_url": f"https://youtube.com/channel/{cid}",
            "followers": followers, "country": "US", "description": "Skincare and makeup tutorials. Contact: collab@testcreator.com "
            "https://instagram.com/testcreator https://testcreator.com", "keywords": "skincare makeup",
            "recent_videos": vids(), "engagement_rate": 7.0, "engagement_videos": 6, "last_upload_days": 5}
    base.update(kw)
    return base


# ---------------------------------------------------------------- analysis
def test_engagement_median_and_missing_likes():
    er, n = compute_engagement(vids(), 3)
    assert er == 7.0 and n == 6
    hidden = [dict(v, likes=None) for v in vids()]
    assert compute_engagement(hidden, 3) == (None, 0)          # never guessed


def test_keyword_scoring_word_boundary():
    s = score_keywords("my skincare routine. nothing about spfx", {"skincare": ["skincare", "spf"]})
    assert s["skincare"] == 1


# ---------------------------------------------------------------- filtering
def test_filter_pass_and_reasons(cfg):
    assert evaluate(infl(), cfg)["status"] == "PASS"
    big = evaluate(infl(followers=2_500_000), cfg)
    assert big["status"] == "FAIL" and "above micro-influencer maximum" in big["fail_reasons"][0]
    assert evaluate(infl(followers=900), cfg)["status"] == "FAIL"
    low_er = evaluate(infl(engagement_rate=0.4), cfg)
    assert any("engagement 0.40%" in r for r in low_er["fail_reasons"])
    none_er = evaluate(infl(engagement_rate=None), cfg)
    assert any("engagement unavailable" in r for r in none_er["fail_reasons"])
    stale = evaluate(infl(last_upload_days=400), cfg)
    assert any("inactive" in r for r in stale["fail_reasons"])
    off_niche = evaluate(infl(description="Gaming and speedruns", keywords="", recent_videos=vids(title="Elden Ring boss", n=6, desc="", tags=("gaming", "elden ring"))), cfg)
    assert off_niche["status"] == "FAIL"


def test_blocked_and_geo(cfg):
    bad = evaluate(infl(description="skincare skincare makeup. Join our casino"), cfg)
    assert any("brand-fit" in r for r in bad["fail_reasons"])
    cfg2 = copy.deepcopy(cfg)
    cfg2["filters"]["allowed_countries"] = ["IN"]
    assert any("country US" in r for r in evaluate(infl(), cfg2)["fail_reasons"])
    assert evaluate(infl(country=None), cfg2)["status"] == "PASS"   # unknown geography -> note, not fail


# ---------------------------------------------------------------- enrichment
def test_email_extraction_rules():
    assert extract_emails("write me: Hello@Site.com.") == ["hello@site.com"]
    assert extract_emails("name [at] gmail [dot] com") == ["name@gmail.com"]
    assert extract_emails("logo@2x.png example@example.com") == []
    assert extract_emails(None) == []


def test_find_email_priority_and_not_found():
    e, src = find_email(infl())
    assert e == "collab@testcreator.com" and src == "channel description"
    only_vids = infl(description="no email here")
    assert find_email(only_vids)[0] == "collab@testcreator.com"       # repeated in >=2 videos
    one = infl(description="x", recent_videos=vids(1))
    assert find_email(one)[0] == NOT_FOUND                             # single hit is not trusted
    assert find_email(infl(description="x", recent_videos=[]))[0] == NOT_FOUND


def test_socials():
    s = extract_socials("ig https://www.instagram.com/some.one/ tt https://tiktok.com/@abc site https://mysite.com/x. "
                        "https://youtube.com/xyz https://instagram.com/p/123")
    assert s["instagram_handle"] == "some.one" and s["tiktok_url"].endswith("@abc") and s["website"] == "https://mysite.com/x"


# ---------------------------------------------------------------- personalization
def test_fallback_word_counts_across_many_creators(cfg):
    for i in range(60):
        r = infl(cid=f"id{i}", name=f"Creator {i} Beauty", followers=6000 + i * 1500, engagement_rate=2 + i % 9,
                 primary_niche=["skincare", "makeup", "fashion styling"][i % 3], themes=["skincare", "grwm"])
        m = fallback_messages(build_signals(r, cfg), cfg)
        assert EMAIL_WORDS[0] <= wc(m["email_body"]) <= EMAIL_WORDS[1], (i, wc(m["email_body"]), m["email_body"])
        assert DM_WORDS[0] <= wc(m["instagram_dm"]) <= DM_WORDS[1], (i, wc(m["instagram_dm"]), m["instagram_dm"])
        assert "[" not in m["email_body"] and "{" not in m["email_body"]


def test_messages_differ_between_creators(cfg):
    a = fallback_messages(build_signals(infl("a", "Alpha Glow"), cfg), cfg)
    b = fallback_messages(build_signals(infl("b", "Beta Style", primary_niche="fashion styling", themes=["thrift"],
                                             recent_videos=vids(title="Thrift haul")), cfg), cfg)
    assert a["email_body"] != b["email_body"] and a["instagram_dm"] != b["instagram_dm"]


class FakeLLM:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        text = self.replies.pop(0)
        return type("R", (), {"content": [type("B", (), {"type": "text", "text": text})()]})()


def test_llm_retry_then_accept(cfg):
    from outreach.personalization import llm_messages
    good = '{"subject":"Idea","email_body":"%s","instagram_dm":"%s"}' % (" ".join(["word"] * 70), " ".join(["dm"] * 20))
    bad = '{"subject":"Idea","email_body":"too short","instagram_dm":"short"}'
    client = FakeLLM([bad, good])
    out = llm_messages(build_signals(infl(), cfg), cfg, client=client, model="m")
    assert out and client.calls == 2
    assert llm_messages(build_signals(infl(), cfg), cfg, client=FakeLLM(["nope"] * 3), model="m") is None


# ---------------------------------------------------------------- end to end
class FakeYT:
    """Stands in for the YouTube API in tests only."""
    def search_channel_ids(self, query, kind="channel", pages=2, region=None, lang=None):
        return ["UC_a", "UC_b", "UC_c", "UC_big"]

    def get_channels(self, ids):
        def ch(i, subs, desc):
            return {"id": i, "snippet": {"title": f"Test {i}", "description": desc, "country": "US", "customUrl": f"@{i.lower()}"},
                    "statistics": {"subscriberCount": str(subs), "viewCount": "1", "videoCount": "10"},
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU" + i}}, "brandingSettings": {"channel": {"keywords": "skincare"}}}
        items = {"UC_a": ch("UC_a", 12000, "Skincare & makeup. Business: pr@a-test.com"),
                 "UC_b": ch("UC_b", 30000, "Skincare and makeup tutorials, no email here"),
                 "UC_c": ch("UC_c", 800, "Skincare tiny channel"),
                 "UC_big": ch("UC_big", 5_000_000, "Skincare giant")}
        return [items[i] for i in ids]

    def get_recent_videos(self, playlist, n=10):
        # UC_b's videos have no email anywhere -> must come out as "Not Found"
        return vids(8, title="skincare serum routine makeup", desc="" if playlist.endswith("UC_b") else DEFAULT_DESC)


def test_end_to_end_pipeline(cfg):
    db = Database(cfg["paths"]["db"])
    stats = run_discovery(cfg, db, FakeYT(), target=2)
    assert stats["in_range_total"] == 2 and db.count("influencers") >= 2
    run_filtering(cfg, db)
    passed = db.all("influencers", "status='PASS'")
    assert {r["channel_id"] for r in passed} == {"UC_a", "UC_b"}
    run_enrichment(cfg, db, scrape=False)
    assert db.get("influencers", "channel_id", "UC_a")["email"] == "pr@a-test.com"
    run_generation(cfg, db, use_llm=False)
    assert db.count("messages") == 2

    # nothing sends before approval
    s0 = run_sending(cfg, db, "dry_run")                       # not approved yet -> nothing goes out
    assert s0["sent"] == 0 and s0["skipped_unapproved"] >= 1
    approve(db)
    s1 = run_sending(cfg, db, "dry_run")
    assert s1["sent"] == 1 and s1["skipped_no_email"] == 1     # UC_a has an email, UC_b does not
    s2 = run_sending(cfg, db, "dry_run")                       # second run: duplicate prevented
    assert s2["sent"] == 0 and s2["skipped_duplicate"] == 1

    rows = tracker_rows(db)
    assert {r["Influencer"] for r in rows} == {"Test UC_a", "Test UC_b"}
    assert any(r["Email"] == NOT_FOUND for r in rows)           # UC_b: no email -> clearly marked, never guessed
    paths = export_all(cfg, db)
    assert Path(paths["outreach_tracker.csv"]).exists()


def test_live_duplicate_blocked_by_db_and_logic(cfg):
    db = Database(cfg["paths"]["db"])
    r = infl()
    r.update(status="PASS", email="pr@a-test.com", themes=["skincare"], primary_niche="skincare")
    db.upsert("influencers", r, "channel_id")
    run_generation(cfg, db, use_llm=False)
    approve(db)

    class Transport:
        sent = []
        def send(self, to, subject, body):
            self.sent.append(to)
    t = Transport()
    s = run_sending(cfg, db, "live", transport=t, sleep=lambda _: None)
    assert s["sent"] == 1 and t.sent == ["pr@a-test.com"]
    s = run_sending(cfg, db, "live", transport=t, sleep=lambda _: None)
    assert s["sent"] == 0 and s["skipped_duplicate"] == 1 and len(t.sent) == 1
    with pytest.raises(Exception):                                # DB-level unique index also rejects it
        db.log_outreach(channel_id="x", influencer="x", email="pr@a-test.com", channel="email",
                        message_generated=1, sent=1, mode="live", status="sent", detail="")


def test_send_failure_is_logged_and_retryable(cfg):
    db = Database(cfg["paths"]["db"])
    r = infl()
    r.update(status="PASS", email="pr@a-test.com", themes=["skincare"], primary_niche="skincare")
    db.upsert("influencers", r, "channel_id")
    run_generation(cfg, db, use_llm=False)
    approve(db)

    class Boom:
        def send(self, *a):
            raise RuntimeError("smtp down")
    s = run_sending(cfg, db, "live", transport=Boom(), sleep=lambda _: None)
    assert s["failed"] == 1 and db.latest_log("c1", "email")["status"] == "failed"
    class Ok:
        def send(self, *a): pass
    assert run_sending(cfg, db, "live", transport=Ok(), sleep=lambda _: None)["sent"] == 1


# ---------------------------------------------------------------- free LLM providers (HTTP mocked)
GOOD_JSON = '{"subject":"Idea","email_body":"%s","instagram_dm":"%s"}' % (" ".join(["word"] * 70), " ".join(["dm"] * 20))


class FakeResp:
    def __init__(self, payload, code=200):
        self._p, self.status_code, self.text = payload, code, str(payload)

    def json(self):
        return self._p


@pytest.mark.parametrize("prov,key_var,payload", [
    ("gemini", "GEMINI_API_KEY", {"candidates": [{"content": {"parts": [{"text": GOOD_JSON}]}}]}),
    ("groq", "GROQ_API_KEY", {"choices": [{"message": {"content": GOOD_JSON}}]}),
    ("openrouter", "OPENROUTER_API_KEY", {"choices": [{"message": {"content": GOOD_JSON}}]}),
    ("ollama", None, {"choices": [{"message": {"content": GOOD_JSON}}]}),
])
def test_free_providers(monkeypatch, cfg, prov, key_var, payload):
    from outreach import llm
    from outreach.personalization import llm_messages
    for v in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", prov)
    if key_var:
        monkeypatch.setenv(key_var, "dummy")
    monkeypatch.setattr(llm.requests, "post", lambda url, **kw: FakeResp(payload))
    assert llm.is_configured()
    out = llm_messages(build_signals(infl(), cfg), cfg)
    assert out and out["_model"].startswith(prov + "/")


def test_provider_without_key_falls_back(monkeypatch, cfg):
    from outreach import llm
    for v in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert not llm.is_configured()
    db = Database(cfg["paths"]["db"])
    r = infl(); r.update(status="PASS", themes=["skincare"], primary_niche="skincare")
    db.upsert("influencers", r, "channel_id")
    assert run_generation(cfg, db, use_llm=True)["rule_based"] == 1