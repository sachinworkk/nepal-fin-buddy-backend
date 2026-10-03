"""Golden question set for the RAG eval.

Each case: a question, which document should be retrieved, and the key facts a
good answer must contain. expected_answer_hint doubles as expected_output for
contextual recall — keep it to the essential facts, not prose.
"""


def seed_documents():
    """(kind, title, text) corpus the eval questions are asked against."""
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
    return [
        ("note", "Rent note", rent),
        ("bill", "Bhatbhateni groceries", groceries),
        ("bill", "WorldLink internet", internet),
        ("note", "Salary slip Shrawan", salary),
    ]


def golden_cases():
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
    ]