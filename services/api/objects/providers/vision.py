"""Reads an image with a vision LLM: what kind of image it is, which furniture is in it, and any
dimensions, price and title printed on it (PLAN.md section 4, steps 1 and 5)."""

from __future__ import annotations

import base64

from langchain_core.messages import HumanMessage

from services.api.agent.models import get_model
from services.api.objects.classes import FURNITURE_CLASSES
from services.api.objects.providers.base import ImageReading, RoomReading

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


class LlmDescriber:
    def read(self, image: bytes, mime_type: str, filename: str) -> ImageReading:
        model = get_model("vision").with_structured_output(ImageReading, method="json_schema")
        message = HumanMessage(
            content=[
                {"type": "text", "text": PROMPT},
                {"type": "image", "base64": base64.b64encode(image).decode(), "mime_type": mime_type},
            ]
        )
        return model.invoke([message])

    def read_frames(self, frames: list[bytes]) -> RoomReading:
        model = get_model("vision").with_structured_output(RoomReading, method="json_schema")
        content: list[dict] = [{"type": "text", "text": ROOM_PROMPT}]
        for n, frame in enumerate(frames):
            content.append({"type": "text", "text": f"Frame {n}:"})
            content.append({"type": "image", "base64": base64.b64encode(frame).decode(), "mime_type": "image/jpeg"})
        reading = model.invoke([HumanMessage(content=content)])
        reading.pieces = [piece for piece in reading.pieces if 0 <= piece.frame < len(frames)]
        return reading
