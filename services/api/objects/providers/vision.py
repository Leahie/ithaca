"""Reads an image with a vision LLM: what kind of image it is, which furniture is in it, and any
dimensions, price and title printed on it (PLAN.md section 4, steps 1 and 5)."""

from __future__ import annotations

import base64

from langchain_core.messages import HumanMessage

from services.api.agent.models import get_model
from services.api.objects.classes import FURNITURE_CLASSES
from services.api.objects.providers.base import ImageReading

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
