from enum import StrEnum


class PaymentMethod(StrEnum):
    cash = "cash"
    card = "card"
    qr = "qr"
    cheque = "cheque"
