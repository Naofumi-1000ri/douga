"""Focused tests for AI v1 self-describing metadata around text batch ops."""

import pytest
from fastapi import Response

from src.api.ai_v1 import get_capabilities, get_schemas


@pytest.mark.asyncio
async def test_capabilities_batch_metadata_includes_update_text_and_split():
    """Capabilities should advertise the new batch operation names and examples."""
    response = Response()
    result = await get_capabilities(current_user=object(), response=response, include="all")

    schema_notes = result.data["schema_notes"]
    batch_types = schema_notes["batch_operation_types"]
    batch_examples = result.data["request_formats"]["endpoints"]["POST /batch"]["body"][
        "operations"
    ]

    update_text_example = next(op for op in batch_examples if op["operation"] == "update_text")
    split_example = next(op for op in batch_examples if op["operation"] == "split")

    assert "update_text" in batch_types
    assert "split" in batch_types
    assert "'update_text'" in schema_notes["batch_operation_names"]
    assert "'split'" in schema_notes["batch_operation_names"]
    assert update_text_example["text"]["text_content"] == "Updated telop"
    assert split_example["data"]["split_at_ms"] == 5000
    assert split_example["data"]["left_text_content"] == "前半テキスト"
    assert split_example["data"]["right_text_content"] == "後半テキスト"


@pytest.mark.asyncio
async def test_schemas_batch_example_includes_update_text_and_split():
    """Schemas endpoint should show example batch payloads for the new operations."""
    response = Response()
    result = await get_schemas(current_user=object(), response=response, detail="full")

    batch_schema = result.data["schemas"]["BatchClipOperation"]
    operations = batch_schema["example_body"]["operations"]

    update_text_example = next(op for op in operations if op["operation"] == "update_text")
    split_example = next(op for op in operations if op["operation"] == "split")

    assert update_text_example["text"]["text_content"] == "Updated telop"
    assert split_example["data"]["split_at_ms"] == 5000
    assert split_example["data"]["left_text_content"] == "前半テキスト"
    assert split_example["data"]["right_text_content"] == "後半テキスト"
