import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORM


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=200)
    preferred_language: Literal["en", "ne"] = "en"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str | None
    preferred_language: str


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    preferred_language: Literal["en", "ne"] | None = None
