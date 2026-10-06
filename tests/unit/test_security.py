import datetime as dt
import uuid

import jwt

from app.config import get_settings
from app.core import security


def test_password_hash_roundtrip():
    h = security.hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert security.verify_password("s3cret-pass", h)
    assert not security.verify_password("wrong", h)


def test_jwt_roundtrip():
    uid = uuid.uuid4()
    assert security.decode_access_token(security.create_access_token(uid)) == uid


def test_jwt_expired_and_garbage():
    uid = uuid.uuid4()
    expired = jwt.encode(
        {"sub": str(uid), "exp": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)},
        get_settings().jwt_secret, algorithm="HS256",
    )
    assert security.decode_access_token(expired) is None
    assert security.decode_access_token("not.a.token") is None
