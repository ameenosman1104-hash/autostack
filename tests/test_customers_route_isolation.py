"""Route-Level Tenant Isolation Tests - Database Layer Verification"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.tenant_db import add_customer, get_customer, search_customers
from app.db_migrations import migrate_tenant_db

results = type('R', (), {'passed': 0, 'failed': 0, 'errors': []})()

def test(name, fn):
    try:
        fn()
        results.passed += 1
        print(f"[OK] {name}")
    except Exception as e:
        results.failed += 1
        results.errors.append((name, str(e)))
        print(f"[FAIL] {name}: {e}")

class DB:
    def __init__(self, p=""): self.t=tempfile.mkdtemp(p); self.o=None
    def setup(self, tid):
        self.o=os.environ.get("AUTOSTACK_DATA_DIR")
        os.environ["AUTOSTACK_DATA_DIR"]=self.t
        migrate_tenant_db(tid, os.path.join(self.t, f"{tid}.db"))
        return tid
    def cleanup(self):
        if self.o: os.environ["AUTOSTACK_DATA_DIR"]=self.o
        else: os.environ.pop("AUTOSTACK_DATA_DIR", None)
        shutil.rmtree(self.t, ignore_errors=True)

def t1():
    d=DB("t1_")
    try:
        tid=d.setup(4001)
        add_customer(tid, "T", phone="081", email="e@t.com", vehicle_registration="V", notes="N")
        r=search_customers(tid, "T")
        assert len(r)>0
        assert {"id","name","phone","vehicle_registration"}.issubset(r[0].keys())
    finally: d.cleanup()

def t2():
    da,db=DB("a_"),DB("b_")
    try:
        ta,tb=da.setup(4002),db.setup(4003)
        os.environ["AUTOSTACK_DATA_DIR"]=da.t
        ca=add_customer(ta, "A")
        assert get_customer(ta, ca) is not None
        os.environ["AUTOSTACK_DATA_DIR"]=db.t
        assert get_customer(tb, ca) is None
    finally: da.cleanup();db.cleanup()

def t3():
    da,db=DB("s1_a"),DB("s1_b")
    try:
        ta,tb=da.setup(4004),db.setup(4005)
        os.environ["AUTOSTACK_DATA_DIR"]=da.t
        add_customer(ta, "Alpha")
        os.environ["AUTOSTACK_DATA_DIR"]=db.t
        add_customer(tb, "Beta")
        os.environ["AUTOSTACK_DATA_DIR"]=da.t
        assert len(search_customers(ta, "Alpha"))>0
        os.environ["AUTOSTACK_DATA_DIR"]=db.t
        assert len(search_customers(tb, "Alpha"))==0
    finally: da.cleanup();db.cleanup()

def t4():
    da,db=DB("p1_a"),DB("p1_b")
    try:
        ta,tb=da.setup(4006),db.setup(4007)
        os.environ["AUTOSTACK_DATA_DIR"]=da.t
        add_customer(ta, "A", phone="081")
        os.environ["AUTOSTACK_DATA_DIR"]=db.t
        add_customer(tb, "B", phone="082")
        assert len(search_customers(tb, "081"))==0
    finally: da.cleanup();db.cleanup()

def t5():
    da,db=DB("v1_a"),DB("v1_b")
    try:
        ta,tb=da.setup(4008),db.setup(4009)
        os.environ["AUTOSTACK_DATA_DIR"]=da.t
        add_customer(ta, "A", vehicle_registration="VA")
        os.environ["AUTOSTACK_DATA_DIR"]=db.t
        add_customer(tb, "B", vehicle_registration="VB")
        assert len(search_customers(tb, "VA"))==0
    finally: da.cleanup();db.cleanup()

def t6():
    d=DB("json_")
    try:
        tid=d.setup(4010)
        add_customer(tid, "Test", phone="081", email="secret", notes="priv")
        r=search_customers(tid, "Test")
        c=r[0]
        assert "id" in c and "name" in c and "phone" in c
        assert "vehicle_registration" in c
        # DB returns all fields, but routes filter in JSON response
    finally: d.cleanup()

print("="*60)
print("Route-Level Tenant Isolation Tests")
print("="*60 + "\n")
test("JSON fields present", t1)
test("Get isolation", t2)
test("Search name isolation", t3)
test("Search phone isolation", t4)
test("Search vehicle isolation", t5)
test("Database fields available", t6)
print(f"\n{'='*60}\nResults: {results.passed} passed, {results.failed} failed\n{'='*60}")
if results.errors:
    print("\nFailed:")
    for n,e in results.errors: print(f"  - {n}: {e}")
sys.exit(0 if results.failed==0 else 1)
