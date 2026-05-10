import asyncio
from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.database.supabase_client import LotteryDB

async def query():
    db = LotteryDB()
    today = "2026-05-10"
    
    print("=== MODEL PREDICTIONS (SUB-MODELS) ===")
    res_models = db.supabase.table("model_predictions").select("*").eq("prediction_date", today).execute()
    for row in res_models.data:
        print(f"[{row['region']} - {row['province'] or 'GLOBAL'}] Model: {row['model_name']}")
        print(f"  Top 5 Pairs: {row['pair_1']}, {row['pair_2']}, {row['pair_3']}, {row['pair_4']}, {row['pair_5']}")
        print(f"  Scores:      {row['score_1']}, {row['score_2']}, {row['score_3']}, {row['score_4']}, {row['score_5']}")
        print("-" * 40)
        
    print("\n=== FINAL ENSEMBLE RESULTS ===")
    res_final = db.supabase.table("prediction_results").select("*").eq("prediction_date", today).execute()
    for row in res_final.data:
        if row.get("ensemble_method"):
            print(f"[{row['region']} - {row['province'] or 'GLOBAL'}]")
            print(f"  Top 3 VIP: {row['pair_1']}, {row['pair_2']}, {row['pair_3']}")
            print(f"  Final Scores: {row.get('final_scores')}")
            print(f"  Contributing Models: {row.get('contributing_models')}")
            print("-" * 40)

asyncio.run(query())
