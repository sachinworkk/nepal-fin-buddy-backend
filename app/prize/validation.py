"""Local mirror of the prize portal's validation ladder.

Fail fast before calling IRD: every invalid submission burns a Turnstile token and
risks the portal's burst throttle. Messages intentionally match the strings the
portal returns so the UI can treat local and remote errors identically.
"""

import datetime as dt
import re

BILL_NUMBER_RE = re.compile(r"^[A-Za-z0-9/._-]{1,40}$")
PAN_RE = re.compile(r"^\d{9}$")
MOBILE_RE = re.compile(r"^9[678]\d{8}$")

ERR_BILL_NUMBER = "bill number must be 1 to 40 letters, digits, slash, dot, dash, or underscore"
ERR_PAN = "seller PAN number must be 9 digits"
ERR_DATE = "bill date must be YYYY-MM-DD"
ERR_AMOUNT = "billed total amount must be greater than zero"
ERR_NAME = "name must be between 2 and 120 characters"
ERR_ADDRESS = "address is required"
ERR_MOBILE = "mobile number must be a 10-digit Nepali number starting with 96, 97 or 98"
ERR_PAYMENT = "only cash bills are supported for now"
ERR_FUTURE_DATE = "bill date cannot be in the future"


def validate_fields(
    *, bill_number: str, seller_pan_no: str, bill_date: dt.date, billed_total_amount: float,
    payment_method: str, name: str, address: str, mobile_number: str,
) -> dict[str, str]:
    """Return a map of field -> error message; empty means all valid."""
    errors: dict[str, str] = {}
    if not BILL_NUMBER_RE.fullmatch(bill_number):
        errors["bill_number"] = ERR_BILL_NUMBER
    if not PAN_RE.fullmatch(seller_pan_no):
        errors["seller_pan_no"] = ERR_PAN
    if bill_date > dt.date.today():
        errors["bill_date"] = ERR_FUTURE_DATE
    if not billed_total_amount or billed_total_amount <= 0:
        errors["billed_total_amount"] = ERR_AMOUNT
    if payment_method != "Cash":
        errors["payment_method"] = ERR_PAYMENT
    if not 2 <= len(name.strip()) <= 120:
        errors["name"] = ERR_NAME
    if not 1 <= len(address.strip()) <= 200:
        errors["address"] = ERR_ADDRESS
    if not MOBILE_RE.fullmatch(mobile_number):
        errors["mobile_number"] = ERR_MOBILE
    return errors