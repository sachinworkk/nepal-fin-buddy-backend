"""Golden question set for the RAG eval.

Each case: a question, which document should be retrieved, and the key facts a
good answer must contain. expected_answer_hint doubles as expected_output for
contextual recall — keep it to the essential facts, not prose.
"""


import os

# EVAL_SMALL=1 trims the corpus and question set for a quick live run.
_SMALL_TITLES = {
    "Rent note", "Bhatbhateni groceries", "WorldLink internet",
    "Salary slip Shrawan", "Dashain kharcha note",
}


def seed_documents():
    """(kind, title, text) corpus the eval questions are asked against."""
    docs = _all_documents()
    if os.environ.get("EVAL_SMALL") == "1":
        docs = [d for d in docs if d[1] in _SMALL_TITLES]
    return docs


def _all_documents():
    rent = (
        "Rent receipt for the month of Shrawan 2083 BS. Landlord: Kumar Shrestha. "
        "Tenant paid Rs. 20,000.00 in cash on 2083-04-15 BS (2026-07-31 AD). "
        "Property address: Baneshwor, Kathmandu. No VAT applied on residential rent. "
        "Receipt number R-1042. "
    ) * 3
    groceries = (
        "Bhatbhateni Supermarket bill. Date: 2083-05-02 BS (2026-08-18 AD). "
        "Items: rice 25kg Rs. 2,450.00; dal 5kg Rs. 1,150.00; cooking oil 5L Rs. 1,875.00; "
        "vegetables Rs. 940.00; milk 4L Rs. 480.00. Subtotal Rs. 6,895.00. "
        "VAT 13% Rs. 896.35. Total Rs. 7,791.35. PAN 601234567. Bill no. BB-88991. "
        "Paid by eSewa. "
    ) * 3
    internet = (
        "WorldLink monthly internet invoice. Billing month: Shrawan 2083. "
        "Plan: 150Mbps fiber, Rs. 3,500.00 per month. VAT 13% Rs. 455.00. "
        "Total Rs. 3,955.00. Invoice no. WL-2083-05-3301. Due date 2083-05-25 BS. "
        "Paid via connectIPS on 2083-05-20 BS. "
    ) * 3
    salary = (
        "Salary slip for Shrawan 2083. Employer: Swift Technology Pvt. Ltd. "
        "Gross salary Rs. 120,000.00. SSF employee contribution 11% Rs. 13,200.00. "
        "Tax deducted at source (TDS) Rs. 8,500.00. Net salary credited to "
        "NIC Asia Bank account: Rs. 98,300.00. "
    ) * 3
    bigmart = (
        "BigMart bill. Date: 2083-05-09 BS. Items: atta 10kg Rs. 1,300.00; sugar 5kg "
        "Rs. 600.00; tea 500g Rs. 450.00; detergent Rs. 780.00. Subtotal Rs. 3,130.00. "
        "VAT 13% Rs. 406.90. Total Rs. 3,536.90. PAN 302345678. Bill no. BM-20417. "
        "Paid in cash."
    )
    electricity = (
        "Nepal Electricity Authority monthly bill, Kathmandu Distribution Center. "
        "Customer no. 123.45.678. Billing month: Shrawan 2083. Units consumed: 186 kWh. "
        "Energy charge Rs. 1,512.00. Service charge Rs. 100.00. Total payable Rs. 1,612.00. "
        "Paid via eSewa on 2083-05-10 BS."
    )
    mobile = (
        "Nepal Telecom postpaid mobile invoice. Number 9841234567. Invoice no. NT-5521. "
        "Monthly plan Rs. 800.00. VAT 13% Rs. 104.00. Total Rs. 904.00. "
        "Paid via Khalti on 2083-05-12 BS."
    )
    kalimati = (
        "Kalimati Fruits and Vegetable Market purchase slip, 2083-05-06 BS. "
        "Tomato 3kg Rs. 270.00; potato 5kg Rs. 400.00; onion 2kg Rs. 240.00; "
        "cauliflower 2kg Rs. 300.00. Total Rs. 1,210.00. No VAT. Paid in cash."
    )
    restaurant = (
        "Newa Momo Restaurant bill. Date: 2083-05-14 BS. Bill no. NM-3308. "
        "Buff momo 2 plates Rs. 560.00; chowmein Rs. 220.00; lassi 2 glasses Rs. 300.00. "
        "Subtotal Rs. 1,080.00. Service charge 10% Rs. 108.00. VAT 13% Rs. 154.44. "
        "Total Rs. 1,342.44. Paid in cash."
    )
    daraz = (
        "Daraz order invoice. Order no. DZ-77120934. Date: 2083-05-18 BS. "
        "Item: wireless earbuds Rs. 2,499.00. Delivery fee Rs. 100.00. "
        "Voucher discount Rs. 300.00. Total paid Rs. 2,299.00. "
        "Payment method: cash on delivery."
    )
    pathao = (
        "Pathao ride receipt. Trip ID PT-9981. Date: 2083-05-20 BS. "
        "Pickup: Baneshwor. Drop: Thamel. Distance 6.4 km. Fare Rs. 285.00. Paid in cash."
    )
    clinic = (
        "Sajha Health Clinic receipt no. SHC-1190. Date: 2083-05-22 BS. "
        "Doctor: Dr. Anita Rai. Consultation Rs. 500.00; blood test (CBC) Rs. 700.00; "
        "medicines Rs. 640.00. Total Rs. 1,840.00."
    )
    school = (
        "Sunrise English School fee receipt no. SES-0742 for Shrawan 2083. "
        "Student: Asha Thapa, Class 6. Tuition fee Rs. 4,500.00; transport fee "
        "Rs. 1,200.00; exam fee Rs. 300.00. Total Rs. 6,000.00. Paid by bank deposit."
    )
    dashain = (
        "दशैंको खर्च (असोज २०८३)। नयाँ कपडा रु ५,५००। मिठाई तथा फलफूल रु २,२००। "
        "उपहार रु ३,०००। कुल खर्च रु १०,७००।"
    )
    return [
        ("note", "Rent note", rent),
        ("bill", "Bhatbhateni groceries", groceries),
        ("bill", "WorldLink internet", internet),
        ("note", "Salary slip Shrawan", salary),
        ("bill", "BigMart groceries", bigmart),
        ("bill", "NEA electricity", electricity),
        ("bill", "Nepal Telecom mobile", mobile),
        ("bill", "Kalimati vegetables", kalimati),
        ("bill", "Newa Momo restaurant", restaurant),
        ("bill", "Daraz order", daraz),
        ("bill", "Pathao ride", pathao),
        ("bill", "Sajha clinic", clinic),
        ("bill", "Sunrise school fee", school),
        ("note", "Dashain kharcha note", dashain),
    ]


def golden_cases():
    cases = _all_cases()
    if os.environ.get("EVAL_SMALL") == "1":
        cases = [c for c in cases
                 if not c["must_retrieve"] or c["must_retrieve"] in _SMALL_TITLES]
    return cases


def _all_cases():
    return [
        {
            "id": "rent_amount",
            "question": "How much rent did I pay and to whom?",
            "must_retrieve": "Rent note",
            "expected_answer_hint": "You paid Rs. 20,000 rent to Kumar Shrestha for Shrawan 2083.",
        },
        {
            "id": "grocery_vat",
            "question": "What was the VAT on my Bhatbhateni grocery bill?",
            "must_retrieve": "Bhatbhateni groceries",
            "expected_answer_hint": "VAT on the Bhatbhateni bill was Rs. 896.35, total Rs. 7,791.35.",
        },
        {
            "id": "internet_due",
            "question": "When is my WorldLink internet bill due and how much is it?",
            "must_retrieve": "WorldLink internet",
            "expected_answer_hint": "The WorldLink bill is Rs. 3,955.00 and was due on 2083-05-25 BS.",
        },
        {
            "id": "net_salary",
            "question": "What was my net salary last month?",
            "must_retrieve": "Salary slip Shrawan",
            "expected_answer_hint": "Net salary was Rs. 98,300.00 credited to NIC Asia Bank.",
        },
        {
            "id": "biggest_grocery_bill",
            "question": "How much was my Bhatbhateni grocery bill in total?",
            "must_retrieve": "Bhatbhateni groceries",
            "expected_answer_hint": "The Bhatbhateni grocery bill total was Rs. 7,791.35.",
        },
        {
            "id": "missing_info_refusal",
            "question": "How much did I spend on fuel last month?",
            "must_retrieve": None,
            "expected_answer_hint": (
                "I don't have any information about fuel expenses in your documents."
            ),
        },
        # --- single-fact lookups -------------------------------------------------
        {
            "id": "electricity_total",
            "question": "How much was my electricity bill and how many units did I use?",
            "must_retrieve": "NEA electricity",
            "expected_answer_hint": "The NEA bill was Rs. 1,612.00 for 186 kWh.",
        },
        {
            "id": "mobile_vat",
            "question": "How much VAT did I pay on my mobile postpaid bill?",
            "must_retrieve": "Nepal Telecom mobile",
            "expected_answer_hint": "VAT on the Nepal Telecom bill was Rs. 104.00, total Rs. 904.00.",
        },
        {
            "id": "kalimati_total",
            "question": "How much did I spend on vegetables at Kalimati?",
            "must_retrieve": "Kalimati vegetables",
            "expected_answer_hint": "The Kalimati vegetable purchase came to Rs. 1,210.00.",
        },
        {
            "id": "restaurant_total",
            "question": "What was the total of my momo restaurant bill?",
            "must_retrieve": "Newa Momo restaurant",
            "expected_answer_hint": "The Newa Momo Restaurant bill total was Rs. 1,342.44.",
        },
        {
            "id": "restaurant_service_charge",
            "question": "What service charge was added at the momo restaurant?",
            "must_retrieve": "Newa Momo restaurant",
            "expected_answer_hint": "A 10% service charge of Rs. 108.00 was added.",
        },
        {
            "id": "daraz_discount",
            "question": "How much discount did I get on my Daraz order and what did I finally pay?",
            "must_retrieve": "Daraz order",
            "expected_answer_hint": "A voucher discount of Rs. 300.00; total paid Rs. 2,299.00.",
        },
        {
            "id": "daraz_payment_method",
            "question": "Did I pay for the Daraz earbuds online or on delivery?",
            "must_retrieve": "Daraz order",
            "expected_answer_hint": "The Daraz order was paid by cash on delivery.",
        },
        {
            "id": "pathao_fare",
            "question": "How much was my Pathao ride from Baneshwor to Thamel?",
            "must_retrieve": "Pathao ride",
            "expected_answer_hint": "The Pathao fare was Rs. 285.00 for 6.4 km.",
        },
        {
            "id": "clinic_total",
            "question": "What did my clinic visit cost in total?",
            "must_retrieve": "Sajha clinic",
            "expected_answer_hint": "The Sajha Health Clinic visit cost Rs. 1,840.00 in total.",
        },
        {
            "id": "school_fee_total",
            "question": "How much was the school fee for Shrawan?",
            "must_retrieve": "Sunrise school fee",
            "expected_answer_hint": (
                "The Sunrise English School fee was Rs. 6,000.00 "
                "(tuition 4,500.00, transport 1,200.00, exam 300.00)."
            ),
        },
        {
            "id": "internet_plan",
            "question": "What internet speed am I paying for and how much per month?",
            "must_retrieve": "WorldLink internet",
            "expected_answer_hint": "A 150Mbps fiber plan at Rs. 3,500.00 per month.",
        },
        {
            "id": "line_item_cooking_oil",
            "question": "How much did I pay for cooking oil?",
            "must_retrieve": "Bhatbhateni groceries",
            "expected_answer_hint": "Cooking oil 5L cost Rs. 1,875.00 on the Bhatbhateni bill.",
        },
        {
            "id": "salary_ssf",
            "question": "How much SSF was deducted from my salary?",
            "must_retrieve": "Salary slip Shrawan",
            "expected_answer_hint": "SSF employee contribution of Rs. 13,200.00 (11%).",
        },
        {
            "id": "salary_tds",
            "question": "How much tax was deducted at source from my salary?",
            "must_retrieve": "Salary slip Shrawan",
            "expected_answer_hint": "Tax deducted at source (TDS) was Rs. 8,500.00.",
        },
        {
            "id": "payment_method_bhatbhateni",
            "question": "How did I pay the Bhatbhateni bill?",
            "must_retrieve": "Bhatbhateni groceries",
            "expected_answer_hint": "The Bhatbhateni bill was paid by eSewa.",
        },
        # --- disambiguation between similar documents ----------------------------
        {
            "id": "bigmart_total",
            "question": "What was the total on my BigMart bill?",
            "must_retrieve": "BigMart groceries",
            "expected_answer_hint": "The BigMart bill total was Rs. 3,536.90.",
        },
        {
            "id": "bigmart_vat_not_bhatbhateni",
            "question": "How much VAT did I pay at BigMart?",
            "must_retrieve": "BigMart groceries",
            "expected_answer_hint": "VAT at BigMart was Rs. 406.90, not the Bhatbhateni amount.",
        },
        {
            "id": "lookup_by_bill_number",
            "question": "Which store issued bill number BM-20417 and what was the total?",
            "must_retrieve": "BigMart groceries",
            "expected_answer_hint": "Bill BM-20417 is from BigMart, total Rs. 3,536.90.",
        },
        {
            "id": "lookup_by_pan",
            "question": "Which bill carries PAN 601234567?",
            "must_retrieve": "Bhatbhateni groceries",
            "expected_answer_hint": "PAN 601234567 is on the Bhatbhateni Supermarket bill.",
        },
        # --- Romanized Nepali and Devanagari questions ---------------------------
        {
            "id": "romanized_rent",
            "question": "Maile ghar bhada kati tirein?",
            "must_retrieve": "Rent note",
            "expected_answer_hint": "You paid Rs. 20,000 rent for Shrawan 2083.",
        },
        {
            "id": "romanized_electricity",
            "question": "Bijuli ko bill kati aayo?",
            "must_retrieve": "NEA electricity",
            "expected_answer_hint": "The electricity bill was Rs. 1,612.00.",
        },
        {
            "id": "romanized_dashain",
            "question": "Dashain ma jamma kati kharcha bhayo?",
            "must_retrieve": "Dashain kharcha note",
            "expected_answer_hint": "Total Dashain spending was Rs. 10,700.",
        },
        {
            "id": "devanagari_dashain",
            "question": "दशैंमा कति खर्च भयो?",
            "must_retrieve": "Dashain kharcha note",
            "expected_answer_hint": "Total Dashain spending was Rs. 10,700.",
        },
        {
            "id": "devanagari_net_salary",
            "question": "मेरो खुद तलब कति थियो?",
            "must_retrieve": "Salary slip Shrawan",
            "expected_answer_hint": "Net salary was Rs. 98,300.00.",
        },
        # --- unanswerable: the bot must say it doesn't know ----------------------
        {
            "id": "refuse_bank_balance",
            "question": "What is my current bank account balance?",
            "must_retrieve": None,
            "expected_answer_hint": (
                "I don't have information about your current bank balance in your documents."
            ),
        },
        {
            "id": "refuse_landlord_phone",
            "question": "What is my landlord's phone number?",
            "must_retrieve": None,
            "expected_answer_hint": (
                "The documents name the landlord, Kumar Shrestha, but contain no phone number."
            ),
        },
        # --- bill-change requests: the canned reply, never the scope refusal -----
        {
            "id": "bill_change_vendor",
            "question": "Change the vendor on my Bhatbhateni bill to Big Mart.",
            "must_retrieve": None,
            "expected_answer_hint": (
                "Bills can't be changed from chat. Before confirming, any field can be "
                "corrected on the Snap screen; once confirmed the vendor, total and date "
                "stay as scanned, so upload the bill again to change those. The expense the "
                "bill created can be changed or deleted from chat, and its PAN, bill number "
                "and VAT amounts are editable in the expense's Edit form."
            ),
        },
    ]