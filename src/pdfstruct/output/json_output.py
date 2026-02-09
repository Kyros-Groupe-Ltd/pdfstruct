"""JSON output formatter."""

from __future__ import annotations

import json
from typing import Any

from pdfstruct.models.document import Document


def to_json(doc: Document, indent: int = 2) -> str:
    """Serialize a Document to a JSON string."""
    return json.dumps(doc.to_dict(), indent=indent, ensure_ascii=False, default=str)


def to_dict(doc: Document) -> dict[str, Any]:
    """Serialize a Document to a dictionary."""
    return doc.to_dict()
