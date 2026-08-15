from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)
ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_json_model(text: str, model: type[ModelT]) -> ModelT:
    """Recover a JSON object from a local model response, then validate it."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        payload: dict[str, Any] = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError("LLM response did not contain a JSON object")
        payload = json.loads(match.group(0))
    return model.model_validate(payload)


def latest_by_step(items: list[dict[str, Any]], step_id: int) -> dict[str, Any] | None:
    return next((item for item in reversed(items) if item.get("step_id") == step_id), None)
