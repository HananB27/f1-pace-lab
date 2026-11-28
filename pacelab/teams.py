"""Team short codes and colour helpers for labels and badges."""

from __future__ import annotations

TEAM_CODES = {
    "mclaren": "MCL",
    "ferrari": "FER",
    "red bull racing": "RBR",
    "red bull": "RBR",
    "mercedes": "MER",
    "aston martin": "AMR",
    "alpine": "ALP",
    "williams": "WIL",
    "racing bulls": "RB",
    "rb": "RB",
    "haas f1 team": "HAA",
    "haas": "HAA",
    "kick sauber": "SAU",
    "sauber": "SAU",
    "audi": "AUD",
    "cadillac": "CAD",
}

# Coloured circle emoji and the RGB they roughly render as. Streamlit widgets
# (select boxes, multiselects) only accept text, so the team colour is shown
# with the nearest emoji there; tables use real colour badges instead.
_EMOJI_RGB = {
    "🔴": (220, 40, 40),
    "🟠": (245, 130, 0),
    "🟡": (250, 210, 40),
    "🟢": (40, 170, 70),
    "🔵": (40, 110, 220),
    "🟣": (140, 70, 200),
    "🟤": (130, 80, 40),
    "⚫": (40, 40, 40),
    "⚪": (235, 235, 235),
}


def team_code(team_name: str | None) -> str:
    if not team_name:
        return "—"
    key = team_name.strip().lower()
    if key in TEAM_CODES:
        return TEAM_CODES[key]
    return "".join(ch for ch in team_name if ch.isalpha())[:3].upper()


def hex_to_rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    if len(value) != 6:
        return (136, 136, 136)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def colour_emoji(colour: str) -> str:
    r, g, b = hex_to_rgb(colour)
    return min(
        _EMOJI_RGB,
        key=lambda e: (r - _EMOJI_RGB[e][0]) ** 2 + (g - _EMOJI_RGB[e][1]) ** 2 + (b - _EMOJI_RGB[e][2]) ** 2,
    )


def driver_label(number: int, acronym: str, team_name: str | None, colour: str) -> str:
    """Text label for widgets, e.g. '🟠 #4 NOR · McLaren'."""
    return f"{colour_emoji(colour)} #{int(number)} {acronym} · {team_name or 'Unknown'}"
