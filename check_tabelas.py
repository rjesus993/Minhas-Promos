from db import get_db
db = get_db()
rows = db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print([r[0] for r in rows])
