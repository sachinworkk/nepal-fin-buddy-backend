"""Prize eligibility shared by the REST route and the chat agent's tools."""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bill, PrizeEnrollment
from app.prize.validation import validate_fields


async def eligible_bills(session: AsyncSession, user_id: uuid.UUID) -> list[Bill]:
    """Confirmed bills that pass the portal's bill checks and aren't already enrolled,
    newest first."""
    taken = {
        (r.seller_pan_no, r.bill_number, r.bill_date)
        for r in await session.scalars(select(PrizeEnrollment).where(
            PrizeEnrollment.user_id == user_id,
            PrizeEnrollment.status.in_(("submitted", "handed_off")),
        ))
    }
    bills = await session.scalars(select(Bill).where(
        Bill.user_id == user_id, Bill.status == "confirmed",
        Bill.vendor_pan.is_not(None), Bill.bill_number.is_not(None),
        Bill.bill_date_ad.is_not(None), Bill.total_amount.is_not(None),
        or_(Bill.payment_method.is_(None), Bill.payment_method == "cash"),
    ).order_by(Bill.bill_date_ad.desc()))
    return [
        b for b in bills
        if (b.vendor_pan, b.bill_number, b.bill_date_ad) not in taken
        and not validate_fields(
            bill_number=b.bill_number, seller_pan_no=b.vendor_pan, bill_date=b.bill_date_ad,
            billed_total_amount=float(b.total_amount), payment_method="Cash",
            name="Placeholder", address="Placeholder", mobile_number="9800000000")
    ]
