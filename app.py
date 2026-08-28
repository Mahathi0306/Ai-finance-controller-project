import os
import re
import json
import time
import uuid
import sqlite3
import itertools
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from google import genai
from google.genai import types

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "reconciliation.db"
load_dotenv(BASE_DIR / ".env")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

USD_TO_INR = 87.50
# Gemini 3.6 Flash standard pricing (verified against Google's pricing page):
# $1.50 / 1M input tokens, $7.50 / 1M output tokens.
COST_PER_MILLION_INPUT = 1.50
COST_PER_MILLION_OUTPUT = 7.50

def get_db_connection():
    return sqlite3.connect(DB_PATH, timeout=25.0)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            run_id TEXT PRIMARY KEY,
            timestamp DATETIME,
            match_rate TEXT,
            money_reconciled REAL,
            money_at_risk REAL,
            total_matches INTEGER,
            total_exceptions INTEGER,
            total_tokens INTEGER,
            total_cost_inr REAL,
            processing_time_sec REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reconciled_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            bank_tx_id TEXT,
            internal_ids TEXT,
            bank_amount REAL,
            company_amount REAL,
            match_type TEXT,
            source TEXT,
            confidence REAL,
            reasoning TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            record_id TEXT,
            source TEXT,
            amount REAL,
            category TEXT,
            confidence REAL,
            reason TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT,
            record_id TEXT,
            layer TEXT,
            target_ids TEXT,
            verdict TEXT,
            confidence REAL,
            source TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d")

def id_in_narration(internal_id, narration):
    """Token-boundary match instead of naive substring match, so e.g. 'TXN_102'
    can never falsely match inside 'TXN_1023'."""
    pattern = rf'(?<![A-Za-z0-9_]){re.escape(internal_id)}(?![A-Za-z0-9_])'
    return re.search(pattern, narration) is not None

def business_days_between(d1, d2):
    if d1 > d2:
        return -business_days_between(d2, d1)
    cur = d1
    b_days = 0
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            b_days += 1
    return b_days

def ask_llm_adjudicator(bank_entry, candidates, is_batch=False):
    expected_sum = sum(c["amount"] for c in candidates)
    amount_diff = round(expected_sum - bank_entry["amount"], 2)
    fee_pct = round((amount_diff / expected_sum) * 100, 2) if expected_sum > 0 else 0.0

    if not client:
        if 1.0 <= fee_pct <= 2.5:
            return {
                "verdict": "MATCH",
                "confidence": 0.95,
                "structured_reasoning": {
                    "reference_match": "High",
                    "variance_pct": f"{fee_pct}%",
                    "mdr_status": "Within standard tolerance (1.0%-2.5%)",
                    "settlement_lag": "1-2 business days"
                },
                "reasoning": f"Matches within {fee_pct}% fee tolerance (diff ₹{amount_diff}).",
                "source": "RULE_BASED_FALLBACK",
                "input_tokens": 0,
                "output_tokens": 0
            }
        return {
            "verdict": "AMBIGUOUS",
            "confidence": 0.65,
            "structured_reasoning": {
                "reference_match": "Uncertain",
                "variance_pct": f"{fee_pct}%",
                "mdr_status": "Exceeds tolerance",
                "settlement_lag": "Unknown"
            },
            "reasoning": "Variance exceeds tolerance thresholds.",
            "source": "RULE_BASED_FALLBACK",
            "input_tokens": 0,
            "output_tokens": 0
        }

    candidate_str = "\n".join([
        f"- ID: {c['internal_id']}, Customer: {c['customer_name']}, Expected: ₹{c['amount']}, Date: {c['date']}"
        for c in candidates
    ])

    prompt = f"""You are an autonomous finance controller.
Evaluate if the Bank Statement record settles the candidate Company Sales record(s).

Bank Statement Entry:
- Amount Credited: ₹{bank_entry['amount']}
- Date: {bank_entry['date']}
- Narration: "{bank_entry['narration']}"

Company Candidate Order(s) (Aggregated Expected: ₹{expected_sum}):
{candidate_str}

Domain Principles:
1. Standard gateway fee deduction ranges from 1.0% to 2.5%.
2. Settlement lag is within 1 to 3 business days.
3. If identical amounts and names exist across multiple distinct internal IDs without unique references, output AMBIGUOUS.
4. If valid match, output MATCH (confidence >= 0.90).

Respond strictly in valid JSON format:
{{
  "verdict": "MATCH" | "AMBIGUOUS" | "NO_MATCH",
  "confidence": 0.95,
  "structured_reasoning": {{
    "reference_match": "Aligned / Truncated / Generic",
    "variance_pct": "X.X%",
    "mdr_status": "Within standard tolerance" | "Exceeds tolerance",
    "settlement_lag": "X business day(s)"
  }},
  "reasoning": "1-2 sentence concise factual justification"
}}"""

    try:
        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0
            )
        )
        usage = getattr(response, 'usage_metadata', None)
        inp_tok = getattr(usage, 'prompt_token_count', 140) if usage else 140
        out_tok = getattr(usage, 'candidates_token_count', 50) if usage else 50

        parsed = json.loads(response.text)
        parsed["source"] = "GEMINI_LIVE"
        parsed["input_tokens"] = inp_tok
        parsed["output_tokens"] = out_tok
        return parsed
    except Exception as e:
        print(f"[Gemini Exception]: {e}")
        return {
            "verdict": "MATCH" if 1.0 <= fee_pct <= 2.5 else "AMBIGUOUS",
            "confidence": 0.92 if 1.0 <= fee_pct <= 2.5 else 0.60,
            "structured_reasoning": {
                "reference_match": "Evaluated via fallback",
                "variance_pct": f"{fee_pct}%",
                "mdr_status": "Within tolerance" if 1.0 <= fee_pct <= 2.5 else "Exceeds tolerance",
                "settlement_lag": "1-2 business days"
            },
            "reasoning": f"Fee deduced at {fee_pct}% (diff ₹{amount_diff}).",
            "source": "RULE_BASED_FALLBACK",
            "input_tokens": 0,
            "output_tokens": 0
        }

@app.get("/")
def get_dashboard():
    return FileResponse(BASE_DIR / "index.html")

@app.get("/api/reconcile")
def run_reconciliation():
    start_time = time.time()
    run_id = f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    with open(DATA_DIR / "company_records.json", "r") as f:
        company_records = json.load(f)

    with open(DATA_DIR / "bank_records.json", "r") as f:
        bank_records = json.load(f)

    matched = []
    exceptions = []
    audit_logs = []

    unmatched_company = {r["internal_id"]: r for r in company_records}
    unmatched_bank = []

    narration_lookup = {}
    duplicate_bank_ids = set()

    for b in bank_records:
        key = (b["amount"], b["narration"])
        if key in narration_lookup:
            duplicate_bank_ids.add(b["bank_tx_id"])
            duplicate_bank_ids.add(narration_lookup[key])
        else:
            narration_lookup[key] = b["bank_tx_id"]

    for b in bank_records:
        if b["bank_tx_id"] in duplicate_bank_ids:
            exceptions.append({
                "record_id": b["bank_tx_id"],
                "source": "BANK",
                "amount": b["amount"],
                "category": "DUPLICATE_SUSPECTED",
                "confidence": 0.98,
                "reason": "Duplicate bank deposit detected with identical narration and amount."
            })
            continue

        exact_candidates = [
            c for c in unmatched_company.values()
            if b["amount"] == c["amount"] and id_in_narration(c["internal_id"], b["narration"])
        ]
        exact_match_id = None
        if exact_candidates:
            # If more than one candidate ties on amount+id-in-narration, prefer the
            # one whose date is closest to the bank record's date.
            exact_candidates.sort(key=lambda c: abs((parse_date(c["date"]) - parse_date(b["date"])).days))
            exact_match_id = exact_candidates[0]["internal_id"]

        if exact_match_id:
            c = unmatched_company[exact_match_id]
            matched.append({
                "bank_tx_id": b["bank_tx_id"],
                "internal_ids": [c["internal_id"]],
                "customer_names": c["customer_name"],
                "bank_amount": b["amount"],
                "company_amount": c["amount"],
                "match_type": "EXACT_CODE",
                "confidence": 1.0,
                "structured_reasoning": {
                    "reference_match": "Exact Internal ID matched in narration",
                    "variance_pct": "0.0%",
                    "mdr_status": "Gross Settlement",
                    "settlement_lag": "Same day"
                },
                "reasoning": "Deterministic exact match on transaction ID and amount.",
                "source": "DETERMINISTIC"
            })
            audit_logs.append({
                "record_id": b["bank_tx_id"],
                "layer": "LAYER_1_DETERMINISTIC",
                "target_ids": [c["internal_id"]],
                "verdict": "MATCH",
                "confidence": 1.0,
                "source": "DETERMINISTIC",
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning": "Deterministic exact match."
            })
            del unmatched_company[exact_match_id]
        else:
            unmatched_bank.append(b)

    remaining_bank = []
    for b in unmatched_bank:
        b_date = parse_date(b["date"])
        candidates = []
        partial_candidate = None

        partial_candidates = []
        for c_id, c in unmatched_company.items():
            c_date = parse_date(c["date"])
            b_diff = business_days_between(c_date, b_date)
            if 0 <= b_diff <= 3 and id_in_narration(c["internal_id"], b["narration"]) and b["amount"] < c["amount"]:
                partial_candidates.append((b_diff, c))

        if partial_candidates:
            # Prefer the closest settlement date among tied candidates.
            partial_candidates.sort(key=lambda x: x[0])
            partial_candidate = partial_candidates[0][1]

        if partial_candidate:
            outstanding = round(partial_candidate["amount"] - b["amount"], 2)
            exceptions.append({
                "record_id": b["bank_tx_id"],
                "source": "BANK",
                "amount": b["amount"],
                "category": "PARTIAL_SETTLEMENT",
                "confidence": 0.95,
                "reason": f"Partial installment for {partial_candidate['internal_id']}. ₹{b['amount']} received, ₹{outstanding} remaining."
            })
            audit_logs.append({
                "record_id": b["bank_tx_id"],
                "layer": "LAYER_2_PARTIAL_ANALYZER",
                "target_ids": [partial_candidate["internal_id"]],
                "verdict": "PARTIAL_SETTLEMENT",
                "confidence": 0.95,
                "source": "DETERMINISTIC",
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning": f"Partial payment. Balance: ₹{outstanding}."
            })
            continue

        for c_id, c in unmatched_company.items():
            c_date = parse_date(c["date"])
            b_diff = business_days_between(c_date, b_date)
            amount_ratio = b["amount"] / c["amount"]

            if 0 <= b_diff <= 3 and 0.96 <= amount_ratio <= 1.00:
                score = abs(c["amount"] - b["amount"]) + (b_diff * 10)
                candidates.append((score, c))

        if len(candidates) > 1 and len({c["customer_name"] for _, c in candidates}) == 1 and len({c["amount"] for _, c in candidates}) == 1:
            exceptions.append({
                "record_id": b["bank_tx_id"],
                "source": "BANK",
                "amount": b["amount"],
                "category": "AMBIGUOUS_MULTI_CANDIDATE",
                "confidence": 0.60,
                "reason": f"Multiple matching orders for {candidates[0][1]['customer_name']}."
            })
            audit_logs.append({
                "record_id": b["bank_tx_id"],
                "layer": "LAYER_2_AMBIGUITY_GATE",
                "target_ids": [c["internal_id"] for _, c in candidates],
                "verdict": "AMBIGUOUS",
                "confidence": 0.60,
                "source": "DETERMINISTIC",
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning": "Collision detected."
            })
            continue

        if candidates:
            candidates.sort(key=lambda x: x[0])
            best_candidate = candidates[0][1]

            ai_res = ask_llm_adjudicator(b, [best_candidate], is_batch=False)
            verdict = ai_res.get("verdict", "NO_MATCH")
            confidence = float(ai_res.get("confidence", 0.0))
            reasoning = ai_res.get("reasoning", "")
            structured_reasoning = ai_res.get("structured_reasoning", {})
            adjudication_source = ai_res.get("source", "UNKNOWN")

            audit_logs.append({
                "record_id": b["bank_tx_id"],
                "layer": "LAYER_3_AI_1TO1",
                "target_ids": [best_candidate["internal_id"]],
                "verdict": verdict,
                "confidence": confidence,
                "source": adjudication_source,
                "input_tokens": ai_res.get("input_tokens", 0),
                "output_tokens": ai_res.get("output_tokens", 0),
                "reasoning": reasoning
            })

            if verdict == "MATCH" and confidence >= 0.90 and best_candidate["internal_id"] in unmatched_company:
                matched.append({
                    "bank_tx_id": b["bank_tx_id"],
                    "internal_ids": [best_candidate["internal_id"]],
                    "customer_names": best_candidate["customer_name"],
                    "bank_amount": b["amount"],
                    "company_amount": best_candidate["amount"],
                    "match_type": "AI_1TO1_ADJUDICATED",
                    "confidence": confidence,
                    "structured_reasoning": structured_reasoning,
                    "reasoning": reasoning,
                    "source": adjudication_source
                })
                del unmatched_company[best_candidate["internal_id"]]
            else:
                remaining_bank.append(b)
        else:
            remaining_bank.append(b)

    final_unresolved_bank = []
    for b in remaining_bank:
        b_date = parse_date(b["date"])
        plausible_orders = [
            c for c in unmatched_company.values()
            if 0 <= business_days_between(parse_date(c["date"]), b_date) <= 3 and c["amount"] < b["amount"]
        ]
        plausible_orders.sort(key=lambda x: x["amount"], reverse=True)
        plausible_orders = plausible_orders[:12]

        found_batch = False
        for k in [2, 3, 4, 5]:
            if found_batch:
                break
            if len(plausible_orders) < k:
                continue
            for combo in itertools.combinations(plausible_orders, k):
                combo_sum = sum(item["amount"] for item in combo)
                ratio = b["amount"] / combo_sum if combo_sum > 0 else 0

                if 0.96 <= ratio <= 1.00:
                    ai_res = ask_llm_adjudicator(b, list(combo), is_batch=True)
                    verdict = ai_res.get("verdict", "NO_MATCH")
                    confidence = float(ai_res.get("confidence", 0.0))
                    reasoning = ai_res.get("reasoning", "")
                    structured_reasoning = ai_res.get("structured_reasoning", {})
                    adjudication_source = ai_res.get("source", "UNKNOWN")
                    target_ids = [item["internal_id"] for item in combo]

                    audit_logs.append({
                        "record_id": b["bank_tx_id"],
                        "layer": f"LAYER_3_AI_BATCH_{k}TO1",
                        "target_ids": target_ids,
                        "verdict": verdict,
                        "confidence": confidence,
                        "source": adjudication_source,
                        "input_tokens": ai_res.get("input_tokens", 0),
                        "output_tokens": ai_res.get("output_tokens", 0),
                        "reasoning": reasoning
                    })

                    if verdict == "MATCH" and confidence >= 0.90:
                        matched.append({
                            "bank_tx_id": b["bank_tx_id"],
                            "internal_ids": target_ids,
                            "customer_names": ", ".join(item["customer_name"] for item in combo),
                            "bank_amount": b["amount"],
                            "company_amount": round(combo_sum, 2),
                            "match_type": f"AI_BATCH_{k}TO1",
                            "confidence": confidence,
                            "structured_reasoning": structured_reasoning,
                            "reasoning": reasoning,
                            "source": adjudication_source
                        })
                        for item in combo:
                            if item["internal_id"] in unmatched_company:
                                del unmatched_company[item["internal_id"]]
                        found_batch = True
                        break

        if not found_batch:
            final_unresolved_bank.append(b)

    for b in final_unresolved_bank:
        exceptions.append({
            "record_id": b["bank_tx_id"],
            "source": "BANK",
            "amount": b["amount"],
            "category": "UNMAPPED_BANK_DEPOSIT",
            "confidence": 1.0,
            "reason": "Unmapped Bank Credit."
        })

    for c_id, c in unmatched_company.items():
        exceptions.append({
            "record_id": c["internal_id"],
            "source": "COMPANY",
            "amount": c["amount"],
            "category": "UNPAID_SALES_ORDER",
            "confidence": 1.0,
            "reason": "Unpaid Sales Order."
        })

    processing_time = round(time.time() - start_time, 2)
    money_reconciled = round(sum(m["bank_amount"] for m in matched), 2)
    money_at_risk = round(sum(e["amount"] for e in exceptions), 2)

    total_inp_tok = sum(log["input_tokens"] for log in audit_logs)
    total_out_tok = sum(log["output_tokens"] for log in audit_logs)
    total_tok = total_inp_tok + total_out_tok
    total_cost_usd = ((total_inp_tok / 1e6) * COST_PER_MILLION_INPUT) + ((total_out_tok / 1e6) * COST_PER_MILLION_OUTPUT)
    total_cost_inr = round(total_cost_usd * USD_TO_INR, 4)

    total_bank_records = len(bank_records)
    match_rate = round((len(matched) / total_bank_records) * 100, 1)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reconciliation_runs (run_id, timestamp, match_rate, money_reconciled, money_at_risk, total_matches, total_exceptions, total_tokens, total_cost_inr, processing_time_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, datetime.now(), f"{match_rate}%", money_reconciled, money_at_risk, len(matched), len(exceptions), total_tok, total_cost_inr, processing_time))

        for m in matched:
            cursor.execute("""
                INSERT INTO reconciled_items (run_id, bank_tx_id, internal_ids, bank_amount, company_amount, match_type, source, confidence, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, m["bank_tx_id"], json.dumps(m["internal_ids"]), m["bank_amount"], m["company_amount"], m["match_type"], m["source"], m["confidence"], m["reasoning"]))

        for e in exceptions:
            cursor.execute("""
                INSERT INTO exceptions (run_id, record_id, source, amount, category, confidence, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (run_id, e["record_id"], e["source"], e["amount"], e["category"], e["confidence"], e["reason"]))

        for log in audit_logs:
            cursor.execute("""
                INSERT INTO audit_logs (run_id, record_id, layer, target_ids, verdict, confidence, source, input_tokens, output_tokens, reasoning)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (run_id, log["record_id"], log["layer"], json.dumps(log["target_ids"]), log["verdict"], log["confidence"], log["source"], log["input_tokens"], log["output_tokens"], log["reasoning"]))
        conn.commit()

    return {
        "summary": {
            "run_id": run_id,
            "records_processed": len(bank_records) + len(company_records),
            "bank_records_processed": total_bank_records,
            "company_records_processed": len(company_records),
            "total_matches_resolved": len(matched),
            "money_reconciled_inr": f"₹{money_reconciled:,.2f}",
            "money_at_risk_inr": f"₹{money_at_risk:,.2f}",
            "exact_code_matches": len([m for m in matched if m["match_type"] == "EXACT_CODE"]),
            "ai_1to1_matches": len([m for m in matched if m["match_type"] == "AI_1TO1_ADJUDICATED"]),
            "ai_batch_matches": len([m for m in matched if "BATCH" in m["match_type"]]),
            "unresolved_exceptions": len(exceptions),
            "bank_settlement_match_rate": f"{match_rate}%",
            "total_tokens_consumed": total_tok,
            "total_reconciliation_cost_inr": f"₹{total_cost_inr:.4f}",
            "processing_time_sec": processing_time,
            "throughput_records_per_sec": round((len(bank_records) + len(company_records)) / processing_time, 1) if processing_time > 0 else 0
        },
        "matched": matched,
        "exceptions": exceptions,
        "audit_logs": audit_logs
    }