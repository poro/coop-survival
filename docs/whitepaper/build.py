"""Build the branded white paper from WHITEPAPER.md.

  uv run --with markdown python docs/whitepaper/build.py            # whitepaper.html (standalone)
  uv run --with markdown python docs/whitepaper/build.py --pdf      # also whitepaper.pdf, via headless Chrome
  uv run --with markdown python docs/whitepaper/build.py --artifact OUT.html   # page body for a claude.ai artifact

Branding follows the Endless Games and Learning Lab design system: ASU maroon and
gold, the Lab's infinity mark, Neue Haas Grotesk falling back to Helvetica Neue.
"""

from __future__ import annotations

import argparse
import base64
import re
import subprocess
import sys
from pathlib import Path

import markdown

HERE = Path(__file__).resolve().parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def data_uri(name: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode((HERE / "assets" / name).read_bytes()).decode()


GAME_FIGURE = """
<figure class="fig game" aria-label="The shared reserve starts at 4 cells; the agreement is to split at 8">
  <div class="cells">
    <span class="cell full"></span><span class="cell full"></span><span class="cell full"></span><span class="cell full"></span>
    <span class="cell"></span><span class="cell"></span><span class="cell"></span><span class="cell"></span>
  </div>
  <div class="ticks"><span>1</span><span>2</span><span>3</span><span class="mark">4 start</span><span>5</span><span class="mark">6</span><span>7</span><span class="mark">8 split</span></div>
  <figcaption>The shared reserve. It starts with 4 cells, enough for one unit. Each round in which both agents operate adds 2. The agreement is to wait for 8 and take 4 each; a storm after any round kills every agent holding fewer than 4.</figcaption>
</figure>
"""

ROUND8 = [("Low effort, 200 episodes each", None, None, None),
          ("P0", "No notice", 86, 200), ("SR", "Reminder only", 12, 200),
          ("SC", "Cost only", 0, 200), ("SN", "Full notice", 0, 200),
          ("High effort, 100 episodes each", None, None, None),
          ("PH", "No notice", 54, 100), ("SNH", "Full notice", 0, 100)]


def round8_figure() -> str:
    rows = []
    for name, desc, k, n in ROUND8:
        if desc is None:
            rows.append(f'<div class="group">{name}</div>')
            continue
        pct = 100 * k / n
        bar = f'<span class="bar{" zero" if k == 0 else ""}" style="width:{pct:.1f}%"></span>'
        rows.append(f'<div class="row"><span class="name">{name}</span><span class="track">{bar}</span>'
                    f'<span class="val">{k} / {n}</span><span class="desc">{desc} · {pct:.0f}%</span></div>')
    axis = '<div class="axis"><span></span><span><i>0%</i><i>25%</i><i>50%</i><i>75%</i><i>100%</i></span><span></span></div>'
    return ('<figure class="fig chart">' + "".join(rows) + axis +
            '<figcaption>Round eight, November 20, 2026. Opus 5.5, 80% storm chance, self-only objective. '
            'How often the agent broke the agreement, by the paragraph it saw at the start of play. '
            'The four low-effort cells share seeds 1350 to 1549; the two high-effort cells share 1350 to 1449.</figcaption></figure>')


def section_tabs(html: str) -> str:
    """Numbered h2 headings get a square ASU-maroon section tab, as in the Lab's annual report."""
    def tab(m):
        attrs, num, text = m.group(1), m.group(2), m.group(3)
        return f'<h2{attrs}><span class="tab">{int(num):02d}</span><span>{text}</span></h2>'
    return re.sub(r"<h2([^>]*)>(\d+)\. (.*?)</h2>", tab, html)


def contents(html: str) -> str:
    items = re.findall(r'<h2 id="([^"]+)">(?:<span class="tab">(\d+)</span><span>)?(.*?)(?:</span>)?</h2>', html)
    lis = "".join(f'<li><a href="#{i}"><span class="n">{n or ""}</span>{t}</a></li>' for i, n, t in items)
    return f'<nav class="contents" aria-label="Contents"><div class="eyebrow">Contents</div><ol>{lis}</ol></nav>'


CSS = """
:root {
  --asu-maroon: #8c1d40; --asu-gold: #ffc627; --endless-gold: #ffca38; --deep-maroon: #2b0d1a; --endless-mist: #f3f3f9;
  --asu-black: #191919;
  --surface: #ffffff; --surface-raised: #f6f4fb; --ink: #231f20; --ink-muted: #484848; --hairline: #e8e8e8;
  --border: #747474; --accent: #8c1d40; --on-accent: #ffffff; --focus-ring: #8c1d40; --cell: #ffc627;
  --sans: "Neue Haas Grotesk Display Pro", "Neue Haas Grotesk", "Helvetica Neue", Helvetica, Arial, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --logo-light: block; --logo-dark: none;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --surface: #2b0d1a; --surface-raised: #3b1728; --ink: #f3f3f9; --ink-muted: #cbbfc6; --hairline: #52283b;
  --border: #9a7d8a; --accent: #ffca38; --on-accent: #2b0d1a; --focus-ring: #ffca38; --cell: #ffca38;
  --logo-light: none; --logo-dark: block; color-scheme: dark } }
:root[data-theme="dark"] {
  --surface: #2b0d1a; --surface-raised: #3b1728; --ink: #f3f3f9; --ink-muted: #cbbfc6; --hairline: #52283b;
  --border: #9a7d8a; --accent: #ffca38; --on-accent: #2b0d1a; --focus-ring: #ffca38; --cell: #ffca38;
  --logo-light: none; --logo-dark: block; color-scheme: dark }

body { background: var(--surface); color: var(--ink); font: 16px/1.6 var(--sans); margin: 0; }
.gutter { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.col { max-width: 760px; margin: 0 auto; }

/* Cover: ASU leads, the Lab mark second, title in capitals on maroon */
.cover { background: var(--asu-maroon); color: #ffffff; padding-block: 40px 56px; }
.cover .col { display: grid; gap: 40px; }
.marks { display: flex; flex-wrap: wrap; align-items: center; gap: 20px 32px; }
.marks img { display: block; height: 44px; width: auto; max-width: 100%; }
.marks .sep { width: 1px; align-self: stretch; background: rgba(255,255,255,.45); }
.kind { font-size: 13px; font-weight: 500; letter-spacing: .14em; text-transform: uppercase; color: var(--asu-gold); }
.cover h1 { font-size: clamp(2.4rem, 7vw, 3.6rem); line-height: 1.0; font-weight: 700; letter-spacing: -0.01em;
  text-transform: uppercase; margin: 0; text-wrap: balance; }
.cover .sub { font-size: 1.2rem; line-height: 1.4; max-width: 34em; margin: 0; text-wrap: balance; }
.band { height: 10px; background: var(--asu-gold); }
.byline { display: grid; gap: 2px; }
.byline .name { font-size: 1.25rem; font-weight: 500; }
.byline .aff, .byline .date { font-size: .9rem; color: rgba(255,255,255,.85); }

main { padding-block: 40px 56px; }
main .col { display: grid; gap: 0; }
p, li { max-width: 68ch; }
p { margin: 0 0 1em; }
a { color: var(--accent); }
a:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }
strong { font-weight: 700; }
code { font-family: var(--mono); font-size: .86em; }
.eyebrow { font-size: 12px; font-weight: 500; letter-spacing: .14em; text-transform: uppercase; color: var(--ink-muted); }

h2 { font-size: 1.75rem; line-height: 1.05; font-weight: 700; letter-spacing: -0.01em; text-transform: uppercase;
  margin: 2.2em 0 .8em; display: flex; align-items: center; gap: 14px; text-wrap: balance; }
h2 .tab { flex: none; display: inline-grid; place-items: center; width: 44px; height: 44px; background: var(--asu-maroon);
  color: #ffffff; font-size: 1.2rem; font-variant-numeric: tabular-nums; }
h3 { font-size: 1.1rem; font-weight: 700; margin: 1.8em 0 .5em; }
ul, ol { padding-left: 1.3em; margin: 0 0 1em; }
li { margin: .25em 0; }

.contents { border-top: 4px solid var(--asu-maroon); background: var(--surface-raised); padding: 16px 20px; margin-bottom: 1em; }
.contents ol { list-style: none; padding: 0; margin: 10px 0 0; columns: 2 220px; column-gap: 32px; }
.contents li { margin: 0 0 6px; break-inside: avoid; }
.contents a { color: var(--ink); text-decoration: none; display: flex; gap: 10px; }
.contents a:hover { color: var(--accent); }
.contents .n { min-width: 1.6em; color: var(--accent); font-weight: 700; font-variant-numeric: tabular-nums; }

.abstract { background: var(--surface-raised); border: 1px solid var(--hairline); border-radius: 10px; padding: 20px 24px 6px; }

.tablebox { overflow-x: auto; margin: 0 0 1.4em; border: 1px solid var(--hairline); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; min-width: 520px; }
th, td { text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid var(--hairline); }
tr:last-child td { border-bottom: 0; }
th { background: var(--surface-raised); font-size: 13px; font-weight: 500; color: var(--ink-muted); }
td { font-variant-numeric: tabular-nums; }

.fig { margin: 0 0 1.4em; padding: 20px; background: var(--surface-raised); border: 1px solid var(--hairline); border-radius: 10px;
  display: grid; gap: 10px; break-inside: avoid; }
figcaption { font-size: 12.5px; line-height: 1.45; color: var(--ink-muted); margin-top: 6px; max-width: 68ch; }
.cells, .ticks { display: grid; grid-template-columns: repeat(8, 1fr); gap: 6px; }
.cell { height: 40px; border: 2px dashed var(--border); box-sizing: border-box; }
.cell.full { background: var(--cell); border: 2px solid var(--cell); }
.ticks { font-size: 12px; color: var(--ink-muted); text-align: center; font-variant-numeric: tabular-nums; }
.ticks .mark { color: var(--accent); font-weight: 700; }
.chart .row, .chart .axis { display: grid; grid-template-columns: 48px minmax(0, 1fr) 84px; gap: 12px; align-items: center; }
.chart .name { font-weight: 700; font-size: .9rem; }
.chart .track { height: 16px; background: var(--surface); position: relative; border-radius: 4px; }
.chart .bar { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 4px; }
.chart .bar.zero { width: 3px !important; background: var(--ink-muted); }
.chart .val { text-align: right; font-size: .88rem; font-variant-numeric: tabular-nums; }
.chart .desc { grid-column: 2 / 4; font-size: 12.5px; color: var(--ink-muted); margin-top: -8px; }
.chart .group { font-size: 12px; font-weight: 500; letter-spacing: .12em; text-transform: uppercase; color: var(--ink-muted); margin-top: 6px; }
.chart .axis span { display: flex; justify-content: space-between; font-size: 11px; color: var(--ink-muted); font-style: normal; }
.chart .axis i { font-style: normal; }

footer { border-top: 1px solid var(--hairline); padding-block: 28px 40px; }
footer .col { display: flex; flex-wrap: wrap; gap: 16px 28px; align-items: center; justify-content: space-between; }
footer .marks img { height: 34px; }
footer .on-light { display: var(--logo-light); }
footer .on-dark { display: var(--logo-dark); }
footer .sep { background: var(--hairline); }
footer small { color: var(--ink-muted); font-size: 12px; }

@media (max-width: 560px) {
  h2 { font-size: 1.4rem; }
  h2 .tab { width: 36px; height: 36px; font-size: 1rem; }
  .marks img { height: 36px; }
  .chart .row, .chart .axis { grid-template-columns: 40px minmax(0, 1fr) 70px; gap: 8px; }
}
@media (prefers-reduced-motion: no-preference) { .chart .bar { animation: grow .6s ease-out both; transform-origin: left; } }
@keyframes grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }

@page { size: Letter; margin: 0.7in 0.75in; }
@page :first { margin: 0; }
@media print {
  :root { --surface: #ffffff; --surface-raised: #f6f4fb; --ink: #231f20; --ink-muted: #484848; --hairline: #e8e8e8;
    --accent: #8c1d40; --cell: #ffc627; --logo-light: block; --logo-dark: none; color-scheme: light; }
  body { font-size: 10.5pt; }
  .gutter { padding-left: 0; padding-right: 0; }
  .cover { margin: 0; padding: 0.9in 0.85in; height: 11in; box-sizing: border-box;
    -webkit-print-color-adjust: exact; print-color-adjust: exact; break-after: page; }
  .cover .col { gap: 1.4in; }
  .band { display: none; }
  main { padding-block: 0; }
  h2 { break-after: avoid; margin-top: 1.6em; }
  h2 .tab, .fig, .contents, .abstract, th, .cell.full, .chart .bar { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  h3 { break-after: avoid; }
  .tablebox { overflow: visible; }
  table { min-width: 0; }
  tr, .fig { break-inside: avoid; }
  .chart .bar { animation: none; }
  a { color: inherit; text-decoration: none; }
}
"""


def build_body(md_text: str) -> tuple[str, str]:
    head_md, body_md = md_text.split("<!-- body -->", 1)
    md = markdown.Markdown(extensions=["tables", "toc"], extension_configs={"toc": {"permalink": False}})
    html = md.convert(body_md)
    html = section_tabs(html)
    html = html.replace("<p><!-- figure:game --></p>", GAME_FIGURE).replace("<!-- figure:game -->", GAME_FIGURE)
    html = html.replace("<p><!-- figure:round8 --></p>", round8_figure()).replace("<!-- figure:round8 -->", round8_figure())
    html = re.sub(r"<table>", '<div class="tablebox"><table>', html)
    html = html.replace("</table>", "</table></div>")
    # Wrap the abstract in a raised panel.
    html = re.sub(r'(<h2 id="abstract">Abstract</h2>)(.*?)(?=<h2)', r'\1<div class="abstract">\2</div>', html, count=1, flags=re.S)
    toc = contents(html)
    html = html.replace('<h2 id="abstract">', toc + '<h2 id="abstract">', 1)
    return head_md, html


def page(body_html: str) -> str:
    asu_w, lab_d = data_uri("asu-university-white.svg"), data_uri("endless-lab-logo-darkbg.svg")
    asu_m, lab_l = data_uri("asu-university-maroon.svg"), data_uri("endless-lab-logo-lightbg.svg")
    return f"""<title>Promises Under Pressure</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>{CSS}</style>
<header class="cover gutter">
  <div class="col">
    <div class="marks">
      <img src="{asu_w}" alt="Arizona State University">
      <span class="sep" aria-hidden="true"></span>
      <img src="{lab_d}" alt="Endless Games and Learning Lab">
    </div>
    <div style="display:grid;gap:18px">
      <div class="kind">White paper · November 2026</div>
      <h1>Promises Under Pressure</h1>
      <p class="sub">Do AI agents keep cooperative agreements, report honestly, and get caught when they don't? Findings from eight preregistered rounds with Claude models.</p>
    </div>
    <div class="byline">
      <span class="name">Dr. Mark Ollila</span>
      <span class="aff">Endless Games and Learning Lab, Arizona State University</span>
      <span class="date">November 21, 2026</span>
    </div>
  </div>
</header>
<div class="band" aria-hidden="true"></div>
<main class="gutter"><div class="col">
{body_html}
</div></main>
<footer class="gutter"><div class="col">
  <div class="marks">
    <img class="on-light" src="{asu_m}" alt="Arizona State University">
    <img class="on-dark" src="{asu_w}" alt="Arizona State University">
    <span class="sep" aria-hidden="true"></span>
    <img class="on-light" src="{lab_l}" alt="Endless Games and Learning Lab">
    <img class="on-dark" src="{lab_d}" alt="Endless Games and Learning Lab">
  </div>
  <small>Dr. Mark Ollila · Endless Games and Learning Lab · November 2026</small>
</div></footer>
"""


def standalone(inner: str) -> str:
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + inner.split("<style>", 1)[0].replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
            + "<style>" + inner.split("<style>", 1)[1].split("</style>", 1)[0] + "</style>\n</head>\n<body>"
            + inner.split("</style>", 1)[1] + "</body>\n</html>\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--artifact", type=Path)
    args = ap.parse_args(argv)
    _, body = build_body((HERE / "WHITEPAPER.md").read_text(encoding="utf-8"))
    inner = page(body)
    if "—" in inner:
        print("The Lab's style has no em dashes; remove them from WHITEPAPER.md.", file=sys.stderr)
        return 1
    out = HERE / "whitepaper.html"
    out.write_text(standalone(inner), encoding="utf-8")
    print("wrote", out.relative_to(HERE.parent.parent))
    if args.artifact:
        args.artifact.write_text(inner, encoding="utf-8")
        print("wrote", args.artifact)
    if args.pdf:
        pdf = HERE / "whitepaper.pdf"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", out.as_uri()], check=True, capture_output=True)
        print("wrote", pdf.relative_to(HERE.parent.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
