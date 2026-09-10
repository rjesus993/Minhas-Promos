#!/usr/bin/env python3
"""Restore a backup to the live database.

Usage: python scripts/restore_backup.py <path_to_backup.db> [--dest ./data/minhas_promo.db]
"""
import argparse
import sys
import os

from db import SQLiteDatabase

def main():
    parser = argparse.ArgumentParser(description="Restore a backup to the live database")
    parser.add_argument("backup_path", help="Path to the backup file (.db or .db.gz)")
    parser.add_argument("--dest", default="./data/minhas_promo.db", help="Destination database path")
    args = parser.parse_args()

    backup = args.backup_path
    dest = args.dest
    if not os.path.exists(backup):
        print(f"Error: Backup file not found: {backup}")
        return 1

    # Verify backup integrity
    if not SQLiteDatabase._verify_backup_file(backup):
        print("Error: Backup integrity check failed.")
        return 1

    # Perform restore with safety copy
    try:
        safety = SQLiteDatabase.restore_from_backup(backup, dest, safety_copy=True)
    except Exception as e:
        print(f"Error during restore: {e}")
        return 1

    print("Restore completed successfully.")
    if safety:
        print(f"Safety copy of the previous database created at: {safety}")
    print("Please restart the bot to use the restored database.")
    return 0

if __name__ == "__main__":
    sys.exit(main())