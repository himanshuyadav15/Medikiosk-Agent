"""
MediKiosk — Summary Viewer
Run anytime: python view_summaries.py
"""
import io
import sys
import db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def main():
    summaries = db.get_all_summaries()
    print("=" * 60)
    print(f"  MediKiosk — Stored Clinical Summaries (Total: {len(summaries)})")
    print(f"  Database File: medikiosk.db")
    print("=" * 60)

    if not summaries:
        print("\nNo summaries saved yet.")
        return

    for s in summaries:
        print(f"\n[ID #{s['id']}] Patient: {s['patient_name']} | Source: {s['source']} | Date: {s['created_at']}")
        print("-" * 60)
        print(s["summary_text"])
        print("-" * 60)


if __name__ == "__main__":
    main()
