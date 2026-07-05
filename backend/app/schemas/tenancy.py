import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: dt.datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    created_at: dt.datetime
