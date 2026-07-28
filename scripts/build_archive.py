#!/usr/bin/env python3
"""
build_archive.py — Accumulate the daily ordo files into a liturgical-year archive.

Each day in ordo/ is classified by its liturgical identity:
  - ferial day:  "torsdag - uke IV"  →  key "uke-iv-torsdag"
  - named feast: "Marta"             →  key "fest-marta"
  - unclassified (feast missing)     →  key "ukjent-YYYY-MM-DD"

For every key the most recently scraped full content (all hours) is stored in
archive/days/<key>.json, and archive/index.json maps keys to their dates and
tracks psalter-week coverage. After a whole liturgical year of scraping, the
archive contains the complete four-week psalter cycle plus every feast.

The output is deterministic, so re-running without new data produces no git diff.

Run: python scripts/build_archive.py
"""

import json
import re
import unicodedata
from pathlib import Path

ORDO_DIR = Path(__file__).parent.parent / "ordo"
ARCHIVE_DIR = Path(__file__).parent.parent / "archive"
DAYS_DIR = ARCHIVE_DIR / "days"

WEEKDAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]

FERIAL_RE = re.compile(
    r"^(mandag|tirsdag|onsdag|torsdag|fredag|lørdag|søndag)\s*-\s*uke\s+([IVX]+)$",
    re.IGNORECASE,
)


def slugify(s: str) -> str:
    s = s.lower().replace("ø", "o").replace("æ", "ae").replace("å", "a")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "ukjent"


def classify(day: dict) -> tuple[str, str]:
    """Return (key, type) for a day's ordo JSON."""
    feast = (day.get("feast") or "").strip()
    m = FERIAL_RE.match(feast)
    if m:
        weekday, week = m.group(1).lower(), m.group(2).upper()
        return f"uke-{week.lower()}-{weekday}", "ferie"
    if feast:
        return f"fest-{slugify(feast)}", "fest"
    return f"ukjent-{day['date']}", "ukjent"


def main():
    days = []
    for path in sorted(ORDO_DIR.glob("*.json")):
        with open(path, encoding="utf-8") as f:
            days.append(json.load(f))

    index: dict[str, dict] = {}
    for day in days:  # sorted by date, so later days overwrite earlier content
        key, kind = classify(day)
        entry = index.setdefault(key, {"type": kind, "feast": day.get("feast", ""), "dates": []})
        if day["date"] not in entry["dates"]:
            entry["dates"].append(day["date"])
        entry["_content"] = day

    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    for key, entry in index.items():
        content = entry.pop("_content")
        entry["dates"].sort()
        entry["file"] = f"days/{key}.json"
        with open(DAYS_DIR / f"{key}.json", "w", encoding="utf-8") as f:
            json.dump(
                {"key": key, "type": entry["type"], "feast": entry["feast"],
                 "dates": entry["dates"], "content": content},
                f, ensure_ascii=False, indent=2, sort_keys=False,
            )

    # Psalter coverage: which of the 4 weeks × 7 days have been collected
    coverage = {}
    for week in ("i", "ii", "iii", "iv"):
        have = [wd for wd in WEEKDAYS if f"uke-{week}-{wd}" in index]
        coverage[f"uke-{week.upper()}"] = {
            "collected": have,
            "missing": [wd for wd in WEEKDAYS if wd not in have],
        }

    with open(ARCHIVE_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "keys": {k: index[k] for k in sorted(index)},
                "coverage": coverage,
                "totals": {
                    "days_scraped": len(days),
                    "ferial": sum(1 for e in index.values() if e["type"] == "ferie"),
                    "feasts": sum(1 for e in index.values() if e["type"] == "fest"),
                    "unclassified": sum(1 for e in index.values() if e["type"] == "ukjent"),
                },
            },
            f, ensure_ascii=False, indent=2,
        )

    print(f"Archive built: {len(index)} keys from {len(days)} days")
    for week, cov in coverage.items():
        print(f"  {week}: {len(cov['collected'])}/7 collected", end="")
        if cov["missing"]:
            print(f" (missing: {', '.join(cov['missing'])})")
        else:
            print(" ✓ complete")


if __name__ == "__main__":
    main()
