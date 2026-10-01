"""CBMS response codes -> sync outcome and a user-safe EN/NE message."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    status: str  # synced | failed | retry
    en: str
    ne: str


SYNCED = Outcome("synced", "Submitted to IRD.", "IRD मा पेस भयो।")
# 101 means CBMS already has this bill; treating it as success keeps retries idempotent.
ALREADY_EXISTS = Outcome(
    "synced", "Already recorded at IRD.", "IRD मा पहिले नै दर्ता भइसकेको छ।"
)

CODES: dict[int, Outcome] = {
    200: SYNCED,
    100: Outcome(
        "failed", "IRD rejected the credentials. Check them in Settings, then re-sync.",
        "IRD ले प्रमाणपत्र अस्वीकार गर्‍यो। सेटिङमा जाँच गरी पुनः सिंक गर्नुहोस्।",
    ),
    101: ALREADY_EXISTS,
    102: Outcome(
        "failed", "IRD could not save the invoice. Check the PANs and amounts, then re-sync.",
        "IRD ले बिल सुरक्षित गर्न सकेन। PAN र रकम जाँच गरी पुनः सिंक गर्नुहोस्।",
    ),
    103: Outcome(
        "retry", "IRD had an unexpected error. Retrying automatically.",
        "IRD मा अप्रत्याशित त्रुटि भयो। स्वतः पुनः प्रयास हुँदैछ।",
    ),
    104: Outcome(
        "failed", "IRD says the invoice data is invalid. Fix it, then re-sync.",
        "IRD अनुसार बिलको विवरण अमान्य छ। सच्याएर पुनः सिंक गर्नुहोस्।",
    ),
    # billreturn only. The original was already accepted (we wait for that before
    # sending a credit note), so treat this as IRD lagging and retry.
    105: Outcome(
        "retry", "IRD can't find the original invoice yet. Retrying automatically.",
        "IRD ले मूल बिल अझै फेला पारेन। स्वतः पुनः प्रयास हुँदैछ।",
    ),
}

UNREACHABLE = Outcome(
    "retry", "Could not reach IRD. Retrying automatically.",
    "IRD सम्म पुग्न सकिएन। स्वतः पुनः प्रयास हुँदैछ।",
)
UNEXPECTED = Outcome(
    "retry", "IRD sent an unexpected response. Retrying automatically.",
    "IRD बाट अप्रत्याशित जवाफ आयो। स्वतः पुनः प्रयास हुँदैछ।",
)
NO_CREDENTIALS = Outcome(
    "failed", "Add your IRD credentials in Settings, then re-sync.",
    "सेटिङमा IRD प्रमाणपत्र थपेर पुनः सिंक गर्नुहोस्।",
)
WAITING_FOR_ORIGINAL = Outcome(
    "retry", "Waiting for the original invoice to reach IRD.",
    "मूल बिल IRD मा पुग्न पर्खँदै।",
)
GAVE_UP = Outcome(
    "failed", "IRD kept failing, so automatic retries stopped. Re-sync to try again.",
    "IRD मा बारम्बार त्रुटि भएकाले स्वतः प्रयास रोकियो। फेरि प्रयास गर्न पुनः सिंक गर्नुहोस्।",
)

_NE_BY_EN = {
    o.en: o.ne
    for o in (*CODES.values(), UNREACHABLE, UNEXPECTED, NO_CREDENTIALS,
              WAITING_FOR_ORIGINAL, GAVE_UP)
}


def for_code(code: int) -> Outcome:
    return CODES.get(code, UNEXPECTED)


def to_nepali(en: str | None) -> str | None:
    """Nepali text for a stored (English) sync message."""
    return _NE_BY_EN.get(en) if en else None
