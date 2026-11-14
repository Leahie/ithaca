"""Unit abbreviations in the guide's replies are written out for the voice."""

from __future__ import annotations

import pytest

from services.api.routers.speech_text import for_speech


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("The living room is 24 ft wide.", "The living room is 24 feet wide."),
        ("It's 1 ft from the wall.", "It's 1 foot from the wall."),
        ("The ceiling is 2.7 m high.", "The ceiling is 2.7 meters high."),
        ("Leave 1 m in front of the sofa.", "Leave 1 meter in front of the sofa."),
        ("About 3m of wall.", "About 3 meters of wall."),
        ("The room is 12 x 14 ft.", "The room is 12 by 14 feet."),
        ("The room is 12 ft x 14 ft.", "The room is 12 feet by 14 feet."),
        ("It's 4.2 m × 3.6 m.", "It's 4.2 meters by 3.6 meters."),
        ("It has 1,850 sq ft and 172 m² of space.", "It has 1,850 square feet and 172 square meters of space."),
        ("Only 320 sq. ft. upstairs.", "Only 320 square feet upstairs."),
        ("A 90 cm desk and a 12 mm gap.", "A 90 centimeters desk and a 12 millimeters gap."),
        ("The park is 0.4 mi away, 1 km by car.", "The park is 0.4 miles away, 1 kilometer by car."),
        ("Traffic is 55 dBA here, 48 dB(A) inside.", "Traffic is 55 decibels here, 48 decibels inside."),
        ("It gets 6.5 h of sun, 1 hr in winter.", "It gets 6.5 hours of sun, 1 hour in winter."),
        ("The sun is at 42° high, 173°.", "The sun is at 42 degrees high, 173 degrees."),
        ("It's 72°F outside.", "It's 72 degrees Fahrenheit outside."),
        ("The door is 6'8\" tall.", "The door is 6 feet 8 inches tall."),
        ("A 9' ceiling.", "A 9 feet ceiling."),
    ],
)
def test_units_are_written_out(text, spoken):
    assert for_speech(text) == spoken


@pytest.mark.parametrize(
    "text",
    [
        "Sunrise is at 6:45 am, it takes 5 min.",
        "It's listed at $1.2m.",
        "Put it in the kitchen, 2x faster.",
        "The 1990's kitchen has room 3B.",
        "I'm here. Mmm, ft and m alone stay.",
    ],
)
def test_other_text_is_unchanged(text):
    assert for_speech(text) == text
