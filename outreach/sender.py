
from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .config import env
from .enrichment import is_valid_email

log = logging.getLogger(__name__)


MODE_STATUS = {"dry_run": "simulated", "test": "test_sent", "live": "sent"}


class SmtpTransport:
    def __init__(self):
        self.host, self.port = env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587"))
        self.user, self.password = env("SMTP_USER"), env("SMTP_PASS")
        self.from_email = env("FROM_EMAIL", self.user)
        self.from_name = env("FROM_NAME", "")
        if not (self.user and self.password and self.from_email):
            raise RuntimeError("SMTP_USER / SMTP_PASS / FROM_EMAIL missing in .env - needed for live or test sends.")

    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.from_email))
        msg["To"], msg["Subject"] = to, subject
        msg["Reply-To"] = self.from_email
        msg.set_content(body)
        cls = smtplib.SMTP_SSL if self.port == 465 else smtplib.SMTP
        with cls(self.host, self.port, timeout=30) as s:
            if cls is smtplib.SMTP:
                s.starttls()
            s.login(self.user, self.password)
            s.send_message(msg)


def load_suppression(path: str) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    return {ln.strip().lower() for ln in p.read_text().splitlines() if ln.strip() and not ln.startswith("#")}


def approve(db, ids: list[str] | None = None) -> int:
    where, params = "status='pending_review'", ()
    if ids:
        where += f" AND channel_id IN ({','.join('?' * len(ids))})"
        params = tuple(ids)
    n = db.count("messages", where, params)
    db.conn.execute(f"UPDATE messages SET status='approved' WHERE {where}", params)
    db.conn.commit()
    return n


def run_sending(cfg: dict, db, mode: str = "dry_run", test_recipient: str | None = None,
                limit: int | None = None, transport=None, sleep=time.sleep) -> dict:
    """Send approved emails to shortlisted influencers with a valid email. Idempotent."""
    if mode == "test" and not test_recipient:
        raise ValueError("test mode needs --test-recipient")
    s_cfg = cfg["sending"]
    status_ok = MODE_STATUS[mode]
    blocking = ("sent", status_ok)          
    suppressed = load_suppression(cfg["paths"]["suppression_list"])
    if mode in ("test", "live") and transport is None:
        transport = SmtpTransport()

    stats = {"attempted": 0, "sent": 0, "skipped_duplicate": 0, "skipped_no_email": 0,
             "skipped_unapproved": 0, "skipped_suppressed": 0, "failed": 0}
    rows = db.all("influencers", "status='PASS'", order="followers DESC")
    for infl in rows:
        cid, name, email = infl["channel_id"], infl["name"], infl.get("email")
        msg = db.get("messages", "channel_id", cid)
        if not msg:
            continue
        if not is_valid_email(email):
            stats["skipped_no_email"] += 1
            continue
        if msg["status"] not in ("approved", "sent", "simulated", "test_sent"):
            stats["skipped_unapproved"] += 1
            continue
        if email.lower() in suppressed:
            stats["skipped_suppressed"] += 1
            continue
        if db.already_contacted(email, cid, blocking):
            stats["skipped_duplicate"] += 1
            log.info("Duplicate prevented: %s <%s>", name, email)
            continue
        if limit is not None and stats["attempted"] >= limit:
            break
        if mode == "live" and stats["sent"] >= s_cfg["daily_cap"]:
            log.warning("Daily cap (%d) reached - stopping.", s_cfg["daily_cap"])
            break

        stats["attempted"] += 1
        body = f"{msg['email_body']}\n\n--\n{s_cfg['footer']}"
        to, subject, detail = email, msg["subject"], ""
        if mode == "test":
            to, subject, detail = test_recipient, f"[TEST for {name} <{email}>] {msg['subject']}", f"redirected to {test_recipient}"
        try:
            if mode == "dry_run":
                detail = "dry run - nothing was transmitted"
                log.info("[DRY RUN] would send to %s <%s>: %s", name, email, msg["subject"])
            else:
                transport.send(to, subject, body)
            status, sent = status_ok, 1
        except Exception as exc: 
            log.error("Send failed for %s: %s", email, exc)
            status, sent, detail = "failed", 0, str(exc)[:300]
            stats["failed"] += 1
        try:
            db.log_outreach(channel_id=cid, influencer=name, email=email, channel="email",
                            message_generated=1, sent=sent, mode=mode, status=status, detail=detail)
        except Exception as exc:  
            log.warning("Log insert rejected (duplicate?): %s", exc)
            stats["skipped_duplicate"] += 1
            continue
        if sent:
            stats["sent"] += 1
            db.conn.execute("UPDATE messages SET status=? WHERE channel_id=?", (status_ok, cid))
            db.conn.commit()
            if mode == "live":
                sleep(s_cfg["delay_seconds"])
    log.info("Sending (%s): %s", mode, stats)
    return stats


def mark_dm_sent(db, ids: list[str]) -> int:
    """Instagram DMs are sent manually (no compliant automation API). Record that a human sent one."""
    n = 0
    for cid in ids:
        infl = db.get("influencers", "channel_id", cid)
        if not infl or not db.exists("messages", "channel_id", cid):
            continue
        db.log_outreach(channel_id=cid, influencer=infl["name"], email=infl.get("email"), channel="instagram_dm",
                        message_generated=1, sent=1, mode="manual", status="sent_manual",
                        detail=f"sent by hand to {infl.get('instagram_url') or 'profile found manually'}")
        n += 1
    return n
