"""Session types and small REST payloads. Owner: B. Consumer: A."""
from __future__ import annotations

from datetime import datetime

from .common import Model


class Session(Model):
    session_id: str
    pair_token: str
    expires_at: datetime


class JoinRequest(Model):
    pair_token: str


class Ok(Model):
    ok: bool


class UploadResult(Model):
    upload_id: str
    url: str


class ObjectIngestRequest(Model):
    upload_id: str


class JobRef(Model):
    job_id: str


class LayoutFeedbackRequest(Model):
    feedback: str
