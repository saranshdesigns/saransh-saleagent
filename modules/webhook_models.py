"""
Pydantic v2 models for Meta WhatsApp Cloud API webhook payloads.
`extra="allow"` — Meta adds fields over time; tolerate forward-compat.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_Config = ConfigDict(extra="allow", populate_by_name=True)


class WhatsAppText(BaseModel):
    model_config = _Config
    body: str = ""


class WhatsAppMedia(BaseModel):
    model_config = _Config
    id: str
    mime_type: str | None = None
    sha256: str | None = None
    caption: str | None = None
    filename: str | None = None
    voice: bool | None = None


class WhatsAppLocation(BaseModel):
    model_config = _Config
    latitude: float
    longitude: float
    name: str | None = None
    address: str | None = None


class WhatsAppButtonReply(BaseModel):
    model_config = _Config
    id: str
    title: str


class WhatsAppListReply(BaseModel):
    model_config = _Config
    id: str
    title: str
    description: str | None = None


class WhatsAppInteractive(BaseModel):
    model_config = _Config
    type: str
    button_reply: WhatsAppButtonReply | None = None
    list_reply: WhatsAppListReply | None = None


class WhatsAppContext(BaseModel):
    model_config = _Config
    from_: str | None = Field(default=None, alias="from")
    id: str | None = None


class WhatsAppMessage(BaseModel):
    model_config = _Config

    id: str = ""
    from_: str = Field(default="", alias="from")
    timestamp: str | None = None
    type: str = "unknown"

    text: WhatsAppText | None = None
    image: WhatsAppMedia | None = None
    audio: WhatsAppMedia | None = None
    video: WhatsAppMedia | None = None
    document: WhatsAppMedia | None = None
    sticker: WhatsAppMedia | None = None
    voice: WhatsAppMedia | None = None
    location: WhatsAppLocation | None = None
    interactive: WhatsAppInteractive | None = None
    button: dict[str, Any] | None = None
    context: WhatsAppContext | None = None


class WhatsAppContactProfile(BaseModel):
    model_config = _Config
    name: str | None = None


class WhatsAppContact(BaseModel):
    model_config = _Config
    profile: WhatsAppContactProfile | None = None
    wa_id: str | None = None


class WhatsAppStatus(BaseModel):
    model_config = _Config
    id: str
    status: Literal["sent", "delivered", "read", "failed"] | str
    timestamp: str | None = None
    recipient_id: str | None = None


class WhatsAppMetadata(BaseModel):
    model_config = _Config
    display_phone_number: str | None = None
    phone_number_id: str | None = None


class WhatsAppValue(BaseModel):
    model_config = _Config
    messaging_product: str | None = None
    metadata: WhatsAppMetadata | None = None
    contacts: list[WhatsAppContact] = Field(default_factory=list)
    messages: list[WhatsAppMessage] = Field(default_factory=list)
    statuses: list[WhatsAppStatus] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class WhatsAppChange(BaseModel):
    model_config = _Config
    field: str | None = None
    value: WhatsAppValue = Field(default_factory=WhatsAppValue)


class WhatsAppEntry(BaseModel):
    model_config = _Config
    id: str | None = None
    changes: list[WhatsAppChange] = Field(default_factory=list)


class WhatsAppWebhookPayload(BaseModel):
    model_config = _Config
    object: str | None = None
    entry: list[WhatsAppEntry] = Field(default_factory=list)

    def first_value(self) -> WhatsAppValue | None:
        if not self.entry:
            return None
        entry = self.entry[0]
        if not entry.changes:
            return None
        return entry.changes[0].value

    def messages(self) -> list[WhatsAppMessage]:
        v = self.first_value()
        return v.messages if v else []
