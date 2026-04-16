"""
Pytest tests for Pydantic WhatsApp webhook models.
Run: pytest test_webhook_models.py -v
"""

from __future__ import annotations

import pytest

from modules.webhook_models import WhatsAppWebhookPayload


VALID_TEXT_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "123456789",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550001",
                            "phone_number_id": "988748124325833",
                        },
                        "contacts": [
                            {"profile": {"name": "Test User"}, "wa_id": "919999999999"}
                        ],
                        "messages": [
                            {
                                "from": "919999999999",
                                "id": "wamid.HBgM...",
                                "timestamp": "1728000000",
                                "type": "text",
                                "text": {"body": "Hi, I need a logo"},
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

STATUS_ONLY_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "123",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "statuses": [
                            {
                                "id": "wamid.x",
                                "status": "delivered",
                                "timestamp": "1728000001",
                                "recipient_id": "919999999999",
                            }
                        ],
                    },
                }
            ],
        }
    ],
}

IMAGE_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "1",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "messages": [
                            {
                                "from": "919999999999",
                                "id": "wamid.img",
                                "timestamp": "1728000002",
                                "type": "image",
                                "image": {
                                    "id": "media-1",
                                    "mime_type": "image/jpeg",
                                    "sha256": "deadbeef",
                                    "caption": "my existing logo",
                                },
                            }
                        ],
                    },
                }
            ],
        }
    ],
}


def test_valid_text_payload_parses():
    payload = WhatsAppWebhookPayload.model_validate(VALID_TEXT_PAYLOAD)
    assert payload.object == "whatsapp_business_account"
    msgs = payload.messages()
    assert len(msgs) == 1
    assert msgs[0].from_ == "919999999999"
    assert msgs[0].type == "text"
    assert msgs[0].text is not None
    assert msgs[0].text.body == "Hi, I need a logo"


def test_status_only_payload_has_zero_messages():
    payload = WhatsAppWebhookPayload.model_validate(STATUS_ONLY_PAYLOAD)
    assert len(payload.messages()) == 0
    value = payload.first_value()
    assert value is not None
    assert len(value.statuses) == 1
    assert value.statuses[0].status == "delivered"


def test_image_payload_captures_media():
    payload = WhatsAppWebhookPayload.model_validate(IMAGE_PAYLOAD)
    msgs = payload.messages()
    assert len(msgs) == 1
    assert msgs[0].type == "image"
    assert msgs[0].image is not None
    assert msgs[0].image.id == "media-1"
    assert msgs[0].image.caption == "my existing logo"


def test_unknown_top_level_field_allowed_extra_allow():
    # Meta occasionally introduces new fields; extra=allow means no rejection.
    payload_with_extra = {**VALID_TEXT_PAYLOAD, "future_field": {"hello": "world"}}
    payload = WhatsAppWebhookPayload.model_validate(payload_with_extra)
    assert payload.object == "whatsapp_business_account"


def test_empty_entry_list_parses_safely():
    payload = WhatsAppWebhookPayload.model_validate({"object": "whatsapp_business_account"})
    assert payload.entry == []
    assert payload.messages() == []
    assert payload.first_value() is None


def test_invalid_payload_raises():
    # `entry` must be a list — a dict here should fail validation.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload.model_validate({"object": "x", "entry": "not a list"})


def test_malformed_message_missing_required_field_raises():
    # latitude/longitude are required on WhatsAppLocation; missing both should fail.
    from pydantic import ValidationError

    bad = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "from": "919999999999",
                                    "id": "wamid.loc",
                                    "type": "location",
                                    "location": {"name": "incomplete"},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationError):
        WhatsAppWebhookPayload.model_validate(bad)
