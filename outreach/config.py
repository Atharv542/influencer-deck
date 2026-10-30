
from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
        
    for key, val in cfg.get("paths", {}).items():
        p = Path(val)
        cfg["paths"][key] = str(p if p.is_absolute() else ROOT / p)
    Path(cfg["paths"]["output_dir"]).mkdir(parents=True, exist_ok=True)
    Path(cfg["paths"]["db"]).parent.mkdir(parents=True, exist_ok=True)
    return cfg


def env(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    return val if val not in ("", None) else default
