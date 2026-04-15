#!/usr/bin/env python3
"""
scrape_lesning.py — Daily scraper for the Office of Readings (Lesningsgudstjenesten).

Sources:
  - Norwegian text:  https://www.oblates.se/index.php?o=nbreviar  (today only)
  - Verification:    https://universalis.com/europe.norway/YYYYMMDD/readings.htm

Output:
  - ordo/YYYY-MM-DD.json

The JSON file is committed to the repo by the GitHub Action. Editors can then
review it, correct OCR errors, and set "verified": true.

Run manually:
  python scripts/scrape_lesning.py [YYYY-MM-DD]

If no date is given, today's date is used.
"""

import sys
import json
import re
import logging
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

OBLATES_URL = "https://www.oblates.se/index.php?o=nbreviar"
UNIVERSALIS_URL = "https://universalis.com/europe.norway/{date}/readings.htm"
OUTPUT_DIR = Path(__file__).parent.parent / "ordo"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LiturgiaNorvegiaeBot/1.0; "
        "+https://github.com/CooperatoresVeritatis/Liturgia-Horarum-Norvegica)"
    )
}


# ---------------------------------------------------------------------------
# oblates.se parser
# ---------------------------------------------------------------------------

def fetch_oblates(session: requests.Session) -> BeautifulSoup:
    resp = session.get(OBLATES_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return BeautifulSoup(resp.text, "lxml")


def find_lg_section(soup: BeautifulSoup):
    """
    Locate the Lesningsgudstjenesten (LG) block on the oblates.se page.
    The page lists all seven hours; we want only the LG section.
    Returns the tag that starts the LG section, or None.
    """
    # Look for a heading that contains 'Lesningsgudstjenesten' or 'LG'
    candidates = soup.find_all(
        lambda tag: tag.name in ("h2", "h3", "h4", "b", "strong", "p")
        and re.search(r"Lesningsgudstjenesten|LG\b|Office of Readings", tag.get_text(), re.I)
    )
    if not candidates:
        log.warning("Could not locate LG section heading on oblates.se")
        return None
    return candidates[0]


def extract_text_block(tag) -> list[str]:
    """Return a list of non-empty text lines from a tag and its siblings
    until the next section heading."""
    lines = []
    for sibling in tag.next_siblings:
        if sibling.name in ("h2", "h3", "h4") and sibling != tag:
            break
        text = sibling.get_text(separator="\n").strip() if hasattr(sibling, "get_text") else str(sibling).strip()
        if text:
            lines.extend([l.strip() for l in text.splitlines() if l.strip()])
    return lines


def parse_oblates(soup: BeautifulSoup) -> dict:
    """
    Parse the full LG section and return a structured dict.
    oblates.se renders everything in one long page; we extract
    the LG block between its heading and the next hour heading (Laudes).
    """
    result = {
        "feast": "",
        "hymne": "",
        "salmer": [],
        "vers": None,
        "lesning1": None,
        "responsorium1": None,
        "lesning2": None,
        "responsorium2": None,
        "teDeum": None,
        "bønn": "",
    }

    # --- Feast / date heading -------------------------------------------------
    date_tag = soup.find(lambda t: t.name and re.search(r"\d+ \w+ 20\d\d", t.get_text()))
    if date_tag:
        result["feast"] = date_tag.get_text(strip=True)

    # --- Locate LG section ---------------------------------------------------
    lg_start = find_lg_section(soup)
    if lg_start is None:
        log.error("LG section not found; returning empty structure.")
        return result

    # Collect all text inside the LG section until the next major hour heading
    section_tags = []
    for tag in lg_start.next_siblings:
        text = tag.get_text(strip=True) if hasattr(tag, "get_text") else ""
        # Stop when we hit the Laudes heading or similar
        if tag.name in ("h2", "h3") and re.search(r"Laudes|Ters|Sekst|Non|Vesper|Komplet", text, re.I):
            break
        section_tags.append(tag)

    section_soup = BeautifulSoup("".join(str(t) for t in section_tags), "lxml")

    # --- Hymn ----------------------------------------------------------------
    hymne_heading = section_soup.find(
        lambda t: t.name and re.search(r"Hymne|Hymn", t.get_text(), re.I)
    )
    if hymne_heading:
        lines = []
        for sib in hymne_heading.next_siblings:
            txt = sib.get_text(separator="\n").strip() if hasattr(sib, "get_text") else ""
            if txt and re.search(r"Ant\.|Salme|Psalm|℣|℟|Lesning", txt):
                break
            if txt:
                lines.append(txt)
        result["hymne"] = "\n".join(lines)

    # --- Psalms --------------------------------------------------------------
    antiphon_pattern = re.compile(r"Ant\.\s*\d*", re.I)
    psalm_ref_pattern = re.compile(r"Sal(?:me)?\s*\d+|Jes\s*\d+|Dan\s*\d+", re.I)

    current_salme = None
    for tag in section_soup.find_all(True):
        text = tag.get_text(strip=True)
        if antiphon_pattern.match(text):
            if current_salme and "antifon" not in current_salme:
                current_salme["antifon"] = text.replace("Ant.", "").replace("Ant. 1", "").replace("Ant. 2", "").replace("Ant. 3", "").strip()
            elif current_salme and current_salme.get("tekst"):
                # Second antiphon occurrence = end of psalm
                pass
        if psalm_ref_pattern.search(text) and len(text) < 50:
            if current_salme:
                result["salmer"].append(current_salme)
            current_salme = {"referanse": text, "antifon": "", "tekst": []}

    if current_salme:
        result["salmer"].append(current_salme)

    # --- First Reading -------------------------------------------------------
    reading1_heading = section_soup.find(
        lambda t: t.name and re.search(r"(Første|1\.|I\.)\s*lesning|Bibel", t.get_text(), re.I)
    )
    if reading1_heading:
        ref_tag = reading1_heading.find_next(
            lambda t: t.name and re.search(r"\d+,\d+", t.get_text())
        )
        ref = ref_tag.get_text(strip=True) if ref_tag else ""
        # Collect reading text until responsory
        lines = []
        for sib in (ref_tag or reading1_heading).next_siblings:
            txt = sib.get_text(separator="\n").strip() if hasattr(sib, "get_text") else ""
            if re.search(r"Responsorium|℟|Andre lesning|2\. lesning", txt, re.I):
                break
            if txt:
                lines.append(txt)
        result["lesning1"] = {"referanse": ref, "tekst": "\n".join(lines)}

    # --- Second Reading (patristic) ------------------------------------------
    reading2_heading = section_soup.find(
        lambda t: t.name and re.search(r"(Andre|2\.|II\.)\s*lesning|Kirkefader|Patristic", t.get_text(), re.I)
    )
    if reading2_heading:
        kilde_tag = reading2_heading.find_next(["em", "i", "b", "strong"])
        kilde = kilde_tag.get_text(strip=True) if kilde_tag else ""
        lines = []
        for sib in reading2_heading.next_siblings:
            txt = sib.get_text(separator="\n").strip() if hasattr(sib, "get_text") else ""
            if re.search(r"Responsorium|Te Deum|Avslutning|Bønn", txt, re.I):
                break
            if txt and txt != kilde:
                lines.append(txt)
        result["lesning2"] = {
            "kilde": kilde,
            "tittel": "",
            "tekst": "\n".join(lines),
        }

    # --- Closing Prayer -------------------------------------------------------
    bønn_heading = section_soup.find(
        lambda t: t.name and re.search(r"Avslutningsbønn|Bønn\b", t.get_text(), re.I)
    )
    if bønn_heading:
        lines = []
        for sib in bønn_heading.next_siblings:
            txt = sib.get_text(separator="\n").strip() if hasattr(sib, "get_text") else ""
            if re.search(r"Laudes|Invitatorium", txt, re.I):
                break
            if txt:
                lines.append(txt)
        result["bønn"] = "\n".join(lines)

    # --- Te Deum (Sundays and solemnities) -----------------------------------
    te_deum_heading = section_soup.find(
        lambda t: t.name and re.search(r"Te Deum", t.get_text(), re.I)
    )
    if te_deum_heading:
        lines = []
        for sib in te_deum_heading.next_siblings:
            txt = sib.get_text(separator="\n").strip() if hasattr(sib, "get_text") else ""
            if re.search(r"Avslutningsbønn|Bønn\b", txt, re.I):
                break
            if txt:
                lines.append(txt)
        result["teDeum"] = "\n".join(lines)

    return result


# ---------------------------------------------------------------------------
# universalis.com parser (English, for cross-reference)
# ---------------------------------------------------------------------------

def fetch_universalis(session: requests.Session, target_date: date) -> dict:
    date_str = target_date.strftime("%Y%m%d")
    url = UNIVERSALIS_URL.format(date=date_str)
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Universalis fetch failed: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "lxml")

    result = {"feast_en": "", "psalm_refs": [], "reading1_ref": "", "reading2_source": ""}

    # Feast/day title
    title = soup.find("h1") or soup.find("h2")
    if title:
        result["feast_en"] = title.get_text(strip=True)

    # Psalm references
    psalm_refs = soup.find_all(
        lambda t: t.name in ("p", "span", "td") and re.search(r"Psalm \d+|Ps \d+", t.get_text())
    )
    result["psalm_refs"] = list({r.get_text(strip=True) for r in psalm_refs})

    # Reading references
    reading_refs = soup.find_all(
        lambda t: t.name in ("p", "span") and re.search(r"\d+:\d+", t.get_text())
    )
    refs = [r.get_text(strip=True) for r in reading_refs if len(r.get_text(strip=True)) < 40]
    if refs:
        result["reading1_ref"] = refs[0]
    if len(refs) > 1:
        result["reading2_source"] = refs[1]

    return result


# ---------------------------------------------------------------------------
# Cross-reference check
# ---------------------------------------------------------------------------

def check_mismatch(no_data: dict, en_data: dict) -> bool:
    """
    Return True if there are signs of a mismatch between the Norwegian
    (oblates.se) and English (universalis) sources.
    Currently checks: feast name similarity.
    """
    if not en_data:
        return False

    no_feast = no_data.get("feast", "").lower()
    en_feast = en_data.get("feast_en", "").lower()

    # Very rough heuristic: if one says "feast" or "solemnity" and the other doesn't
    feast_keywords = {"feast", "solemnity", "memorial", "høytid", "fest", "minnedag"}
    no_has_feast = any(k in no_feast for k in feast_keywords)
    en_has_feast = any(k in en_feast for k in feast_keywords)

    if no_has_feast != en_has_feast:
        log.warning(f"Feast type mismatch — NO: '{no_feast}' vs EN: '{en_feast}'")
        return True

    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    target_date = date.today()
    if len(sys.argv) > 1:
        try:
            target_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
        except ValueError:
            log.error("Date must be in YYYY-MM-DD format")
            sys.exit(1)

    date_str = target_date.isoformat()
    output_path = OUTPUT_DIR / f"{date_str}.json"

    if output_path.exists():
        log.info(f"{output_path} already exists — skipping (delete to re-scrape)")
        sys.exit(0)

    log.info(f"Scraping for {date_str}…")

    with requests.Session() as session:
        # Norwegian source
        log.info("Fetching oblates.se…")
        oblates_soup = fetch_oblates(session)
        no_data = parse_oblates(oblates_soup)

        # English cross-reference
        log.info("Fetching universalis.com…")
        en_data = fetch_universalis(session, target_date)

    mismatch = check_mismatch(no_data, en_data)

    ordo = {
        "date": date_str,
        "feast": no_data.get("feast", ""),
        "feast_en": en_data.get("feast_en", ""),
        "season": "",           # TODO: derive from feast/calendar
        "verified": False,
        "mismatch_flag": mismatch,
        "sources": {
            "no": OBLATES_URL,
            "en": UNIVERSALIS_URL.format(date=target_date.strftime("%Y%m%d")),
        },
        "hymne": no_data.get("hymne", ""),
        "salmer": no_data.get("salmer", []),
        "vers": no_data.get("vers"),
        "lesning1": no_data.get("lesning1"),
        "responsorium1": no_data.get("responsorium1"),
        "lesning2": no_data.get("lesning2"),
        "responsorium2": no_data.get("responsorium2"),
        "teDeum": no_data.get("teDeum"),
        "bønn": no_data.get("bønn", ""),
    }

    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ordo, f, ensure_ascii=False, indent=2)

    log.info(f"Written to {output_path}")
    if mismatch:
        log.warning("⚠ Mismatch flag set — please review this file before publishing.")

    sys.exit(0)


if __name__ == "__main__":
    main()
