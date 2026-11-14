"""Matterport Model API (GraphQL) client.

Auth is HTTP basic: MATTERPORT_API_KEY is the user name, MATTERPORT_API_TOKEN the password. The
hackathon keys work on production (api.matterport.com) only. Fields that are null are left out of
responses, so read everything with .get().

Model frame: right-handed, Z up, meters. Panos, wall vertices and openings all share it.
"""

from __future__ import annotations

from typing import Any

import requests

from services.api.integrations.cache import RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env

GRAPH_URL = "https://api.matterport.com/api/models/graph"


class MatterportError(RuntimeError):
    pass


# The geoLocationOf probes let ingestion check its reading of the georeference against
# Matterport's own model -> lat/lon conversion.
TWIN_INPUTS_QUERY = """
query TwinInputs($id: ID!) { model(id: $id) {
  id name
  address { address locality administrativeArea postalCode }
  geolocation { lat long }
  geocoordinates {
    latitude longitude altitude source
    rotation { w x y z }
    translation { x y z }
    checkOrigin: geoLocationOf(modelLocation: {x: 0, y: 0, z: 0}) { lat long status }
    checkX: geoLocationOf(modelLocation: {x: 10, y: 0, z: 0}) { lat long status }
    checkY: geoLocationOf(modelLocation: {x: 0, y: 10, z: 0}) { lat long status }
  }
  floors {
    id label sequence
    vertices { id position { x y } }
    edges { id type thickness vertices { id }
            openings { id type label width height lowerElevation relativeCenter } }
  }
  rooms {
    id label keywords
    floor { id }
    classifications { label confidence }
    boundary { edges { id } }
    ceiling(units: metric) { minHeight maxHeight }
    dimensions(units: metric) { areaFloor width depth height }
    dimensionEstimates(units: metric) { area height lowerElevation }
  }
  panoLocations { id label placement position { x y z } anchor { floor { id } room { id } } }
} }
"""


class MatterportClient:
    def __init__(
        self,
        api_key: str | None = None,
        api_token: str | None = None,
        cache: RecordReplayCache | None = None,
        timeout_s: float = 120.0,
    ):
        self.api_key = env("MATTERPORT_API_KEY") if api_key is None else api_key
        self.api_token = env("MATTERPORT_API_TOKEN") if api_token is None else api_token
        self.cache = cache or RecordReplayCache()
        self.timeout_s = timeout_s

    def graph(self, query: str, variables: dict[str, Any] | None = None, *, name: str) -> dict:
        request = {"query": " ".join(query.split()), "variables": variables or {}}
        body = self.cache.call("matterport", name, request, lambda: self._post(request))
        _raise_on_errors(body)
        return body["data"]

    def twin_inputs(self, model_id: str) -> dict:
        data = self.graph(TWIN_INPUTS_QUERY, {"id": model_id}, name=f"twin_inputs_{model_id}")
        model = data.get("model")
        if not model:
            raise MatterportError(f"model {model_id} not found or not visible to this key")
        return model

    def _post(self, request: dict) -> dict:
        if not (self.api_key and self.api_token):
            raise IntegrationNotConfigured("Matterport", "MATTERPORT_API_KEY and MATTERPORT_API_TOKEN")
        response = requests.post(
            GRAPH_URL, auth=(self.api_key, self.api_token), json=request, timeout=self.timeout_s
        )
        if response.status_code in (401, 403):
            raise MatterportError(
                f"Matterport rejected the key (HTTP {response.status_code}): {response.text[:200]}"
            )
        response.raise_for_status()
        body = response.json()
        _raise_on_errors(body)  # before the cache records it
        return body


def _raise_on_errors(body: dict) -> None:
    if body.get("errors"):
        messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
        raise MatterportError(f"GraphQL errors: {messages}")
