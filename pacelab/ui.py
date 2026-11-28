"""HTML building blocks for the app: timing tables, tyre icons, team badges.

Streamlit's own table widget cannot colour single cells or show icons, so the
timing screens are rendered as small HTML tables styled by `CSS` below.
Every value that comes from the API goes through `html.escape`.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable

import pandas as pd

from pacelab.teams import team_code

COMPOUNDS = {
    "SOFT": ("S", "#E8002D"),
    "MEDIUM": ("M", "#FFD12E"),
    "HARD": ("H", "#F0F0F0"),
    "INTERMEDIATE": ("I", "#43B02A"),
    "WET": ("W", "#0067AD"),
}

CSS = """
<style>
:root {
  --pl-bg-card: #15171e; --pl-border: #272a33; --pl-row: #1d2029;
  --pl-muted: #9aa0ab; --pl-text: #f2f3f5;
  --pl-purple: #a24bf0; --pl-green: #19c26b; --pl-yellow: #ffd12e;
}
.pl-table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; font-size: 13px; color: var(--pl-text); }
.pl-table th { text-align: left; color: var(--pl-muted); font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: .06em; padding: 6px 5px; border-bottom: 1px solid var(--pl-border); white-space: nowrap; }
.pl-table td { padding: 5px 5px; border-bottom: 1px solid var(--pl-row); white-space: nowrap; vertical-align: middle; }
.pl-table tr:hover td { background: var(--pl-row); }
.pl-table td.num, .pl-table th.num { text-align: right; }
.pl-table td.pos { font-weight: 700; text-align: right; width: 28px; color: var(--pl-muted); }
.pl-table tr.out td { opacity: .45; }
.pl-wrap { overflow-x: auto; }
.pl-driver { display: inline-flex; align-items: center; gap: 8px; }
.pl-bar { width: 4px; height: 20px; border-radius: 2px; flex: none; }
.pl-no { color: var(--pl-muted); font-size: 12px; min-width: 22px; text-align: right; }
.pl-acr { font-weight: 700; letter-spacing: .02em; }
.pl-team { display: inline-flex; align-items: center; justify-content: center; min-width: 34px; height: 18px; padding: 0 5px; border-radius: 4px; font-size: 10px; font-weight: 800; letter-spacing: .05em; color: #0b0c10; }
.pl-tyre { display: inline-flex; align-items: center; gap: 5px; }
.pl-tyre svg { flex: none; }
.pl-tyre .age { color: var(--pl-muted); font-size: 12px; }
.pl-stops { color: var(--pl-muted); font-size: 11px; border: 1px solid var(--pl-border); border-radius: 3px; padding: 0 3px; margin-left: 2px; }
.pl-sec { display: inline-block; min-width: 46px; text-align: right; padding: 1px 6px; border-radius: 4px; font-weight: 600; }
.pl-sec.purple { background: var(--pl-purple); color: #fff; }
.pl-sec.green { background: var(--pl-green); color: #04140b; }
.pl-sec.yellow { color: var(--pl-yellow); }
.pl-tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 800; }
.pl-tag.pit { background: #ffffff; color: #0b0c10; }
.pl-tag.out { background: #3a3d46; color: #c9ccd3; }
.pl-good { color: var(--pl-green); font-weight: 600; }
.pl-bad { color: var(--pl-muted); }
.pl-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 4px 0 14px; }
.pl-card { background: var(--pl-bg-card); border: 1px solid var(--pl-border); border-radius: 10px; padding: 10px 12px; }
.pl-card .label { color: var(--pl-muted); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.pl-card .value { font-size: 22px; font-weight: 700; margin: 2px 0; font-variant-numeric: tabular-nums; }
.pl-card .value.purple { color: var(--pl-purple); }
.pl-card .sub { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--pl-muted); }
.pl-legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--pl-muted); margin: 4px 0 10px; }
.pl-legend span { display: inline-flex; align-items: center; gap: 6px; }
.pl-flag { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; border-radius: 10px; font-weight: 800; letter-spacing: .04em; margin-bottom: 10px; }
.pl-flag .lap { font-variant-numeric: tabular-nums; font-size: 20px; }
.pl-flag.GREEN { background: #0f3d24; color: #7ef0b0; }
.pl-flag.SC, .pl-flag.SC-ENDING, .pl-flag.VSC, .pl-flag.VSC-ENDING { background: #4a3b00; color: #ffd12e; }
.pl-flag.RED { background: #4a0b0b; color: #ff7b7b; }
.pl-flag.CHEQUERED { background: repeating-conic-gradient(#ececec 0 25%, #1a1a1a 0 50%) 0 0 / 16px 16px; color: #fff; text-shadow: 0 1px 3px #000; }
.pl-msgs { font-size: 12px; color: var(--pl-muted); line-height: 1.5; }
.pl-msgs b { color: var(--pl-text); font-weight: 600; margin-right: 6px; font-variant-numeric: tabular-nums; }
.pl-title { display: flex; align-items: center; gap: 8px; font-weight: 700; margin: 6px 0 4px; }
</style>
"""

STATUS_TEXT = {
    "GREEN": "GREEN FLAG",
    "SC": "SAFETY CAR",
    "SC ENDING": "SAFETY CAR IN THIS LAP",
    "VSC": "VIRTUAL SAFETY CAR",
    "VSC ENDING": "VSC ENDING",
    "RED": "RED FLAG",
    "CHEQUERED": "CHEQUERED FLAG",
}


def laptime(seconds: Any) -> str:
    if seconds is None or pd.isna(seconds):
        return ""
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f}"


def gap_text(value: Any, leader: str = "LEADER") -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return escape(value)
    if float(value) == 0:
        return leader
    return f"+{float(value):.3f}"


def tyre_icon(compound: Any, age: Any = None, size: int = 22) -> str:
    letter, colour = COMPOUNDS.get(str(compound).upper(), ("?", "#888888"))
    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" role="img" aria-label="{escape(str(compound).title())} tyre">'
        f'<circle cx="12" cy="12" r="10" fill="#121318" stroke="{colour}" stroke-width="3.5"/>'
        f'<circle cx="12" cy="12" r="10" fill="none" stroke="#000" stroke-opacity=".35" stroke-width="1" stroke-dasharray="2 4"/>'
        f'<text x="12" y="16.2" text-anchor="middle" font-size="11" font-weight="800" font-family="system-ui, sans-serif" fill="{colour}">{letter}</text>'
        "</svg>"
    )
    age_html = ""
    if age is not None and not pd.isna(age):
        age_html = f'<span class="age">{int(age)}L</span>'
    return f'<span class="pl-tyre">{svg}{age_html}</span>'


def team_badge(team_name: Any, colour: str) -> str:
    return (
        f'<span class="pl-team" style="background:{escape(colour)}" title="{escape(str(team_name))}">'
        f"{escape(team_code(team_name if isinstance(team_name, str) else None))}</span>"
    )


def driver_cell(number: Any, acronym: Any, team_name: Any, colour: str, show_team: bool = True) -> str:
    team = team_badge(team_name, colour) if show_team else ""
    return (
        f'<span class="pl-driver"><span class="pl-bar" style="background:{escape(colour)}"></span>'
        f'<span class="pl-no">{int(number)}</span><span class="pl-acr">{escape(str(acronym))}</span>{team}</span>'
    )


def sector_cell(seconds: Any, status: str) -> str:
    if seconds is None or pd.isna(seconds):
        return ""
    return f'<span class="pl-sec {escape(status)}">{float(seconds):.3f}</span>'


def table(headers: Iterable[tuple[str, str]], rows: Iterable[tuple[list[str], str]]) -> str:
    """headers: (label, css class); rows: (cells, row css class). Cells are trusted HTML."""
    headers = list(headers)
    head = "".join(f'<th class="{cls}">{escape(label)}</th>' for label, cls in headers)
    body = []
    for cells, row_cls in rows:
        tds = "".join(f'<td class="{cls}">{cell}</td>' for cell, (_, cls) in zip(cells, headers))
        body.append(f'<tr class="{row_cls}">{tds}</tr>')
    return f'<div class="pl-wrap"><table class="pl-table"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def cards(items: Iterable[dict[str, str]]) -> str:
    """items: label, value, optional sub (HTML) and cls (e.g. 'purple')."""
    html = []
    for item in items:
        html.append(
            f'<div class="pl-card"><div class="label">{escape(item["label"])}</div>'
            f'<div class="value {item.get("cls", "")}">{escape(item["value"])}</div>'
            f'<div class="sub">{item.get("sub", "")}</div></div>'
        )
    return f'<div class="pl-cards">{"".join(html)}</div>'


def sector_legend() -> str:
    return (
        '<div class="pl-legend">'
        f'<span>{sector_cell(0, "purple").replace("0.000", "fastest")} fastest of anyone</span>'
        f'<span>{sector_cell(0, "green").replace("0.000", "personal")} driver\'s own best</span>'
        f'<span>{sector_cell(0, "yellow").replace("0.000", "slower")} slower than own best</span>'
        f'<span>{tyre_icon("SOFT")} {tyre_icon("MEDIUM")} {tyre_icon("HARD")} tyre, with laps used</span>'
        "</div>"
    )


def flag_banner(status: str, lap: int, total: int | None) -> str:
    lap_text = f"LAP {lap}/{total}" if total else f"LAP {lap}"
    cls = status.replace(" ", "-")
    return (
        f'<div class="pl-flag {escape(cls)}"><span>{escape(STATUS_TEXT.get(status, status))}</span>'
        f'<span class="lap">{escape(lap_text)}</span></div>'
    )


def messages_html(messages: pd.DataFrame) -> str:
    if messages is None or messages.empty:
        return '<div class="pl-msgs">No messages yet.</div>'
    lines = [
        f'<div><b>{row.date:%H:%M:%S}</b>{escape(str(row.message))}</div>' for row in messages.itertuples()
    ]
    return f'<div class="pl-msgs">{"".join(lines)}</div>'
