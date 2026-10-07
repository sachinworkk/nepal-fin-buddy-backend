from app.schemas.common import ORM


class CategoryOut(ORM):
    slug: str
    label_en: str
    label_ne: str


class PaymentMethodOut(ORM):
    code: str
    label_en: str
    label_ne: str
