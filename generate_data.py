import os
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EVAL_DIR = BASE_DIR / "evaluation"

DATA_DIR.mkdir(exist_ok=True)
EVAL_DIR.mkdir(exist_ok=True)

random.seed(42)

names = [
    "Ramesh Kumar", "Priya Sharma", "Ananya Rao", "Vikram Patel",
    "Sneha Reddy", "Arjun Mehta", "Rahul Verma", "Kavita Nair",
    "Siddharth Joshi", "Deepa Iyer", "Manish Gupta", "Pooja Hegde"
]

company_records = []
bank_records = []
ground_truth = {
    "exact_matches": {},
    "fee_deduction_matches": {},
    "batch_matches": {},
    "partial_matches": {},
    "duplicates": [],
    "ambiguous_cases": [],
    "unmatched_bank": [],
    "unmatched_company": []
}

base_date = datetime(2026, 8, 3)

def add_business_days(start_date, num_days):
    cur = start_date
    added = 0
    while added < num_days:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur

tx_counter = 1000
bank_counter = 5000

for _ in range(75):
    tx_counter += 1
    bank_counter += 1
    tx_id = f"TXN_{tx_counter}"
    bank_id = f"BANK_{bank_counter}"
    person = random.choice(names)
    amount = round(random.uniform(500, 15000), 2)
    sale_dt = (base_date + timedelta(days=random.randint(0, 10))).strftime("%Y-%m-%d")

    company_records.append({
        "internal_id": tx_id,
        "customer_name": person,
        "amount": amount,
        "date": sale_dt,
        "status": "OPEN"
    })
    bank_records.append({
        "bank_tx_id": bank_id,
        "narration": f"UPI/{tx_id}/{person.upper().replace(' ', '')}",
        "amount": amount,
        "date": sale_dt
    })
    ground_truth["exact_matches"][bank_id] = [tx_id]

for _ in range(2):
    tx_counter += 1
    bank_counter += 1
    tx_id = f"TXN_{tx_counter}"
    bank_id = f"BANK_{bank_counter}"
    person = random.choice(names)
    orig_amount = round(random.uniform(3000, 12000), 2)
    settled_amount = round(orig_amount * 0.982, 2)

    sale_dt = base_date + timedelta(days=5)
    settle_dt = add_business_days(sale_dt, 1)
    short_name = person.split()[0].upper()

    company_records.append({
        "internal_id": tx_id,
        "customer_name": person,
        "amount": orig_amount,
        "date": sale_dt.strftime("%Y-%m-%d"),
        "status": "OPEN"
    })
    bank_records.append({
        "bank_tx_id": bank_id,
        "narration": f"CMS/INB/{tx_id[-4:]}/{short_name}/MDR",
        "amount": settled_amount,
        "date": settle_dt.strftime("%Y-%m-%d")
    })
    ground_truth["fee_deduction_matches"][bank_id] = [tx_id]

batch_tx_ids = []
batch_sum = 0.0
sale_dt = base_date + timedelta(days=6)
bank_counter += 1
bank_id = f"BANK_{bank_counter}"

for _ in range(3):
    tx_counter += 1
    tx_id = f"TXN_{tx_counter}"
    person = random.choice(names)
    amount = round(random.uniform(2000, 5000), 2)
    batch_sum += amount
    batch_tx_ids.append(tx_id)

    company_records.append({
        "internal_id": tx_id,
        "customer_name": person,
        "amount": amount,
        "date": sale_dt.strftime("%Y-%m-%d"),
        "status": "OPEN"
    })

settled_batch_val = round(batch_sum * 0.982, 2)
settle_dt = add_business_days(sale_dt, 1)

bank_records.append({
    "bank_tx_id": bank_id,
    "narration": "RAZORPAY/BATCH_STLMT/K3_N1",
    "amount": settled_batch_val,
    "date": settle_dt.strftime("%Y-%m-%d")
})
ground_truth["batch_matches"][bank_id] = batch_tx_ids

for _ in range(2):
    tx_counter += 1
    bank_counter += 1
    tx_id = f"TXN_{tx_counter}"
    bank_id = f"BANK_{bank_counter}"
    person = random.choice(names)
    total_val = 10000.00
    part_val = 5000.00
    sale_dt = (base_date + timedelta(days=7)).strftime("%Y-%m-%d")

    company_records.append({
        "internal_id": tx_id,
        "customer_name": person,
        "amount": total_val,
        "date": sale_dt,
        "status": "OPEN"
    })
    bank_records.append({
        "bank_tx_id": bank_id,
        "narration": f"NEFT/PARTIAL/{tx_id}/{person.split()[0].upper()}",
        "amount": part_val,
        "date": sale_dt
    })
    ground_truth["partial_matches"][bank_id] = {
        "company_id": tx_id,
        "paid": part_val,
        "outstanding": total_val - part_val
    }

for _ in range(2):
    tx_counter += 1
    tx_id = f"TXN_{tx_counter}"
    person = random.choice(names)
    dup_val = 4500.00
    sale_dt = (base_date + timedelta(days=8)).strftime("%Y-%m-%d")

    company_records.append({
        "internal_id": tx_id,
        "customer_name": person,
        "amount": dup_val,
        "date": sale_dt,
        "status": "OPEN"
    })

    for _ in range(2):
        bank_counter += 1
        bank_id = f"BANK_{bank_counter}"
        bank_records.append({
            "bank_tx_id": bank_id,
            "narration": f"UPI/DUP_REF_{tx_id}/{person.split()[0].upper()}",
            "amount": dup_val,
            "date": sale_dt
        })
        ground_truth["duplicates"].append(bank_id)

for _ in range(2):
    person = "Priya Sharma"
    ambig_amount = 5000.00
    amb_ids = []
    sale_dt = (base_date + timedelta(days=9)).strftime("%Y-%m-%d")

    for _ in range(2):
        tx_counter += 1
        tx_id = f"TXN_{tx_counter}"
        amb_ids.append(tx_id)
        company_records.append({
            "internal_id": tx_id,
            "customer_name": person,
            "amount": ambig_amount,
            "date": sale_dt,
            "status": "OPEN"
        })

    bank_counter += 1
    bank_id = f"BANK_{bank_counter}"
    bank_records.append({
        "bank_tx_id": bank_id,
        "narration": "NEFT/PRIYA_SHARMA/GENERIC_REF",
        "amount": ambig_amount,
        "date": sale_dt
    })
    ground_truth["ambiguous_cases"].append({"bank_id": bank_id, "candidates": amb_ids})

for _ in range(4):
    bank_counter += 1
    bank_id = f"BANK_{bank_counter}"
    bank_records.append({
        "bank_tx_id": bank_id,
        "narration": f"IMPS/SUSPENSE_CREDIT/ACC{random.randint(100000, 999999)}",
        "amount": round(random.uniform(1000, 7000), 2),
        "date": (base_date + timedelta(days=12)).strftime("%Y-%m-%d")
    })
    ground_truth["unmatched_bank"].append(bank_id)

for _ in range(4):
    tx_counter += 1
    tx_id = f"TXN_{tx_counter}"
    company_records.append({
        "internal_id": tx_id,
        "customer_name": random.choice(names),
        "amount": round(random.uniform(1000, 7000), 2),
        "date": (base_date + timedelta(days=12)).strftime("%Y-%m-%d"),
        "status": "OPEN"
    })
    ground_truth["unmatched_company"].append(tx_id)

random.shuffle(bank_records)

with open(DATA_DIR / "company_records.json", "w") as f:
    json.dump(company_records, f, indent=2)

with open(DATA_DIR / "bank_records.json", "w") as f:
    json.dump(bank_records, f, indent=2)

with open(EVAL_DIR / "ground_truth.json", "w") as f:
    json.dump(ground_truth, f, indent=2)

print(f"Generated {len(company_records)} Company Records and {len(bank_records)} Bank Records.")