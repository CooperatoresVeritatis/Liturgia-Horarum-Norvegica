#!/usr/bin/env python3
"""
Regression test for the oblates.se parser.

The fixture below reproduces the container layout the live page actually uses
(observed in the Daily Ordo workflow logs): every hour sits in its own tab on a
single page, each tab holding an inner "…mymain" div, and the nav bar switches
between them with href="#" links — so there is no per-hour URL to fetch.

    gl → cmymain    Lesningsgudstjenesten     ve → vmymain   Vesper
    la → llmymain   Laudes                    co → kmymain   Kompletorium
    te/se/no → tmymain/smymain/nmymain        Ters, Sekst, Non

Laudes and the little hours head their sections with <font color="#FD1601">
<strong> rather than the <b> markup the Office of Readings uses; requiring <b>
once made those containers invisible and every hour but Vesper came out null.

Run: python tests/test_parse_structure.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from bs4 import BeautifulSoup  # noqa: E402
import scrape_lesning as S  # noqa: E402

FILL = "Salmevers med mange ord for å nå lengdekravet i innholdssjekken. " * 12


def hour_tab(outer, inner, title, header_tag, canticum=None, reading=True):
    """Build one hour's tab: outer tab > fontlinks + inner content div."""
    def h(text):
        return f'<font color="#FD1601"><{header_tag}>{text}</{header_tag}></font>'

    html = f'<div id="{outer}"><div id="fontlinks">A+ A A-</div><div id="{inner}">'
    html += f"{title}<br/>"
    html += h("Hymne") + f"Hymnetekst for {title}.<br/>"
    html += f'<font color="red">Ant. 1</font> Antifon for {title}<br/><br/>'
    html += '<font color="red"><strong>Salme 118</strong></font>' + FILL + "<br/>"
    html += f'<font color="red">Ant. 1</font> Antifon for {title}'
    if reading:
        html += (f'<font color="#FD1601"><{header_tag}>Kort lesning</{header_tag}>'
                 f"<br/>Rom 8,35</font>Lesningstekst her.")
        html += h("Responsorium") + "Versikkelen her<br/><strong>* responsen her.</strong><br/>"
    if canticum:
        html += f'<font color="red">Ant.</font>Antifonen til {canticum}.<br/><br/>'
        html += h(canticum) + f"{canticum}-teksten linje en.<br/>"
        html += h("Forbønner") + "La oss be til Herren.<br/>"
    html += h("Bønn") + f"Avslutningsbønn for {title}.<br/>"
    return html + "</div></div>"


NAV = ('<div id="meny_container">'
       '<a href="#">LG</a><a href="#">Laudes</a><a href="#">Ters</a>'
       '<a href="#">Sext</a><a href="#">Non</a><a href="#">Vesper</a>'
       '<a href="#">Compl.</a></div>')

LG = ('<div id="gl"><div id="fontlinks">A+</div><div id="cmymain">'
      '<font color="blue">torsdag - uke I</font>'
      '<font color="red"><b>Hymne</b></font>LG-hymne.<br/>'
      '<font color="red">Ant. 1</font> LG-antifon<br/><br/>'
      '<font color="red"><strong>Salme 44</strong></font>' + FILL + '<br/>'
      '<font color="red">Ant. 1</font> LG-antifon'
      '<font color="red"><b>Første lesning</b><br/>Jer 1,1-10</font>Første lesningstekst.'
      '<p style="color:red;"><em><strong>Responsorium</strong> Jer 1,5</em></p>'
      'Versikkel<br/><strong>* respons.</strong><br/>'
      '<font color="red"><b>Annen lesning</b><br/>Fra en preken</font>Patristisk tekst.'
      '<font color="red"><b>Bønn</b></font>LG-bønn.'
      '</div></div>')

PAGE = ('<html><body><div id="mymain"><div id="printingstuff">' + NAV + LG
        + hour_tab("la", "llmymain", "LAUDES", "strong", canticum="Benedictus")
        + hour_tab("te", "tmymain", "TERS", "strong")
        + hour_tab("se", "smymain", "SEKST", "strong")
        + hour_tab("no", "nmymain", "NON", "strong")
        + hour_tab("ve", "vmymain", "VESPER", "strong", canticum="Magnificat")
        + "</div></div></body></html>")


class FakeSession:
    """scrape_other_hours only uses the session to follow per-hour links, and
    this page has none — any use would be a bug, so fail loudly."""

    def get(self, *args, **kwargs):
        raise AssertionError("no per-hour link should be followed for this page")


def main():
    soup = BeautifulSoup(PAGE, "lxml")

    lg = S.parse_oblates(soup)
    assert lg["feast"] == "torsdag - uke I", lg["feast"]
    assert lg["salmer"], "Office of Readings lost its psalms"
    assert lg["lesning1"] and lg["lesning1"]["referanse"] == "Jer 1,1-10", lg["lesning1"]
    assert lg["lesning2"], "second reading missing"
    assert lg["bønn"], "closing prayer missing"

    hours = S.scrape_other_hours(FakeSession(), soup)
    for key in ("laudes", "middagsbønn", "vesper"):
        hour = hours[key]
        assert hour is not None, f"{key} came out null"
        assert hour["hymne"], f"{key}: hymn missing"
        assert hour["salmer"], f"{key}: psalms missing"
        assert hour["lesning"], f"{key}: reading missing"
        assert hour["lesning"]["referanse"] == "Rom 8,35", hour["lesning"]
        assert hour["responsorium"], f"{key}: responsory missing"
        assert hour["bønn"], f"{key}: prayer missing"

    for key, name in (("laudes", "Benedictus"), ("vesper", "Magnificat")):
        canticum = hours[key]["canticum"]
        assert canticum, f"{key}: {name} missing"
        assert canticum["antifon"] == f"Antifonen til {name}.", canticum
        assert canticum["tekst"], f"{key}: {name} has no verses"

    # The mid-hour has no Gospel canticle and no intercessions.
    assert hours["middagsbønn"]["canticum"] is None
    assert not hours["middagsbønn"]["forbønner"]

    print("All parser structure checks passed.")


if __name__ == "__main__":
    main()
