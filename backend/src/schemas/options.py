from pydantic import BaseModel


class OperationOptions(BaseModel):
    validate_only: bool = False
    include_diff: bool = False
