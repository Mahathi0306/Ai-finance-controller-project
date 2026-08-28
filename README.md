# Transaction Reconciliation Engine

A high-throughput, multi-tiered financial reconciliation system designed to automate ledger matching between bank statements and internal sales records. The system handles deterministic matching, merchant discount rate (MDR) fee deductions, multi-day settlement windows, grouped batch payouts, and automated exception routing.

---

## Architecture Overview

The pipeline resolves transactions through four discrete evaluation stages:

1. **Deterministic Code Matching (Tier 1):** Performs boundary-validated string matching on transaction references and gross amounts with zero computational overhead.
2. **Structural Variance Analysis (Tier 2):** Identifies partial settlements, calculates remaining balances, and isolates ambiguous duplicate customer orders to prevent erroneous matching.
3. **Tolerance & Fee Adjudication (Tier 3):** Reconciles gateway deductions against standard fee tolerances (1.0% to 2.5%) across allowable settlement lag windows (1 to 3 business days).
4. **Combinatorial Batch Settlement (Tier 3.5):** Solves $N:1$ aggregated bank payout lines against open sales ledger orders using bounded subset sum combinations.
5. **Exception Queue (Tier 4):** Flags duplicate deposits, unmapped bank credits, and unpaid orders for manual audit.

---

## Tech Stack

* **Backend:** FastAPI, Uvicorn, Python 3.10+
* **Database & Auditing:** SQLite3
* **Frontend:** Vue.js 3, Tailwind CSS
* **Configuration:** python-dotenv

---

## Project Structure

```text
├── app.py                  # Core FastAPI application and reconciliation pipeline
├── generate_data.py        # Synthetic dataset generator for testing scenarios
├── benchmark.py            # Evaluation benchmark against verified ground truth
├── index.html              # Monitoring and audit dashboard
├── requirements.txt        # Project dependencies
├── .env.example            # Environment configuration template
├── data/                   # Generated transaction records
│   ├── bank_records.json
│   └── company_records.json
└── evaluation/             # Ground truth datasets for verification
    └── ground_truth.json