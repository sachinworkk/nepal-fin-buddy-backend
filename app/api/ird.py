from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.core.crypto import decrypt, encrypt
from app.models import IrdCredential
from app.schemas.ird import IrdCredentialsIn, IrdCredentialsOut

router = APIRouter(prefix="/settings/ird-credentials", tags=["settings"])


def _out(c: IrdCredential) -> IrdCredentialsOut:
    return IrdCredentialsOut(
        username=decrypt(c.username_enc), seller_pan=c.seller_pan,
        has_password=bool(c.password_enc), updated_at=c.updated_at,
    )


async def _get(session, user) -> IrdCredential | None:
    return await session.scalar(select(IrdCredential).where(IrdCredential.user_id == user.id))


@router.put("", response_model=IrdCredentialsOut)
async def put_credentials(body: IrdCredentialsIn, user: CurrentUser, session: SessionDep):
    c = await _get(session, user) or IrdCredential(user_id=user.id)
    c.username_enc, c.password_enc = encrypt(body.username), encrypt(body.password)
    c.seller_pan = body.seller_pan
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return _out(c)


@router.get("", response_model=IrdCredentialsOut)
async def get_credentials(user: CurrentUser, session: SessionDep):
    c = await _get(session, user)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No IRD credentials saved")
    return _out(c)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(user: CurrentUser, session: SessionDep):
    c = await _get(session, user)
    if c is not None:
        await session.delete(c)
        await session.commit()
