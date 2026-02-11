from pydantic import BaseModel, Field, model_validator


class OperationOptions(BaseModel):
    """Options for AI API operations.

    Attributes:
        validate_only: If true, validates without executing (dry-run)
        include_diff: If true, includes diff in response (alias: return_diff)
    """

    validate_only: bool = False
    include_diff: bool = Field(default=False, alias="return_diff")
    include_audio: bool = Field(default=True, description="Auto-place linked audio clip when adding a video clip")

    # Allow both field names for compatibility
    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def handle_diff_aliases(cls, data: dict) -> dict:
        """Accept both include_diff and return_diff."""
        if isinstance(data, dict):
            # If include_diff is provided, use it
            if "include_diff" in data and "return_diff" not in data:
                data["return_diff"] = data["include_diff"]
        return data
