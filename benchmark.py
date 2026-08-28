import json
import time
from pathlib import Path
from app import run_reconciliation

BASE_DIR = Path(__file__).resolve().parent
EVAL_DIR = BASE_DIR / "evaluation"

print("\n" + "="*70)
print("     RAZORPAY AI FINANCE CONTROLLER — RECONCILIATION BENCHMARK")
print("="*70)

print("\n[1/3] Loading transaction datasets...")
time.sleep(0.2)

print("[2/3] Executing 4-Layer Autonomous Reconciliation Engine...")
start_time = time.time()
results = run_reconciliation()
elapsed = round(time.time() - start_time, 2)

print(f"[3/3] Reconciliation complete in {elapsed}s!\n")

summary = results["summary"]
matched = results["matched"]
exceptions = results["exceptions"]

with open(EVAL_DIR / "ground_truth.json", "r") as f:
    ground_truth = json.load(f)

matched_lookup = {m["bank_tx_id"]: sorted(m["internal_ids"]) for m in matched}

true_positives = 0
false_positives = 0
false_negatives = 0

all_expected_matches = {}
all_expected_matches.update(ground_truth.get("exact_matches", {}))
all_expected_matches.update(ground_truth.get("fee_deduction_matches", {}))
all_expected_matches.update(ground_truth.get("batch_matches", {}))

for bank_id, expected_ids in all_expected_matches.items():
    actual_ids = matched_lookup.get(bank_id, [])
    if sorted(actual_ids) == sorted(expected_ids):
        true_positives += 1
    elif not actual_ids:
        false_negatives += 1
    else:
        false_positives += 1

exception_ids = {e["record_id"] for e in exceptions}
for dup_id in ground_truth.get("duplicates", []):
    if dup_id in exception_ids:
        true_positives += 1

for amb in ground_truth.get("ambiguous_cases", []):
    if amb["bank_id"] in exception_ids:
        true_positives += 1

total_eval_scenarios = len(all_expected_matches) + len(ground_truth.get("duplicates", [])) + len(ground_truth.get("ambiguous_cases", []))
precision = round((true_positives / (true_positives + false_positives)) * 100, 2) if (true_positives + false_positives) > 0 else 0.0
recall = round((true_positives / total_eval_scenarios) * 100, 2) if total_eval_scenarios > 0 else 0.0
fp_rate = round((false_positives / summary["bank_records_processed"]) * 100, 2)

print("="*70)
print("                     EXECUTIVE EVALUATION REPORT")
print("="*70)
print(f"Run Identifier                 : {summary['run_id']}")
print(f"Total Transactions Processed   : {summary['records_processed']} records")
print(f"├── Bank Statement Lines Ingested : {summary['bank_records_processed']}")
print(f"└── Company Sales Orders Ingested : {summary['company_records_processed']}")
print(f"\nFinancial Resolution Overview:")
print(f"├── Money Reconciled           : {summary['money_reconciled_inr']}")
print(f"├── Money At Risk (Review)     : {summary['money_at_risk_inr']}")
print(f"└── Settlement Resolution Rate : {summary['bank_settlement_match_rate']}")
print(f"\nArchitecture Breakdown:")
print(f"├── Layer 1 Exact Matches      : {summary['exact_code_matches']} (Deterministic - 0 Tokens)")
print(f"├── Layer 3 AI 1:1 Matches     : {summary['ai_1to1_matches']} (MDR / Lag Adjudicated)")
print(f"├── Layer 3.5 AI Batch Matches : {summary['ai_batch_matches']} (2:1 to 5:1 Aggregations)")
print(f"└── Layer 4 Human Review Queue : {summary['unresolved_exceptions']} Exceptions Flagged")
print(f"\nSystem Performance & Economics:")
print(f"├── Execution Time             : {summary['processing_time_sec']} sec")
print(f"├── Throughput                 : {summary['throughput_records_per_sec']} records/sec")
print(f"├── Tokens Consumed            : {summary['total_tokens_consumed']} tokens")
print(f"└── Total AI Cost              : {summary['total_reconciliation_cost_inr']} INR")
print("\n" + "-"*70)
print("FINANCIAL ACCURACY METRICS (GROUND TRUTH VERIFIED)")
print("-"*70)
print(f"Precision (Zero Cash Leakage)  : {precision}%")
print(f"Recall (Match Capture Rate)    : {recall}%")
print(f"False Positive Rate            : {fp_rate}% (Target: 0.0%)")
print("="*70)

batch_samples = [m for m in matched if "BATCH" in m["match_type"]]
if batch_samples:
    s = batch_samples[0]
    print("\nSAMPLE BATCH ADJUDICATION REASONING TRACE:")
    print(f"Bank Record   : {s['bank_tx_id']} (₹{s['bank_amount']})")
    print(f"Matched Orders: {s['internal_ids']} (Sum: ₹{s['company_amount']})")
    print(f"Source Badge  : {s['source']}")
    print(f"Confidence    : {int(s['confidence']*100)}%")
    print(f"Structured    : {s.get('structured_reasoning')}")
    print(f"Audit Summary : \"{s['reasoning']}\"\n")