#!/usr/bin/env python3
"""
scrape_lesning.py — Daily scraper for the Liturgy of the Hours:
the Office of Readings (Lesningsgudstjenesten) plus, when found on the page,
Laudes, Middagsbønn and Vesper.

Sources:
  - Norwegian text:  https://www.oblates.se/index.php?o=nbreviar  (today only)
  - Verification:    https://universalis.com/europe.norway/YYYYMMDD/readings.htm

Output:
  - ordo/YYYY-MM-DD.json  (date in Europe/Oslo)
  - debug/*.html          (raw fetched HTML, for workflow artifacts — not committed)

Run manually:
  python scripts/scrape_lesning.py [YYYY-MM-DD]
"""

import sys
import json
import re
import time
import logging
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

OBLATES_URL = "https://www.oblates.se/index.php?o=nbreviar"
UNIVERSALIS_URL = "https://universalis.com/europe.norway/{date}/readings.htm"
OSLO_TZ = ZoneInfo("Europe/Oslo")
OUTPUT_DIR = Path(__file__).parent.parent / "ordo"
DEBUG_DIR = Path(__file__).parent.parent / "debug"

# The other hours we try to locate on the oblates.se page, keyed by the name
# used in the output JSON. Each hour is matched by regexes against the first
# characters of a candidate <div id=…> container's text.
HOUR_DEFS = {
    "laudes": {
        "patterns": [r"\bLAUDES\b", r"\bMORGENB[ØO]NN\b"],
        "canticum": "Benedictus",
    },
    "middagsbønn": {
        "patterns": [r"\bMIDDAGSB[ØO]NN\b", r"\bTERS\b", r"\bSEKST\b", r"\bNON\b"],
        "canticum": None,
    },
    "vesper": {
        "patterns": [r"\bVESPER\b", r"\bKVELDSB[ØO]NN\b", r"\bAFTENB[ØO]NN\b"],
        "canticum": "Magnificat",
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; LiturgiaNorvegiaeBot/1.0; "
        "+https://github.com/CooperatoresVeritatis/Liturgia-Horarum-Norvegica)"
    )
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    """Strip psalm verse markers (* +) and normalise whitespace."""
    s = re.sub(r"\s*[*+]\s*", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def node_text(node) -> str:
    """Get visible text from a node, ignoring hidden alternative-hymn divs."""
    if isinstance(node, NavigableString):
        return str(node)
    if isinstance(node, Tag):
        # Skip hidden alternative hymn divs
        if node.get("style", "") and "display:none" in node.get("style", ""):
            return ""
        return node.get_text(separator=" ")
    return ""


def is_red_bold_header(tag) -> bool:
    """True for <font color="red"><b>…</b></font> — main section headers."""
    if not isinstance(tag, Tag) or tag.name != "font":
        return False
    color = tag.get("color", "").lower().strip()
    return color == "red" and bool(tag.find("b"))


def is_responsory_p(tag) -> bool:
    """True for <p style="color:red;">…<strong>Responsorium</strong>…</p>"""
    if not isinstance(tag, Tag) or tag.name != "p":
        return False
    style = tag.get("style", "")
    return "color:red" in style.replace(" ", "") and "Responsorium" in tag.get_text()


def is_responsory_header(tag) -> bool:
    """
    True for either the classic <p style="color:red;">…Responsorium…</p> form
    (Office of Readings) or the <font color="red|#fd1601">…Responsorium…</font>
    form used for Laudes/Middagsbønn/Vesper.
    """
    if is_responsory_p(tag):
        return True
    if isinstance(tag, Tag) and tag.name == "font":
        color = tag.get("color", "").lower().strip()
        return color in ("red", "#fd1601") and "Responsorium" in tag.get_text()
    return False


def is_strong_header(tag) -> bool:
    """
    True for <font color="red|#fd1601"><strong>…</strong></font> — the newer
    section-header markup used for Responsorium/Magnificat/Benedictus/Bønn/
    Fader vår/Avslutning in the other-hours pages. Kept separate from
    is_red_bold_header (which requires <b>) because that one also has to
    reject psalm sub-markers like <font color="red"><strong>II</strong></font>
    inside parse_psalms — callers outside the psalm-walking loop can safely
    treat both <b> and <strong> reds as section boundaries.
    """
    if not isinstance(tag, Tag) or tag.name != "font":
        return False
    color = tag.get("color", "").lower().strip()
    return color in ("red", "#fd1601") and bool(tag.find("strong"))


def is_section_header(tag) -> bool:
    """Any section-header boundary: the <b> or <strong> red/#fd1601 markup."""
    return is_red_bold_header(tag) or is_strong_header(tag)


def header_text(tag) -> str:
    """Return the bold text of a red section header."""
    b = tag.find("b") or tag.find("strong")
    return b.get_text(strip=True) if b else ""


# ---------------------------------------------------------------------------
# oblates.se parser
# ---------------------------------------------------------------------------

def fetch_html(session: requests.Session, url: str, attempts: int = 4) -> str:
    """GET a URL with retries and exponential backoff (15s, 30s, 60s)."""
    for attempt in range(1, attempts + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException as e:
            if attempt == attempts:
                raise
            wait = 15 * (2 ** (attempt - 1))
            log.warning(f"Fetch failed ({e}) — retry {attempt}/{attempts - 1} in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def fetch_oblates(session: requests.Session) -> BeautifulSoup:
    html = fetch_html(session, OBLATES_URL)
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / "oblates-main.html").write_text(html, encoding="utf-8")
    return BeautifulSoup(html, "lxml")


def collect_text_until_next_section(start_tag) -> str:
    """
    Walk siblings of start_tag and collect plain text until we hit another
    red-bold section header or a responsory <p>.
    """
    lines = []
    for sib in start_tag.next_siblings:
        if is_section_header(sib) or is_responsory_p(sib):
            break
        txt = node_text(sib).strip()
        if txt:
            lines.append(txt)
    return "\n".join(lines)


def parse_psalms(cmymain: Tag) -> list[dict]:
    """
    Parse the psalms (with antiphons) of an hour. Returns salmer.

    HTML pattern:
      <font color="red">Ant. 1</font> antiphon text <br/><br/>
      <font color="red"><strong>Salme 39</strong><br/>subtitle<br/><em>note</em><br/><br/>I</font>
      … psalm verses (NavigableStrings, <p>, <br/>, <font color="red">*</font>) …
      <font color="red"><strong>II</strong></font>   ← sub-section marker
      … more verses …
      <font color="red">Ant. 1</font> antiphon (closing repetition)
      <font color="red">Ant. 2</font> …
    """

    def is_ant_tag(tag):
        """<font color="red">Ant. N</font>"""
        if not isinstance(tag, Tag) or tag.name != "font":
            return False
        return tag.get("color", "").lower() == "red" and \
               bool(re.match(r"Ant\.\s*\d", tag.get_text(strip=True)))

    def is_psalm_font(tag):
        """
        <font color="red"><strong>Salme 39…</strong>…</font>
        OR <font color="red"><strong>II</strong></font>  (sub-section marker)
        Psalm headers use <strong>, NOT <b>.
        """
        if not isinstance(tag, Tag) or tag.name != "font":
            return False
        return tag.get("color", "").lower() == "red" and bool(tag.find("strong"))

    salmer = []

    # Collect opening antiphon tags (first occurrence of each number)
    seen_nums: set[str] = set()
    ant_openings = []  # (num_str, tag, antiphon_text)

    for tag in cmymain.find_all("font"):
        if not is_ant_tag(tag):
            continue
        num = re.search(r"\d", tag.get_text()).group()
        if num in seen_nums:
            continue
        seen_nums.add(num)

        # Antiphon text is the first NavigableString sibling after the tag
        ant_text = ""
        for sib in tag.next_siblings:
            if isinstance(sib, NavigableString):
                txt = str(sib).strip()
                if txt:
                    ant_text = txt
                    break
            elif isinstance(sib, Tag) and sib.name == "br":
                continue
            else:
                break
        ant_openings.append((num, tag, ant_text))

    # Parse each psalm: walk from its opening antiphon to its closing one
    last_psalm_ref = ""
    for num, opening_ant, antiphon in ant_openings:
        psalm_ref = ""
        psalm_lines = []
        collecting = False

        for sib in opening_ant.next_siblings:
            if isinstance(sib, Tag):
                # Hidden alternative-hymn divs — skip entirely
                if sib.name == "div" and "display:none" in sib.get("style", ""):
                    continue

                # Closing antiphon for this psalm number → done
                if is_ant_tag(sib) and re.search(r"\d", sib.get_text()).group() == num:
                    break

                # Main section headers (<font color="red"><b>…</b></font>) → done
                if is_red_bold_header(sib):
                    break

                # Responsory <p style="color:red;"> → done
                if is_responsory_p(sib):
                    break

                # Psalm/sub-section font (<font color="red"><strong>…</strong></font>)
                if is_psalm_font(sib):
                    strong_txt = sib.find("strong").get_text(strip=True)
                    if re.match(r"^[IVX]+$", strong_txt):
                        # Roman numeral = new stanza of the same psalm
                        if collecting and psalm_lines:
                            psalm_lines.append("")
                    else:
                        # "Salme 39" etc. — record psalm reference
                        psalm_ref = strong_txt
                    collecting = True
                    continue

                if not collecting:
                    continue

                # Verse markers (* +) — strip from text, don't add as own line
                if sib.name == "font" and sib.get_text(strip=True) in ("*", "+"):
                    continue

                if sib.name in ("br", "script", "style", "center"):
                    continue

                if sib.name == "p":
                    txt = clean_text(sib.get_text())
                    if txt:
                        psalm_lines.append(txt)
                else:
                    txt = clean_text(sib.get_text())
                    if txt:
                        psalm_lines.append(txt)

            elif isinstance(sib, NavigableString) and collecting:
                txt = clean_text(str(sib))
                if txt:
                    psalm_lines.append(txt)

        psalm_lines = [l for l in psalm_lines if l.strip()]
        # If no new psalm title was found, this section continues the previous psalm
        if not psalm_ref and last_psalm_ref:
            psalm_ref = last_psalm_ref
        elif psalm_ref:
            last_psalm_ref = psalm_ref
        salmer.append({
            "referanse": psalm_ref,
            "antifon": antiphon,
            "tekst": psalm_lines,
        })

    return salmer


def parse_versicle(container: Tag, before_header: str) -> dict:
    """
    Parse the ℣/℟ pair that sits immediately before the red-bold header whose
    text contains `before_header` (e.g. "Første lesning").
    """
    vers = {"v": "", "r": ""}
    header = container.find(
        lambda t: is_red_bold_header(t) and before_header in header_text(t)
    )
    if header:
        # previous_siblings iterates nearest → farthest, so we get the
        # versicle pair that sits immediately before the reading header.
        for sib in header.previous_siblings:
            if isinstance(sib, Tag) and sib.name == "strong":
                if not vers["r"]:
                    vers["r"] = clean_text(sib.get_text())
            elif isinstance(sib, NavigableString):
                txt = clean_text(str(sib))
                if txt and not vers["v"] and "$" not in txt and len(txt) < 200:
                    vers["v"] = txt
            if vers["v"] and vers["r"]:
                break
    return vers


def parse_reading(header_tag: Tag) -> dict:
    """
    Parse a reading (first or second) from its red-bold header tag.

    Header structure:
      <font color="red"><b>Første lesning</b><br/>Åp 2,12-29<br/>Fra…<br/><em>title</em></font>

    or for patristic:
      <font color="red"><b>Annen lesning</b><br/>Fra en preken av pave Leo…<br/><em>title</em></font>
    """
    # Extract reference / source / title from within the header tag itself
    header_html = str(header_tag)
    header_soup = BeautifulSoup(header_html, "lxml")

    # Get all text nodes inside the header (excluding the <b> label itself)
    inner_lines = []
    for child in header_soup.find("font").children:
        if isinstance(child, NavigableString):
            t = str(child).strip()
            if t:
                inner_lines.append(t)
        elif isinstance(child, Tag):
            if child.name == "b":
                continue  # skip the "Første lesning" / "Annen lesning" label
            t = child.get_text(strip=True)
            if t:
                inner_lines.append(t)

    referanse = inner_lines[0] if inner_lines else ""
    kilde_tittel = " — ".join(inner_lines[1:]) if len(inner_lines) > 1 else ""

    # Collect reading text: siblings after header until next responsory or section
    text_parts = []
    for sib in header_tag.next_siblings:
        if is_responsory_header(sib) or is_section_header(sib):
            break
        if isinstance(sib, Tag):
            if sib.name in ("br",):
                continue
            txt = sib.get_text(separator="\n").strip()
        elif isinstance(sib, NavigableString):
            txt = str(sib).strip()
        else:
            continue
        if txt:
            text_parts.append(txt)

    return {
        "referanse": referanse,
        "kilde": kilde_tittel,
        "tekst": "\n".join(text_parts).strip(),
    }


def parse_responsory(resp_p: Tag) -> dict:
    """
    Parse a responsory. Structure:
      <p style="color:red;"><em><strong>Responsorium</strong> Ref</em></p>
      plain versicle text <br/>
      <strong>* response text</strong> <br/>
      plain versicle 2 <br/>
      <strong>* response text (repeated)</strong> <br/>
    """
    ref = resp_p.get_text(strip=True).replace("Responsorium", "").strip()

    v_parts = []
    r_text = ""

    for sib in resp_p.next_siblings:
        if is_section_header(sib) or is_responsory_header(sib):
            break
        # Stop at a red antiphon marker (e.g. "Ant. til Benedictus" after the
        # responsory of Laudes/Vesper)
        if isinstance(sib, Tag) and sib.name == "font" and \
                sib.get("color", "").lower() == "red" and \
                sib.get_text(strip=True).startswith("Ant."):
            break
        if isinstance(sib, Tag):
            if sib.name == "strong":
                r_raw = clean_text(sib.get_text())
                r_raw = re.sub(r"^\*\s*", "", r_raw)
                if not r_text:
                    r_text = r_raw  # take first occurrence as the response
            elif sib.name == "br":
                continue
            else:
                txt = clean_text(sib.get_text())
                if txt:
                    v_parts.append(txt)
        elif isinstance(sib, NavigableString):
            txt = clean_text(str(sib))
            if txt:
                v_parts.append(txt)

    return {
        "ref": ref,
        "v": " ".join(v_parts).strip(),
        "r": r_text,
    }


def parse_hymne(container: Tag) -> str:
    """Parse the hymn of an hour from its red-bold 'Hymne' header."""
    hymne_header = container.find(
        lambda t: is_red_bold_header(t) and header_text(t) == "Hymne"
    )
    if not hymne_header:
        return ""
    lines = []
    for sib in hymne_header.next_siblings:
        # Stop at first antiphon or next red-bold section
        if is_red_bold_header(sib):
            break
        if isinstance(sib, Tag):
            # Skip hidden alternative hymns
            if sib.name == "div" and "display:none" in sib.get("style", ""):
                continue
            # Stop at first antiphon marker
            if sib.name == "font" and re.match(r"Ant\.\s*\d", sib.get_text(strip=True)):
                break
            if sib.name == "font" and sib.get("color", "").lower() in ("#fd1601", "red"):
                txt = sib.get_text(strip=True)
                if txt.startswith("eller"):
                    break  # stop before "eller:" links
            if sib.name in ("script", "style"):
                continue
            txt = sib.get_text(separator="\n").strip()
            if txt:
                lines.append(txt)
        elif isinstance(sib, NavigableString):
            txt = str(sib).strip()
            if txt:
                lines.append(txt)
    return "\n".join(l for l in lines if l)


def parse_oblates(soup: BeautifulSoup) -> dict:
    gl = soup.find("div", id="gl")
    if not gl:
        raise ValueError("div#gl (LG section) not found on oblates.se page")
    cmymain = gl.find("div", id="cmymain")
    if not cmymain:
        raise ValueError("div#cmymain not found inside div#gl")

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

    # --- Feast ---------------------------------------------------------------
    blue = cmymain.find("font", color="blue")
    if blue:
        result["feast"] = blue.get_text(strip=True)

    # --- Hymn ----------------------------------------------------------------
    result["hymne"] = parse_hymne(cmymain)

    # --- Psalms + versicle ---------------------------------------------------
    result["salmer"] = parse_psalms(cmymain)
    vers = parse_versicle(cmymain, "Første lesning")
    result["vers"] = vers if (vers["v"] or vers["r"]) else None

    # --- First reading -------------------------------------------------------
    l1_header = cmymain.find(
        lambda t: is_red_bold_header(t) and "Første lesning" in header_text(t)
    )
    if l1_header:
        result["lesning1"] = parse_reading(l1_header)

    # --- Responsories (in order) ---------------------------------------------
    resp_tags = cmymain.find_all(is_responsory_p)
    if len(resp_tags) >= 1:
        result["responsorium1"] = parse_responsory(resp_tags[0])
    if len(resp_tags) >= 2:
        result["responsorium2"] = parse_responsory(resp_tags[1])

    # --- Second reading ------------------------------------------------------
    l2_header = cmymain.find(
        lambda t: is_red_bold_header(t) and "Annen lesning" in header_text(t)
    )
    if l2_header:
        result["lesning2"] = parse_reading(l2_header)

    # --- Te Deum (Sundays and solemnities) -----------------------------------
    te_header = cmymain.find(
        lambda t: is_red_bold_header(t) and "Te Deum" in header_text(t)
    )
    if te_header:
        result["teDeum"] = collect_text_until_next_section(te_header)

    # --- Closing prayer ------------------------------------------------------
    bønn_header = cmymain.find(
        lambda t: is_red_bold_header(t) and header_text(t) == "Bønn"
    )
    if bønn_header:
        result["bønn"] = collect_text_until_next_section(bønn_header)

    return result


# ---------------------------------------------------------------------------
# Other hours (Laudes, Middagsbønn, Vesper)
# ---------------------------------------------------------------------------

def container_has_content(div: Tag) -> bool:
    """
    True if a div holds real liturgical content rather than a navigation menu
    that merely mentions the hour names: it must contain at least one red-bold
    section header (Hymne, Bønn, …) and a substantial amount of text.
    """
    return div.find(is_red_bold_header) is not None and \
        len(div.get_text(strip=True)) > 500


def find_hour_container(soup: BeautifulSoup, patterns: list[str]) -> Tag | None:
    """
    Find the <div id=…> whose text *starts* with one of the hour-name patterns
    and actually contains liturgical content (nav menus are rejected).
    Matching only the first characters keeps page-wide wrapper divs (whose text
    starts with the first hour on the page) from matching every hour.

    The page also nests every hour's own container inside a couple of
    page-wide wrapper divs (id="mymain", "printingstuff", …) whose text starts
    with the nav bar ("LG Laudes Ters Sekst Non Vesper Compl. …") — that nav
    mention alone can satisfy a pattern even when the hour has no section of
    its own on the page (e.g. Laudes on a solemnity that only has Ters/Sekst/
    Non/Vesper). Such a wrapper is recognizable because it contains *other*
    div ids that independently hold substantial content (i.e. more than one
    hour's worth) — reject those aggregate wrappers in favor of a real,
    single-hour div, if one matched.
    """
    all_content_divs = [d for d in soup.find_all("div", id=True) if container_has_content(d)]

    candidates = []
    for div in all_content_divs:
        head = div.get_text(" ", strip=True)[:150].upper()
        if any(re.search(p, head) for p in patterns):
            candidates.append(div)
    if not candidates:
        return None

    def nested_content_divs(div):
        return [d for d in all_content_divs if d is not div and d in div.descendants]

    leaf_candidates = [d for d in candidates if not nested_content_divs(d)]
    if not leaf_candidates:
        # Every candidate is an aggregate wrapper nesting other hours' real
        # containers — there's no dedicated container for this hour on the
        # page (e.g. Laudes isn't published separately for some feasts).
        return None
    return min(leaf_candidates, key=lambda d: len(d.get_text()))


def find_hour_link(soup: BeautifulSoup, patterns: list[str]) -> str | None:
    """
    If an hour lives behind its own URL, find a same-site link whose text
    matches the hour name and return the absolute URL.
    """
    for a in soup.find_all("a", href=True):
        txt = a.get_text(" ", strip=True).upper()
        if not txt or len(txt) > 40:
            continue
        if any(re.search(p, txt) for p in patterns):
            url = urljoin(OBLATES_URL, a["href"])
            if urlparse(url).netloc == urlparse(OBLATES_URL).netloc and "#" not in a["href"]:
                return url
    return None


def _is_bare_ant_marker(tag) -> bool:
    """<font color="red">Ant.</font> — the canticle's own antiphon marker
    (unlike the numbered psalm markers "Ant. 1", "Ant. 2", …)."""
    if not isinstance(tag, Tag) or tag.name != "font":
        return False
    return tag.get("color", "").lower().strip() == "red" and \
        bool(re.match(r"^Ant\.?$", tag.get_text(strip=True)))


def parse_canticum(container: Tag, name: str) -> dict | None:
    """
    Parse the Gospel canticle (Benedictus / Magnificat). Real layout:
      <font color="red">Ant.</font> antiphon text <br/><br/>
      <font color="red|#fd1601"><b|strong>Magnificat …</strong></font>
      … canticle verses ("*" separators, <p> lines) …
      <font color="red">Ant.</font>  (closing repetition)
    The antiphon marker and the canticle header are separate tags, so this
    first locates the header (by name), then looks back for the nearest
    bare "Ant." marker to pull the antiphon text.
    """
    header = container.find(
        lambda t: is_section_header(t) and name.lower() in t.get_text().lower()
    )
    if not header:
        return None

    antifon = ""
    for sib in header.previous_siblings:
        if _is_bare_ant_marker(sib):
            for after in sib.next_siblings:
                if after is header:
                    break
                if isinstance(after, NavigableString):
                    txt = str(after).strip()
                    if txt:
                        antifon = txt
                        break
                elif isinstance(after, Tag) and after.name != "br":
                    break
            break
        if is_section_header(sib):
            break

    tekst_lines = []
    for sib in header.next_siblings:
        if _is_bare_ant_marker(sib) or is_section_header(sib):
            break
        if isinstance(sib, Tag) and sib.name == "font" and \
                sib.get_text(strip=True) in ("*", "+"):
            continue
        txt = clean_text(node_text(sib)) if not isinstance(sib, NavigableString) \
            else clean_text(str(sib))
        if txt:
            tekst_lines.append(txt)

    return {"antifon": antifon, "tekst": tekst_lines}


def parse_section(container: Tag, header_substr: str) -> str:
    """Collect the text of a section header (Forbønner, Bønn, …) — either the
    <b> or the newer <strong> red/#fd1601 markup."""
    header = container.find(
        lambda t: is_section_header(t) and header_substr in header_text(t)
    )
    return collect_text_until_next_section(header) if header else ""


def parse_hour(container: Tag, canticum: str | None) -> dict:
    """
    Generic parser for Laudes / Middagsbønn / Vesper. Reuses the same building
    blocks as the Office of Readings: hymn, psalms with antiphons, a short
    reading, responsory, Gospel canticle, intercessions and closing prayer.
    """
    result = {
        "hymne": parse_hymne(container),
        "salmer": parse_psalms(container),
        "lesning": None,
        "responsorium": None,
        "canticum": None,
        "forbønner": "",
        "bønn": "",
    }

    lesning_header = container.find(
        lambda t: is_red_bold_header(t) and "lesning" in header_text(t).lower()
    )
    if lesning_header:
        result["lesning"] = parse_reading(lesning_header)

    resp_tags = container.find_all(is_responsory_header)
    if resp_tags:
        result["responsorium"] = parse_responsory(resp_tags[0])

    if canticum:
        result["canticum"] = parse_canticum(container, canticum)

    result["forbønner"] = parse_section(container, "Forbønner")
    result["bønn"] = parse_section(container, "Bønn")

    return result


def scrape_other_hours(session: requests.Session, soup: BeautifulSoup) -> dict:
    """
    Locate and parse Laudes, Middagsbønn and Vesper. Never raises: an hour that
    cannot be found or parsed becomes None, and the page's div structure is
    logged so the layout can be verified from the workflow logs / debug artifact.
    """
    hours: dict[str, dict | None] = {}
    structure_logged = False

    def log_structure_once():
        nonlocal structure_logged
        if structure_logged:
            return
        structure_logged = True
        ids = [d.get("id") for d in soup.find_all("div", id=True)]
        log.warning(f"div ids on page: {ids}")
        links = [
            f"{a.get_text(' ', strip=True)[:30]} → {a['href']}"
            for a in soup.find_all("a", href=True)
            if "breviar" in a["href"] or len(a.get_text(strip=True)) < 30
        ]
        log.warning(f"candidate links on page: {links[:40]}")

    for key, hour_def in HOUR_DEFS.items():
        try:
            container = find_hour_container(soup, hour_def["patterns"])
            if container is None:
                # Hour might live on its own page — follow a matching link once.
                link = find_hour_link(soup, hour_def["patterns"])
                if link:
                    log.info(f"{key}: not on main page, following {link}")
                    html = fetch_html(session, link)
                    DEBUG_DIR.mkdir(exist_ok=True)
                    (DEBUG_DIR / f"oblates-{key}.html").write_text(html, encoding="utf-8")
                    sub_soup = BeautifulSoup(html, "lxml")
                    cmymain = sub_soup.find("div", id="cmymain")
                    if cmymain is not None and container_has_content(cmymain):
                        container = cmymain
                    else:
                        container = find_hour_container(sub_soup, hour_def["patterns"])
            if container is None:
                log.warning(f"{key}: no content container found — skipping")
                log_structure_once()
                hours[key] = None
                continue
            parsed = parse_hour(container, hour_def["canticum"])
            # An hour with no psalms, no reading and no prayer is a failed
            # parse (e.g. we matched the wrong element) — store null, not an
            # empty shell.
            if not parsed["salmer"] and not parsed["lesning"] and \
                    not parsed["hymne"] and not parsed["bønn"]:
                log.warning(f"{key}: container matched but parsed empty — skipping")
                log_structure_once()
                hours[key] = None
                continue
            hours[key] = parsed
            log.info(
                f"{key}: parsed — {len(parsed['salmer'])} psalms, "
                f"lesning={bool(parsed['lesning'])}"
            )
        except Exception as e:
            log.warning(f"{key}: parse failed ({e}) — skipping")
            hours[key] = None

    return hours


# ---------------------------------------------------------------------------
# universalis.com cross-reference
# ---------------------------------------------------------------------------

def fetch_universalis(session: requests.Session, target_date: date) -> dict:
    url = UNIVERSALIS_URL.format(date=target_date.strftime("%Y%m%d"))
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning(f"Universalis fetch failed: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "lxml")
    result = {"feast_en": "", "reading1_ref": "", "reading2_source": ""}

    title = soup.find("h1") or soup.find("h2")
    if title:
        result["feast_en"] = title.get_text(strip=True)

    # Reading references: look for scripture-like citations
    refs = [
        t.get_text(strip=True)
        for t in soup.find_all(True)
        if re.search(r"\d:\d+", t.get_text()) and len(t.get_text(strip=True)) < 40
    ]
    if refs:
        result["reading1_ref"] = refs[0]

    return result


# ---------------------------------------------------------------------------
# Mismatch check
# ---------------------------------------------------------------------------

def check_mismatch(no_data: dict, en_data: dict) -> bool:
    if not en_data:
        return False
    feast_keywords = {"feast", "solemnity", "memorial", "høytid", "fest", "minnedag"}
    no_has = any(k in no_data.get("feast", "").lower() for k in feast_keywords)
    en_has = any(k in en_data.get("feast_en", "").lower() for k in feast_keywords)
    if no_has != en_has:
        log.warning(f"Feast type mismatch — NO: '{no_data.get('feast')}' / EN: '{en_data.get('feast_en')}'")
        return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # "Today" in Oslo — the runner's clock is UTC, which is a day behind Oslo
    # between midnight and 01:00/02:00 Oslo time.
    target_date = datetime.now(OSLO_TZ).date()
    force = False
    for arg in sys.argv[1:]:
        if arg == "--force":
            force = True
            continue
        try:
            target_date = datetime.strptime(arg, "%Y-%m-%d").date()
        except ValueError:
            log.error("Usage: scrape_lesning.py [YYYY-MM-DD] [--force]")
            sys.exit(1)

    date_str = target_date.isoformat()
    output_path = OUTPUT_DIR / f"{date_str}.json"

    if output_path.exists() and not force:
        log.info(f"{output_path} already exists — use --force to re-scrape")
        sys.exit(0)

    log.info(f"Scraping for {date_str}…")

    with requests.Session() as session:
        log.info("Fetching oblates.se…")
        oblates_soup = fetch_oblates(session)
        no_data = parse_oblates(oblates_soup)

        log.info("Scraping other hours (Laudes, Middagsbønn, Vesper)…")
        hours = scrape_other_hours(session, oblates_soup)

        log.info("Fetching universalis.com…")
        en_data = fetch_universalis(session, target_date)

    mismatch = check_mismatch(no_data, en_data)

    ordo = {
        "date": date_str,
        "feast": no_data.get("feast", ""),
        "feast_en": en_data.get("feast_en", ""),
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
        "laudes": hours.get("laudes"),
        "middagsbønn": hours.get("middagsbønn"),
        "vesper": hours.get("vesper"),
    }

    OUTPUT_DIR.mkdir(exist_ok=True)

    # Remove stale file if exists (shouldn't happen due to check above, but be safe)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(ordo, f, ensure_ascii=False, indent=2)

    log.info(f"Written → {output_path}")
    if mismatch:
        log.warning("⚠ mismatch_flag set — review before publishing")

    # Quick summary
    log.info(f"  feast:        {ordo['feast']}")
    log.info(f"  hymne:        {len(ordo['hymne'])} chars")
    log.info(f"  salmer:       {len(ordo['salmer'])} psalms")
    log.info(f"  lesning1:     {bool(ordo['lesning1'])}")
    log.info(f"  lesning2:     {bool(ordo['lesning2'])}")
    log.info(f"  bønn:         {len(ordo['bønn'])} chars")
    log.info(f"  laudes:       {bool(ordo['laudes'])}")
    log.info(f"  middagsbønn:  {bool(ordo['middagsbønn'])}")
    log.info(f"  vesper:       {bool(ordo['vesper'])}")


if __name__ == "__main__":
    main()
