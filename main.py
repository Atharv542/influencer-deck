#!/usr/bin/env python3
"""Micro-influencer outreach pipeline CLI.

Discovery -> Filtering -> Enrichment -> AI Personalization -> Review -> Sending -> Tracking
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from outreach.config import ROOT, env, load_config
from outreach.db import Database
from outreach.discovery import run_discovery
from outreach.enrichment import run_enrichment
from outreach.exporter import export_all
from outreach.filtering import run_filtering
from outreach.personalization import run_generation
from outreach.sender import approve, mark_dm_sent, run_sending
from outreach.youtube import YouTubeClient, YouTubeError


def setup_logging() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(ROOT / "logs" / "pipeline.log", encoding="utf-8")])


def show(title: str, data) -> None:
    print(f"\n=== {title} ===\n{json.dumps(data, indent=2, ensure_ascii=False)}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to an alternative config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover", help="find channels via YouTube Data API")
    s.add_argument("--target", type=int, help="stop after N in-range (5k-100k) channels")
    sub.add_parser("filter", help="apply filters + classify (PASS/FAIL with reasons)")
    s = sub.add_parser("enrich", help="enrich shortlisted profiles (emails, socials, themes)")
    s.add_argument("--scrape-websites", action="store_true", help="also check creators' own websites (robots.txt respected)")
    s = sub.add_parser("generate", help="generate email + Instagram DM per shortlisted influencer")
    s.add_argument("--no-llm", action="store_true", help="force the rule-based generator")
    s.add_argument("--regenerate", action="store_true", help="overwrite existing messages")
    sub.add_parser("review", help="export messages for human review")
    s = sub.add_parser("approve", help="approve messages for sending")
    s.add_argument("--all", action="store_true")
    s.add_argument("--ids", nargs="*", help="channel_ids to approve")
    s = sub.add_parser("send", help="send approved emails (dry-run by default)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--live", action="store_true", help="really send via SMTP")
    g.add_argument("--test-recipient", help="send REAL emails but redirect all of them to this address")
    s.add_argument("--limit", type=int)
    sub.add_parser("dm-queue", help="export the manual Instagram DM queue")
    s = sub.add_parser("mark-dm-sent", help="record that you sent a DM by hand")
    s.add_argument("--ids", nargs="+", required=True)
    sub.add_parser("export", help="write all CSV/MD deliverables to outputs/")
    s = sub.add_parser("run-all", help="full pipeline (dry-run send by default)")
    s.add_argument("--target", type=int)
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--scrape-websites", action="store_true")
    s.add_argument("--auto-approve", action="store_true", help="skip manual review (demo only)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--live", action="store_true")
    g.add_argument("--test-recipient")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    cfg = load_config(args.config)
    db = Database(cfg["paths"]["db"])
    cmd = args.cmd

    def yt() -> YouTubeClient:
        return YouTubeClient(env("YOUTUBE_API_KEY"))

    def send_mode():
        if getattr(args, "live", False):
            return "live", None
        if getattr(args, "test_recipient", None):
            return "test", args.test_recipient
        return "dry_run", None

    try:
        if cmd == "discover":
            show("Discovery", run_discovery(cfg, db, yt(), args.target))
        elif cmd == "filter":
            show("Filtering", run_filtering(cfg, db))
        elif cmd == "enrich":
            show("Enrichment", run_enrichment(cfg, db, args.scrape_websites or None))
        elif cmd == "generate":
            show("Generation", run_generation(cfg, db, not args.no_llm, args.regenerate))
        elif cmd == "review":
            paths = export_all(cfg, db)
            print(f"Review messages in: {paths['personalized_messages.md']}\nThen run: python main.py approve --all")
        elif cmd == "approve":
            if not (args.all or args.ids):
                print("Use --all or --ids <channel_id ...>")
                return 2
            print(f"Approved {approve(db, None if args.all else args.ids)} messages")
        elif cmd == "send":
            mode, rcpt = send_mode()
            show(f"Sending ({mode})", run_sending(cfg, db, mode, rcpt, args.limit))
            export_all(cfg, db)
        elif cmd == "dm-queue":
            print(f"DM queue written to {export_all(cfg, db)['dm_queue.csv']}")
        elif cmd == "mark-dm-sent":
            print(f"Logged {mark_dm_sent(db, args.ids)} manual DMs")
            export_all(cfg, db)
        elif cmd == "export":
            for name, path in export_all(cfg, db).items():
                print(f"{name:32s} -> {path}")
        elif cmd == "run-all":
            show("Discovery", run_discovery(cfg, db, yt(), args.target))
            show("Filtering", run_filtering(cfg, db))
            show("Enrichment", run_enrichment(cfg, db, args.scrape_websites or None))
            show("Generation", run_generation(cfg, db, not args.no_llm))
            if args.auto_approve:
                print(f"Auto-approved {approve(db)} messages")
            else:
                print("\nReview step: open outputs/personalized_messages.md, then `python main.py approve --all` and `python main.py send`.")
            if args.auto_approve:
                mode, rcpt = send_mode()
                show(f"Sending ({mode})", run_sending(cfg, db, mode, rcpt))
            for name, path in export_all(cfg, db).items():
                print(f"{name:32s} -> {path}")
    except YouTubeError as exc:
        logging.getLogger("main").error("%s", exc)
        return 1
    except Exception:
        logging.getLogger("main").exception("Unexpected error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
