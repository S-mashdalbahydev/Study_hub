"""Public identifiers are 32 lowercase hex characters.

Postgres stores UUIDs and prints them hyphenated. The API, the web client and the JSON
already saved in notebooks all use the unhyphenated form, so every value leaving the API
goes through `public()`, and every id arriving is accepted in either spelling.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException


def parse_id(value, what='Item'):
    try:
        return UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, f'{what} not found') from None


def hex_id(value):
    return value.hex if isinstance(value, UUID) else value


def public(value):
    if isinstance(value, UUID):
        return value.hex
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes | memoryview):
        return None
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [public(v) for v in value]
    return value
