"""The guide's replies as the voice should say them: unit abbreviations written out.

Deepgram reads "12 ft" as "12 foot" and "3 m" as "3 m". Only an abbreviation right after a number
is changed ("12 ft" -> "12 feet", "1 m" -> "1 meter", "12 x 14 ft" -> "12 by 14 feet"), so words
such as "am", "min" or "in" are left alone, and "$2m" stays money.
"""

from __future__ import annotations

import re

# A number not glued to a word, a currency sign, or the end of another number.
_NUM = r"(?<![\w$£€.,])(?P<n>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# "ft." eats its dot only mid-sentence ("12 ft. wide"); a full stop stays.
_DOT = r"(?:\.(?=\s+[a-z]))?"

_UNITS: list[tuple[str, str, str]] = [
    (rf"sq\.?\s?ft{_DOT}|sqft|ft²|ft\^?2", "square foot", "square feet"),
    (rf"sq\.?\s?m{_DOT}|m²|m\^?2", "square meter", "square meters"),
    (r"km", "kilometer", "kilometers"),
    (r"cm", "centimeter", "centimeters"),
    (r"mm", "millimeter", "millimeters"),
    (r"m", "meter", "meters"),
    (rf"ft{_DOT}", "foot", "feet"),
    (rf"mi{_DOT}", "mile", "miles"),
    (r"mph", "mile per hour", "miles per hour"),
    (r"dB\(A\)|dBA|dB", "decibel", "decibels"),
    (rf"hrs?{_DOT}|h", "hour", "hours"),
    (r"°\s?F", "degree Fahrenheit", "degrees Fahrenheit"),
    (r"°\s?C", "degree Celsius", "degrees Celsius"),
    (r"°", "degree", "degrees"),
]
_UNIT_PATTERNS = [(re.compile(rf"{_NUM}[ \u00a0]?(?:{unit})(?![\w²^])"), one, many) for unit, one, many in _UNITS]

_FEET_INCHES = re.compile(r"(?<![\w.])(\d+)['′]\s?(\d+(?:\.\d+)?)[\"″]")
_FEET_MARK = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)['′](?=[\s,;:.)]|$)")
_BY = re.compile(r"(\d|feet|foot|meters|meter)\s*[x×]\s*(?=\d)")


def _plural(n: str, one: str, many: str) -> str:
    return one if n == "1" else many


def for_speech(text: str) -> str:
    text = _FEET_INCHES.sub(lambda m: f"{m[1]} {_plural(m[1], 'foot', 'feet')} {m[2]} {_plural(m[2], 'inch', 'inches')}", text)
    text = _FEET_MARK.sub(lambda m: f"{m[1]} {_plural(m[1], 'foot', 'feet')}", text)
    for pattern, one, many in _UNIT_PATTERNS:
        text = pattern.sub(lambda m, one=one, many=many: f"{m['n']} {_plural(m['n'], one, many)}", text)
    return _BY.sub(r"\1 by ", text)
