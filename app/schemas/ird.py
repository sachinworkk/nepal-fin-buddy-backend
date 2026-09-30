import datetime as dt

from pydantic import BaseModel, Field


class IrdCredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)
    seller_pan: str = Field(min_length=1, max_length=20)


class IrdCredentialsOut(BaseModel):
    username: str
    seller_pan: str
    has_password: bool
    updated_at: dt.datetime
