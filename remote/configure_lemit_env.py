#!/usr/bin/env python3
"""Configure Lemit secrets from stdin without exposing them in process arguments."""

from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=Path)
    args = parser.parse_args()
    token = __import__("sys").stdin.read().strip()
    if len(token) < 20 or any(character.isspace() for character in token):
        raise RuntimeError("invalid Lemit token supplied on stdin")
    path = args.env_file
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    existing: dict[str, str] = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            existing[key] = value
    updates = {
        "LEMIT_API_TOKEN": token,
        "LEMIT_PII_ENCRYPTION_KEY": existing.get("LEMIT_PII_ENCRYPTION_KEY") or secrets.token_urlsafe(48),
        "LEMIT_SEGMENT": "20782",
        "LEMIT_REQUESTS_PER_SECOND": "10",
        "LEMIT_CACHE_DAYS": "60",
        "PARTNER_ENRICHMENT_DATABASE_PATH": "/data/partner-enrichment.sqlite",
    }
    replaced: set[str] = set()
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
        if key in updates:
            output.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            output.append(line)
    if output and output[-1]:
        output.append("")
    output.extend(f"{key}={value}" for key, value in updates.items() if key not in replaced)
    temporary = path.with_name(f".{path.name}.lemit.tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.chmod(path.stat().st_mode & 0o777)
    os.replace(temporary, path)
    print(json.dumps({
        "configured": True,
        "segment": 20782,
        "requests_per_second": 10,
        "cache_days": 60,
        "encryption_key_reused": bool(existing.get("LEMIT_PII_ENCRYPTION_KEY")),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
