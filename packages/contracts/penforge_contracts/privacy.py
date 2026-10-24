from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator

from .schemas import Contract


def version_one(value):
    if type(value) is not int or value != 1:
        raise ValueError("Unsupported privacy payload version")
    return value


class PrivacyMaintenanceJob(Contract):
    schema_version: Annotated[Literal[1], BeforeValidator(version_one)] = 1
    deletion_request_id: UUID
