#!/usr/bin/env python3
"""One-time migration from Supabase to the local SQLite database.

Usage:
  python scripts/import_supabase_to_sqlite.py --dry-run
  python scripts/import_supabase_to_sqlite.py --overwrite
"""
import argparse
import os
import sys

from dotenv import load_dotenv
from db import SQLiteDatabase, get_db

load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="Import Supabase terms/history into SQLite")
    parser.add_argument("--dry-run", action="store_true", help="Only print counts (default)")
    parser.add_argument("--overwrite", action="store_true", help="Actually insert rows into SQLite")
    args = parser.parse_args()

    if not args.overwrite:
        print("Dry run only. No rows will be inserted.")

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("Error: SUPABASE_URL and SUPABASE_KEY are required.")
        return 1

    try:
        import supabase
    except Exception as e:
        print(f"Error: supabase client is not installed. Install with: pip install -r requirements_migration_only.txt")
        return 1

    client = supabase.create_client(url, key)

    try:
        terms = client.table("terms").select("term").execute().data
        histories = client.table("term_history").select("term", "action", "timestamp").execute().data
    except Exception as e:
        print(f"Error reading Supabase tables: {e}")
        return 1

    print(f"Supabase terms count: {len(terms)}")
    print(f"Supabase history count: {len(histories)}")

    db = get_db()
    if not args.overwrite and (db.terms_count() or len(histories)):
        print("Destination has existing data. Use --overwrite to proceed.")
        return 0

    inserted_terms = 0
    inserted_history = 0
    if args.overwrite:
        for row in terms:
            term = row.get("term")
            if term:
                if db.add_term(str(term)):
                    inserted_terms += 1
                else:
                    print(f"Failed to insert term: {term}")
        for row in histories:
            term = row.get("term")
            action = row.get("action")
            if term and action:
                # history timestamp is ignored here; the SQLite table stores its own timestamp
                try:
                    with db._conn:
                        db._conn.execute(
                            "INSERT INTO term_history (term, action) VALUES (?, ?)",
                            (str(term), str(action)),
                        )
                    inserted_history += 1
                except Exception as e:
                    print(f"Failed to insert history: {term} {action} {e}")

    print("Import summary:")
    print(f"  Terms inserted: {inserted_terms}")
    print(f"  History rows inserted: {inserted_history}")
    return 0

if __name__ == "__main__":
    sys.exit(main())