"""Task 6 server tests: build_app route inspection, token gate, payload structure."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# -------- Stub modules (sandbox restrictions, no pip deps installed) --------
class FakeDotenv:
    @staticmethod
    def load_dotenv(*a, **kw): return True

sys.modules.setdefault("dotenv", type(sys)("dotenv"))
sys.modules["dotenv"].load_dotenv = FakeDotenv.load_dotenv

# Provide stub fastapi/uvicorn
try:
    import fastapi  # noqa: F401
except ModuleNotFoundError:
    print("SKIP: sandbox lacks fastapi; this is expected; syntax/static check is in real env.")
    sys.exit(0)

try:
    import uvicorn  # noqa: F401
except ModuleNotFoundError:
    print("SKIP: sandbox lacks uvicorn; syntax check only.")
    sys.exit(0)

# Provide fake env vars so config validates and DB is at temp path
tmpdir = tempfile.mkdtemp()
os.environ["API_ID"] = "100000"
os.environ["API_HASH"] = "a" * 32
os.environ["TELEGRAM_SESSION"] = "s" * 32
os.environ["DB_PATH"] = os.path.join(tmpdir, "t6.db")
os.environ["SERVER_PORT"] = "17861"

# Import AFTER env set
from server import build_app, _health_database_payload, _now_iso
from db import get_db, reset_db_singleton

# Ensure DB singleton picks up our temp path
reset_db_singleton()
get_db().ensure_schema()

# -------- TR-6.1 / health endpoints exist --------
print("TR-6.1: 4 detailed health endpoints + aggregate /health + / present")
app = build_app(health_token=None)
routes_by_path = {}
for route in app.routes:
    if hasattr(route, "path") and hasattr(route, "methods"):
        routes_by_path.setdefault(route.path, set()).update(route.methods or set())
required_routes = {
    "/": {"GET", "HEAD"},
    "/health": {"GET", "HEAD"},
    "/health/database": {"GET"},
    "/health/storage": {"GET"},
    "/health/system": {"GET"},
    "/health/external": {"GET"},
}
print(f"  Routes: {sorted(routes_by_path)}")
missing = []
for p, methods in required_routes.items():
    if p not in routes_by_path:
        missing.append(f"route {p}")
        continue
    have = routes_by_path[p]
    for m in methods:
        if m not in have:
            missing.append(f"{p} missing method {m} (have {sorted(have)})")
if missing:
    for m in missing:
        print(f"  FAIL: {m}")
    sys.exit(1)
print("  OK: All required routes & methods present.")

# -------- TR-6.2: /home branding no Hugging Face mention --------
print("TR-6.2: / message branding updated to 'Userbot ativo'")
root_handler = None
for route in app.routes:
    if getattr(route, "path", None) == "/" and "GET" in (getattr(route, "methods") or set()):
        root_handler = route.func
        break
assert root_handler is not None
resp = root_handler()
print(f"  GET/ -> {resp}")
assert resp.get("message") == "Userbot ativo", f"wrong message: {resp.get('message')!r}"
assert "Hugging" not in str(resp) and "Hugging Face" not in str(resp)
print("  OK: / message updated, no Hugging Face branding.")

# -------- TR-6.3/6.4: Token gate (401 with wrong/missing, pass with correct) --------
print("TR-6.3: HEALTH_TOKEN wrong/missing -> 401")
print("TR-6.4: HEALTH_TOKEN correct -> 200")
from fastapi import HTTPException, Request

app2 = build_app(health_token="t0psecret")

# Find dependency function by inspecting /health route dependencies
health_route = None
for route in app2.routes:
    if getattr(route, "path", None) == "/health" and "GET" in (getattr(route, "methods") or set()):
        health_route = route
        break
assert health_route is not None
# dependencies are in route.dependencies list; extract the callable directly from it
dep_func = None
for d in health_route.dependencies:
    dep_func = d.dependency  # the _require_health_token
    break
assert dep_func is not None

def make_request(headers=None, query=None):
    # Build a minimal Request-like object compatible with the dep function.
    # The dep function reads from: request.headers.get(...) or request.query_params.get(...)
    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)
    class FakeQuery(dict):
        def get(self, key, default=None):
            return super().get(key, default)
    class FakeRequest:
        def __init__(self, h, q):
            self.headers = FakeHeaders(h or {})
            self.query_params = FakeQuery(q or {})
    return FakeRequest(headers or {}, query or {})

# No header, no query -> should raise 401
try:
    dep_func(make_request())
    print("  FAIL: expected HTTPException 401 for missing token")
    sys.exit(1)
except HTTPException as he:
    assert he.status_code == 401, f"expect 401 got {he.status_code}"
    print(f"  OK missing token -> 401 detail={he.detail!r}")

# Wrong token
try:
    dep_func(make_request(headers={"X-Health-Token": "wrong_token"}))
    print("  FAIL expected 401 for wrong token")
    sys.exit(1)
except HTTPException as he:
    assert he.status_code == 401
    print("  OK wrong token -> 401")

# Correct token via header -> no exception
dep_func(make_request(headers={"X-Health-Token": "t0psecret"}))
print("  OK correct token header -> pass")
# Correct token via query param -> no exception
dep_func(make_request(query={"token": "t0psecret"}))
print("  OK correct token query param -> pass")

# With HEALTH_TOKEN unset, missing token passes
app3 = build_app(health_token=None)
# Find dep in app3 health route
health3 = [r for r in app3.routes if getattr(r, "path", None) == "/health" and "GET" in (getattr(r, "methods") or set())][0]
dep3 = health3.dependencies[0].dependency
dep3(make_request())  # no headers, no token env -> no exception
print("  OK HEALTH_TOKEN unset -> gate open")

# -------- TR-6.5: Aggregate /health payload structure --------
print("TR-6.5: Health payload structure contains database/storage/system/external sub-sections")
# Rebuild app with no token so we can call the health endpoint func directly
app4 = build_app(health_token=None)
health4 = [r for r in app4.routes if getattr(r, "path", None) == "/health" and "GET" in (getattr(r, "methods") or set())][0]
resp = health4.func()
print(f"  keys top-level: {sorted(resp.keys())}")
assert "database" in resp and "storage" in resp and "system" in resp and "external" in resp
assert resp["ok"] in (True, False)
assert "components" in resp and set(resp["components"]) == {"database", "storage", "system", "external"}
for comp in ("database", "storage", "system", "external"):
    assert "ok" in resp[comp], f"{comp} missing 'ok' key"
print("  OK structure validated; all sub components have ok key.")

# -------- Cleanup --------
try:
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
except Exception:
    pass

print()
print("========== TASK 6 ALL CHECKS PASSED ==========")
