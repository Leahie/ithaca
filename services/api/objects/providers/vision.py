"""Reads an image with a vision LLM: what kind of image it is, which furniture is in it, and any
dimensions, price and title printed on it (PLAN.md section 4, steps 1 and 5)."""

from __future__ import annotations

import base64
import io

from langchain_core.messages import HumanMessage
from PIL import Image, ImageDraw

from services.api.agent.models import get_model
from services.api.objects.classes import FURNITURE_CLASSES
from services.api.objects.providers.base import ImageReading, Piece, RoomPhotoReading, RoomReading

MAX_ROOM_THINGS = 14

PROMPT = f"""\
This image was uploaded by someone furnishing a home. It is a product page screenshot, a product
photo, or a photo of a room. List the furniture worth adding to their inventory.

- item_class must be one of: {", ".join(FURNITURE_CLASSES)}.
- Dimensions: fill width_m, depth_m and height_m only from numbers printed in the image, converted
  to meters (1 in = 0.0254 m). Width is the long side you face; depth is front to back. If the
  image gives no dimensions, leave them empty. Never estimate.
- In a room photo, list at most the 4 most prominent pieces. Skip built-ins, lamps on tables,
  decor and anything mostly hidden.
- segment_prompt: two or three plain words that single the piece out in this image.
- price and product_title only if printed.
"""

ROOM_PROMPT = f"""\
These frames, numbered from 0, come from one phone video of a room. The person is moving and wants
3D models of what they will take with them. List each distinct piece once: furniture, and other
large freestanding things such as an air purifier or a floor fan.

- frame: the frame where the most of the piece is in view, and sharp. The 3D model is built from
  that frame alone. Something standing in front of the piece is fine (hidden parts get filled in),
  so prefer a frame that shows its full width and height over one that shows a clean corner of
  it. Skip a piece when every frame shows less than about half of it, or only blurred.
- box: a rough box around the piece in that frame.
- segment_prompt: two or three plain words that single the piece out in that frame.
- item_class must be one of: {", ".join(FURNITURE_CLASSES)}.
- Skip what stays with the house (toilets, sinks, doors, curtains, built-in shelves), screens and
  computers, and small things (papers, bottles, bags, decor).
- Leave the dimensions, price and product_title empty.
- At most 8 pieces, most prominent first.
"""


OWN_ROOM_PROMPT = f"""\
This is a photo of someone's own room. They are moving, and want to see this room's things in the
home they may buy. List what is theirs to take: every piece of furniture, and the belongings and
decorations that make the room theirs (lamps, plants, rugs, mirrors, pictures on the wall, and
things standing on the furniture such as a vase, a clock, a speaker or a stack of books).

- Furniture first, largest first, then the smaller things. At most {MAX_ROOM_THINGS} in all. Each
  thing once. Number them from 0 in the order you list them.
- Skip what stays with the house (doors, windows, curtains and blinds, ceiling lights and fans,
  built-in cupboards, radiators, sockets), bedding and pillows (they come with the bed), and loose
  clutter (clothes, cables, cups, papers, chargers). Skip anything mostly cut off by the edge of
  the photo or mostly hidden.
- item_class must be one of: {", ".join(FURNITURE_CLASSES)}. Use table_lamp for a lamp that stands
  on furniture, wall_art for a picture or poster, decor for other small things, other for anything
  larger that fits no class.
- box: a rough box around the whole thing.
- segment_prompt: two or three plain words that single it out in this photo: its colour or
  material and what it is, e.g. "white desk", "black bunk bed". Name the whole thing, never its
  parts or how many it has ("white dresser", not "five-drawer dresser"): the words are matched
  against the picture, and "drawer" finds one drawer.
- rests_on: for a thing standing or lying on a piece of furniture, that piece's number.
- on_wall: true for a thing fixed to a wall.
- beside: for a piece on the floor that goes with another, that piece's number.
"""


TAP_PROMPT = f"""\
Someone furnishing a home photographed something they own and tapped it: the red ring marks the
spot. Describe the one thing they tapped, so a 3D model can be made of it.

- If the ring is on part of a piece (a drawer, a leg, a cushion), it is the whole piece. If it is
  on something standing on a piece (a lamp on a desk), it is that smaller thing.
- item_class must be one of: {", ".join(FURNITURE_CLASSES)}. Use "other" for anything else.
- segment_prompt: two or three plain words that single it out in this photo, e.g. "white desk".
- box: a rough box around the whole thing, not just the ring.
- Dimensions only from numbers printed in the image, converted to meters. Never estimate.
"""


def _mark(image: bytes, at: tuple[float, float]) -> bytes:
    """The photo as a JPEG with a red ring around the tapped spot."""
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    x, y = at[0] * picture.width, at[1] * picture.height
    r = max(12, min(picture.size) // 30)
    draw = ImageDraw.Draw(picture)
    draw.ellipse((x - r, y - r, x + r, y + r), outline=(255, 0, 0), width=max(3, r // 4))
    out = io.BytesIO()
    picture.save(out, "JPEG", quality=90)
    return out.getvalue()


class LlmDescriber:
    def read_at(self, image: bytes, mime_type: str, at: tuple[float, float]) -> Piece:
        model = get_model("vision").with_structured_output(Piece, method="json_schema")
        message = HumanMessage(
            content=[
                {"type": "text", "text": TAP_PROMPT},
                {"type": "image", "base64": base64.b64encode(_mark(image, at)).decode(), "mime_type": "image/jpeg"},
            ]
        )
        return model.invoke([message])

    def read(self, image: bytes, mime_type: str, filename: str) -> ImageReading:
        model = get_model("vision").with_structured_output(ImageReading, method="json_schema")
        message = HumanMessage(
            content=[
                {"type": "text", "text": PROMPT},
                {"type": "image", "base64": base64.b64encode(image).decode(), "mime_type": mime_type},
            ]
        )
        return model.invoke([message])

    def read_room(self, image: bytes, mime_type: str) -> RoomPhotoReading:
        model = get_model("vision").with_structured_output(RoomPhotoReading, method="json_schema")
        message = HumanMessage(
            content=[
                {"type": "text", "text": OWN_ROOM_PROMPT},
                {"type": "image", "base64": base64.b64encode(image).decode(), "mime_type": mime_type},
            ]
        )
        reading = model.invoke([message])
        reading.things = reading.things[:MAX_ROOM_THINGS]
        return reading

    def read_frames(self, frames: list[bytes]) -> RoomReading:
        model = get_model("vision").with_structured_output(RoomReading, method="json_schema")
        content: list[dict] = [{"type": "text", "text": ROOM_PROMPT}]
        for n, frame in enumerate(frames):
            content.append({"type": "text", "text": f"Frame {n}:"})
            content.append({"type": "image", "base64": base64.b64encode(frame).decode(), "mime_type": "image/jpeg"})
        reading = model.invoke([HumanMessage(content=content)])
        reading.pieces = [piece for piece in reading.pieces if 0 <= piece.frame < len(frames)]
        return reading
