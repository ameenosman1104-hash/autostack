# Phase D: Tyre Shop Point of Sale (POS) — Implementation Plan

## Executive Summary

Phase D introduces a fast, simple cashier-facing Point of Sale system for tyre shops. The POS connects safely to the Phase C `complete_sale()` transaction engine and does NOT duplicate transaction logic.

**Key principle:** POS is the UI; complete_sale() is the transaction engine.

---

## 1. Existing UI/Navigation Architecture

**Current structure:**
- Fixed navbar (dark, 50px height on desktop)
- Left sidebar (200px, desktop only) with navigation links
- Main content area with responsive layout
- Mobile-first design (sidebar hidden on tablets/phones)
- Styling: Bootstrap 5.3, custom CSS

**Current navigation items:**
- Dashboard (endpoint: `dashboard.index`)
- Inventory (endpoint: `inventory.index`)
- Purchase Orders (endpoint: `po.index`)
- Customers (endpoint: `customers.index`)
- Debtors (endpoint: `debtors.index`)
- Reports (endpoint: `reports.index`)
- Notifications (endpoint: `notifications.index`)
- Settings (endpoint: `settings.index`)
- Help (endpoint: `help.index`)

**Design consistency:**
- Color scheme: dark navy (#1E293B), slate gray accents, blue highlights (#3B82F6)
- Font: Segoe UI, Arial
- Components: stat cards, tables, badges, buttons
- Icons: Bootstrap Icons 1.11.0

**Recommendation:** Add POS as a top-level nav item: "POS" (endpoint: `pos.index`)

---

## 2. Existing Product Schema & Search Capabilities

**Products table structure:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
code TEXT UNIQUE NOT NULL          ← unique product identifier
name TEXT NOT NULL                 ← product name/description
category TEXT DEFAULT ''           ← category (e.g., "Tyres", "Services")
unit TEXT DEFAULT 'PCS'            ← unit of measure
current_stock REAL DEFAULT 0       ← live inventory quantity
reorder_level REAL DEFAULT 0       ← low-stock threshold
min_level_manual INTEGER DEFAULT 0 ← manual min level flag
max_stock REAL DEFAULT 0           ← maximum stock level
last_cost_price REAL DEFAULT 0     ← cost to business
previous_cost_price REAL DEFAULT 0 ← historical cost
supplier TEXT DEFAULT ''           ← supplier name
condition TEXT DEFAULT NULL        ← 'new'|'used' (Migration 15)
brand TEXT DEFAULT ''              ← brand/manufacturer (Migration 15)
tyre_size TEXT DEFAULT ''          ← tyre size e.g., "205/55R16" (Migration 15)
created_at TEXT DEFAULT (now)
updated_at TEXT DEFAULT (now)
extra_data TEXT DEFAULT '{}'       ← JSON for additional data
deleted INTEGER DEFAULT 0          ← soft delete flag
deleted_at TEXT DEFAULT NULL
```

**Existing search function:**
```python
get_all_products(tid, search="")
  searches by: name LIKE, code LIKE, category LIKE
  excludes: deleted products
  returns: sorted by name
```

**Extended search capability needed for POS:**
- Search by tyre size (e.g., "205/55R16")
- Search by brand (e.g., "Michelin")
- Search by product code (e.g., "MICH-PRI-001")
- Combined search (one box finds any of the above)

**Recommendation:** Enhance get_all_products() or create pos_search_products() that searches:
```
WHERE (deleted=0 OR deleted IS NULL)
AND (
  name LIKE ?
  OR code LIKE ?
  OR category LIKE ?
  OR brand LIKE ?
  OR tyre_size LIKE ?
)
```

**Storage of selling_price:**
- Currently: `extra_data["selling_price"]` (JSON field)
- Phase C uses: `json.loads(product['extra_data']).get('selling_price')`
- **Issue:** No built-in validation that selling_price exists before sale

---

## 3. Existing Customer Search Capabilities

**Customer search function exists:**
```python
search_customers(tid, query)
  searches by: name LIKE, phone LIKE, vehicle_registration LIKE
  returns: [dict(r) for r in rows]
```

**POS customer workflow:**
1. User selects "Walk-in" OR searches for customer
2. Search box accepts: name, phone, vehicle registration
3. Results show: name, phone, vehicle registration, outstanding balance
4. User selects a customer OR proceeds as walk-in

**Recommendation:** Reuse existing search_customers() directly in POS.

**Credit sale requirement:**
- If payment_method = 'credit': customer_id REQUIRED
- Walk-in customer (NULL customer_id) cannot buy on credit
- Backend complete_sale() enforces this (UI also prevents at cashier level)

---

## 4. Existing Services Capabilities

**Services table:**
```
id INTEGER PRIMARY KEY AUTOINCREMENT
name TEXT NOT NULL                 ← service name (e.g., "Fitting")
description TEXT DEFAULT ''
default_price REAL DEFAULT 0       ← authoritative service price
category TEXT DEFAULT ''           ← category (e.g., "Labour")
is_active INTEGER DEFAULT 1        ← availability flag
created_at TEXT DEFAULT (now)
updated_at TEXT DEFAULT (now)
```

**Existing functions:**
```python
get_all_services(tid, active_only=True)
  returns: active services sorted by name
```

**POS services display:**
- Show available services with default_price
- Cashier can add multiple services to same sale
- Service quantities (e.g., "4 tyres × fitting = R80 × 4")

**Recommendation:** Create pos_get_services() which wraps get_all_services():
```python
def pos_get_services(tid):
  return get_all_services(tid, active_only=True)
```

---

## 5. Recommended POS Screen Layout

```
┌─────────────────────────────────────────────────────────────┐
│ AutoStack                                    NEW SALE ✕     │ ← Navbar
├──────────────────────────────────────────────────────────────┤
│                                                               │
│ CUSTOMER SECTION                                             │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ [ Walk-in ▼ ] [ Search customer... ]                     │ │
│ │                                                            │ │
│ │ Selected: Walk-in (or "John Doe")                         │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                               │
│ PRODUCT SEARCH                                               │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ [ Search tyres: 205/55R16... ] [ 🔍 ]                   │ │
│ │                                                            │ │
│ │ SEARCH RESULTS (Live as user types)                       │ │
│ │                                                            │ │
│ │ 205/55R16 | Michelin Primacy 4 | New | 8 stock | R1,500  │ │
│ │                                                [ ADD ]     │ │
│ │                                                            │ │
│ │ 205/55R16 | Continental UltraContact | New | 4 stock ... │ │
│ │                                                [ ADD ]     │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                               │
│ SERVICES                                                     │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ [ + Fitting (R80) ] [ + Balancing (R70) ]                │ │
│ │ [ + Wheel Alignment (R250) ]                              │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                               │
│ SALE BASKET                                                  │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ Item              Qty   Unit    Line Total  Actions      │ │
│ ├──────────────────────────────────────────────────────────┤ │
│ │ Michelin Primacy  4  × R1,500 = R6,000    [−] [✕]      │ │
│ │ Fitting           4  × R80    = R320      [−] [✕]      │ │
│ │ Balancing         4  × R70    = R280      [−] [✕]      │ │
│ ├──────────────────────────────────────────────────────────┤ │
│ │                                     Subtotal  R6,600      │ │
│ │                                     Discount  R    0      │ │
│ │                                     TOTAL     R6,600      │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                               │
│ PAYMENT                                                      │
│ ┌──────────────────────────────────────────────────────────┐ │
│ │ [ CASH ] [ CARD ] [ EFT ] [ CREDIT ]                     │ │
│ │                                                            │ │
│ │ [ COMPLETE SALE ]                    [ DISCARD ]         │ │
│ └──────────────────────────────────────────────────────────┘ │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

---

## 6. Exact Cashier Workflow

### New Sale:
1. Click "NEW SALE" button or nav link
2. **Customer section:** Choose walk-in OR search for existing customer
3. **Product search:** Type tyre size/brand/code, see results in real-time
4. **Add to basket:** Click [ADD] on product, optionally adjust quantity (dropdown)
5. **Services (optional):** Click [+ Fitting], [+ Balancing], etc.
6. **Review basket:** See all items, quantities, prices
7. **Adjust (optional):** Click [−] to decrease qty, [✕] to remove
8. **Payment method:** Select CASH, CARD, EFT, or CREDIT
9. **Complete:** Click [COMPLETE SALE] button
10. **Confirmation:** Show success message with invoice number, total, payment method

### If credit is selected and customer is walk-in:
- Show error: "Please select a customer for a credit sale"
- Keep basket intact, allow retry

### After successful sale:
- Show: "SALE COMPLETE ✓ | INV-000123 | R6,600.00 | CARD"
- Clear basket, reset to blank state
- Ready for next sale

---

## 7. Exact Routes Required

**New routes to create:**

### GET `/pos/`
- Render POS page (authenticated, tenant-scoped)
- Serve: customer dropdown, product/service search dropdowns, empty basket
- Idempotency key generated on page load (stored in JS)

### GET `/pos/products/search?q=<query>`
- Accept: search query string
- Return: JSON list of products matching query
- Fields: id, code, name, brand, tyre_size, condition, current_stock, selling_price (from extra_data)
- Filter: exclude deleted, exclude zero stock (optional)
- Tenant-scoped: WHERE tenant_id = current_user.tenant_id (via get_all_products)

### GET `/pos/services`
- Return: JSON list of active services
- Fields: id, name, default_price
- Tenant-scoped

### GET `/pos/customers/search?q=<query>`
- Reuse: existing `search_customers()` function
- Return: JSON list [id, name, phone, vehicle_registration]
- Tenant-scoped

### POST `/pos/complete`
- Accept JSON:
  ```json
  {
    "invoice_number": "INV-000001",
    "idempotency_key": "uuid-...",
    "items": [
      {"type": "product", "id": 123, "quantity": 4},
      {"type": "service", "id": 5, "quantity": 4}
    ],
    "customer_id": null,           // or customer ID for credit
    "payment_method": "cash",      // cash|card|eft|credit
    "discount": 0,
    "notes": ""
  }
  ```
- Call: `complete_sale(tid, invoice_number, idempotency_key, items, customer_id, payment_method, discount=discount)`
- Return JSON:
  ```json
  {
    "success": true/false,
    "duplicate": true/false,
    "invoice_id": 123,
    "invoice_number": "INV-000001",
    "total": 6600.00,
    "debtor_id": null,             // or debtor ID if credit
    "payment_status": "paid",      // paid|unpaid
    "error": "message" (if success=false)
  }
  ```

---

## 8. Request/Response Structure

### Product Search Response (GET /pos/products/search?q=205)
```json
[
  {
    "id": 1,
    "code": "MICH-PRI-205-55R16",
    "name": "Michelin Primacy 4 205/55R16",
    "brand": "Michelin",
    "tyre_size": "205/55R16",
    "condition": "new",
    "current_stock": 8,
    "selling_price": 1500.00
  },
  {
    "id": 2,
    "code": "CONT-ULTRA-205-55R16",
    "name": "Continental UltraContact 205/55R16",
    "brand": "Continental",
    "tyre_size": "205/55R16",
    "condition": "new",
    "current_stock": 4,
    "selling_price": 1650.00
  }
]
```

### Services Response (GET /pos/services)
```json
[
  {"id": 1, "name": "Fitting", "default_price": 80.00},
  {"id": 2, "name": "Balancing", "default_price": 70.00},
  {"id": 3, "name": "Wheel Alignment", "default_price": 250.00}
]
```

### Customer Search Response (GET /pos/customers/search?q=john)
```json
[
  {"id": 1, "name": "John Doe", "phone": "081234567", "vehicle_registration": "ABC123"}
]
```

### Complete Sale Request (POST /pos/complete)
```json
{
  "invoice_number": "INV-000001",
  "idempotency_key": "550e8400-e29b-41d4-a716-446655440000",
  "items": [
    {"type": "product", "id": 1, "quantity": 4},
    {"type": "service", "id": 1, "quantity": 4}
  ],
  "customer_id": null,
  "payment_method": "cash",
  "discount": 0,
  "notes": ""
}
```

### Complete Sale Response (Success)
```json
{
  "success": true,
  "duplicate": false,
  "invoice_id": 123,
  "invoice_number": "INV-000001",
  "total": 6600.00,
  "debtor_id": null,
  "payment_status": "paid"
}
```

### Complete Sale Response (Error)
```json
{
  "success": false,
  "error": "Insufficient stock for product 'Michelin Primacy 4': have 3, need 4",
  "error_type": "validation"
}
```

---

## 9. How POST /pos/complete Calls complete_sale()

**Backend route logic (pseudo-code):**

```python
@pos_bp.route("/complete", methods=["POST"])
@login_required
def complete_sale_route():
    tid = current_user.tenant_id
    data = request.get_json()
    
    try:
        # Validate required fields
        if not data.get('invoice_number'):
            return jsonify({'success': False, 'error': 'invoice_number required'}), 400
        if not data.get('idempotency_key'):
            return jsonify({'success': False, 'error': 'idempotency_key required'}), 400
        if not data.get('items'):
            return jsonify({'success': False, 'error': 'items required'}), 400
        
        # Call Phase C transaction engine
        result = complete_sale(
            tid=tid,
            invoice_number=data['invoice_number'],
            idempotency_key=data['idempotency_key'],
            items=data['items'],
            customer_id=data.get('customer_id'),
            payment_method=data.get('payment_method', 'cash'),
            discount=float(data.get('discount', 0)),
            notes=data.get('notes', ''),
            created_by=current_user.id
        )
        
        # complete_sale() handles all transaction logic atomically
        # POS only returns its result
        return jsonify(result), 200
    
    except ValueError as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': 'validation'
        }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Database error processing sale',
            'error_type': 'system'
        }), 500
```

**Key principle:** POS route is thin wrapper. All logic is in complete_sale().

---

## 10. Idempotency Key Lifecycle

**Client-side (JavaScript):**

```javascript
// On POS page load
let currentSaleIdempotencyKey = generateUUID();

// User adds items, selects payment, clicks COMPLETE SALE
async function completeSale() {
  const saleData = {
    invoice_number: generateInvoiceNumber(), // or request from server
    idempotency_key: currentSaleIdempotencyKey,
    items: currentBasket,
    customer_id: selectedCustomerId,
    payment_method: selectedPaymentMethod,
    discount: currentDiscount,
    notes: currentNotes
  };
  
  disableCompleteButton(); // Prevent double-click
  
  try {
    const response = await fetch('/pos/complete', {
      method: 'POST',
      body: JSON.stringify(saleData)
    });
    
    const result = await response.json();
    
    if (result.success) {
      showConfirmation(result);     // Show success
      clearBasket();                 // Clear items
      currentSaleIdempotencyKey = generateUUID(); // NEW KEY for next sale
    } else {
      showError(result.error);
      enableCompleteButton();        // Allow retry with same key
    }
  } catch (error) {
    showNetworkError();
    enableCompleteButton();          // Allow retry with same key
  }
}
```

**Server-side behavior:**
- First request with idempotency_key "uuid-1": creates invoice, items, stock deduction, debtor
- Network fails, browser retries: POST with same "uuid-1"
- Server finds existing invoice with "uuid-1": returns `duplicate: true` + original result
- NO duplicate stock deduction, NO duplicate debtor, NO duplicate items
- Cashier sees same success message as before

**Idempotency key lifecycle summary:**
1. **Page load:** Generate new key for current sale
2. **Add/edit items:** Reuse same key
3. **Complete sale (success):** Generate NEW key for next sale
4. **Complete sale (failure):** Reuse same key for retry
5. **Network retry:** Reuse same key (backend detects duplicate)

---

## 11. Automatic Invoice Number Strategy

**Recommendation: Backend-generated invoice numbers**

**Strategy:**
- Invoice numbers are sequential with prefix: `INV-000001`, `INV-000002`, etc.
- Generate on demand in complete_sale() or dedicated helper
- Ensure uniqueness even under concurrency

**Implementation options:**

### Option A: Database sequence (recommended)
```python
def generate_invoice_number(tid):
    # Use a sequences table
    # Atomic increment within transaction
    # Guarantees uniqueness
    pass
```

**Pros:**
- Atomic, concurrency-safe
- Unique even if multiple sales simultaneous
- Simple for staff (sequential, predictable)

**Cons:**
- Requires new table/logic
- Slightly slower (extra query)

### Option B: Timestamp-based
```python
def generate_invoice_number(tid):
    timestamp = int(time.time() * 1000000)
    return f"INV-{timestamp}"
    # Example: INV-1695329401234567
```

**Pros:**
- No database lookup
- Unique (timestamp + microseconds + tenant)
- Works well with SQLite

**Cons:**
- Less human-readable
- Harder for staff to reference

### Recommendation:
**Option A with sequences table**

**Schema addition:**
```sql
CREATE TABLE invoice_number_sequences (
  tid INTEGER PRIMARY KEY,
  next_number INTEGER DEFAULT 1,
  prefix TEXT DEFAULT 'INV',
  created_at TEXT DEFAULT (now)
);
```

**Function:**
```python
def generate_invoice_number(tid):
    conn = get_conn(tid)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT next_number, prefix FROM invoice_number_sequences WHERE tid = ?",
            (tid,)
        ).fetchone()
        
        if not row:
            # First invoice for this tenant
            conn.execute(
                "INSERT INTO invoice_number_sequences (tid, prefix, next_number) VALUES (?, ?, ?)",
                (tid, 'INV', 1)
            )
            next_num = 1
            prefix = 'INV'
        else:
            next_num = row[0]
            prefix = row[1]
        
        # Increment for next time
        conn.execute(
            "UPDATE invoice_number_sequences SET next_number = ? WHERE tid = ?",
            (next_num + 1, tid)
        )
        
        conn.commit()
        return f"{prefix}-{next_num:06d}"  # INV-000001
    finally:
        conn.close()
```

**Where to generate:**
- **Best:** Inside complete_sale() before creating invoice (atomic with transaction)
- Alternative: POST /pos/complete route generates and passes it
- Both work; atomic is safer (no gap between generation and use)

**For Phase D:**
- If implementation time is tight: use timestamp-based (simpler, sufficient for MVP)
- If time permits: implement sequences table (proper solution)

---

## 12. Recommendation: products.selling_price Column

### Current State:
- Selling price stored in: `products.extra_data["selling_price"]` (JSON)
- Phase C reads: `json.loads(extra_data).get('selling_price')`
- **Problem:** No validation that price exists; no direct column query

### Recommendation: Add `products.selling_price` column

**Rationale:**
- Explicit schema (not hidden in JSON)
- Faster queries (no JSON parsing)
- Validation: selling_price IS NULL means price not configured
- Clear to staff: "This product has no selling price set"

**Schema change:**
```sql
ALTER TABLE products ADD COLUMN selling_price REAL DEFAULT NULL;
```

**Semantics:**
- `NULL` = price not configured (product cannot be sold until configured)
- `0.00` or higher = valid selling price

**This requires:**
1. New migration (Migration 17... wait, we removed that. Would be Migration 17 again, but let's call it 18)
2. Data migration: copy existing prices from extra_data to selling_price column
3. Update complete_sale() to read products.selling_price (with fallback to extra_data for compat)
4. Product import/management UI updates
5. Product add/edit forms: ask for selling_price

### Phase D approach (choose one):

**Option A: Full column + migration (recommended)**
- Add Migration 18: `ALTER TABLE products ADD COLUMN selling_price REAL DEFAULT NULL`
- Backfill existing extra_data values to column
- Update complete_sale() to read column first
- Update product forms to set column
- Deprecate extra_data["selling_price"]

**Option B: Hybrid (faster for Phase D MVP)**
- Do NOT add migration yet
- Update complete_sale() to read column if exists, else extra_data (graceful fallback)
- Update product forms to set BOTH column AND extra_data
- Add migration later (Phase E)

**Option C: Keep extra_data (no changes)**
- Accept temporary solution
- Migrate later
- Risk: tech debt accumulates

### Recommendation: **Option A (full solution)**

**Why:**
- Proper schema design
- Safer for cashier (NULL = not priced)
- Queries faster
- Phase C already working; we can update complete_sale() safely
- Backfill can be scripted

**Migration path:**
1. Create Migration 17 (yes, 17 again, since 17 was removed):
   ```sql
   ALTER TABLE products ADD COLUMN selling_price REAL DEFAULT NULL;
   ```

2. Write backfill script (runs after migration):
   ```python
   def backfill_selling_prices(tid):
       conn = get_conn(tid)
       products = conn.execute("SELECT id, extra_data FROM products").fetchall()
       
       for prod_id, extra_data_json in products:
           try:
               extra_data = json.loads(extra_data_json or '{}')
               selling_price = extra_data.get('selling_price')
               if selling_price is not None:
                   conn.execute(
                       "UPDATE products SET selling_price = ? WHERE id = ?",
                       (float(selling_price), prod_id)
                   )
           except:
               pass  # Skip malformed JSON
       
       conn.commit()
   ```

3. Update complete_sale() in Phase D:
   ```python
   # Read from column (authoritative)
   selling_price = product['selling_price']
   
   if selling_price is None:
       raise ValueError(f"Product '{product['name']}' has no configured selling price")
   ```

4. Update product forms (Phase D):
   - Add input field: "Selling Price (R)"
   - Set both column AND extra_data (for transition period)

---

## 13. Migration Plan (If selling_price column is adopted)

**Migration 17 (formerly removed, now re-added):**
```python
if get_schema_version(conn) < 17:
    conn.execute("BEGIN")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(products)").fetchall()}
        
        if "selling_price" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN selling_price REAL DEFAULT NULL")
        
        mark_migration_applied(conn, 17, "Add selling_price column to products")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Migration 17 failed: {e}")
```

**Backfill (one-time, safe to run multiple times):**
```python
def backfill_selling_prices():
    for tid in get_all_tenants():  # new helper
        conn = get_conn(tid)
        try:
            products = conn.execute("SELECT id, extra_data FROM products WHERE selling_price IS NULL").fetchall()
            
            for prod_id, extra_data_json in products:
                try:
                    extra_data = json.loads(extra_data_json or '{}')
                    selling_price = extra_data.get('selling_price')
                    if selling_price is not None:
                        conn.execute(
                            "UPDATE products SET selling_price = ? WHERE id = ?",
                            (float(selling_price), prod_id)
                        )
                except:
                    pass  # Skip bad data
            
            conn.commit()
        finally:
            conn.close()
```

**Safety:**
- Migration only adds column with DEFAULT NULL (safe)
- Backfill reads extra_data (never modifies it)
- No data loss
- Existing products continue to work (extra_data still valid)
- New products use column directly

---

## 14. How Existing Products Will Be Handled

**Current products (in production with extra_data["selling_price"]):**

1. **After Migration 17 (add column):**
   - All products still have NULL in selling_price column
   - extra_data["selling_price"] remains unchanged
   - complete_sale() reads column first; if NULL, tries extra_data (fallback)

2. **After backfill:**
   - Products with selling_price in extra_data now also have it in column
   - Both exist (temporary double storage)
   - complete_sale() prioritizes column

3. **After product forms updated:**
   - Staff editing a product see both fields
   - When they save, both column AND extra_data updated (transition period)

4. **After Phase D stabilizes:**
   - Remove extra_data reading from complete_sale() (column only)
   - Keep extra_data ["selling_price"] as deprecated (don't remove yet, for compat)

**Risk mitigation:**
- No existing product loses its price
- Fallback ensures Phase C complete_sale() keeps working
- Gradual transition (never a cliff)

---

## 15. Import Implications

**Current product import (existing system):**
- Reads CSV: code, name, category, current_stock, last_cost_price
- Sets extra_data["selling_price"] via import handler
- Stores to database

**Phase D changes:**
- Import handler should populate selling_price column directly
- Update import route/function to accept "Selling Price" column

**Example CSV import (Phase D):**
```
Code,Name,Category,Stock,Cost,Selling Price
MICH-PRI-205,Michelin Primacy 4 205/55R16,Tyres,10,1200.00,1500.00
CONT-ULTRA-205,Continental UltraContact 205/55R16,Tyres,8,1400.00,1650.00
```

**Import handler update:**
```python
def import_products(tid, csv_data):
    for row in csv_reader(csv_data):
        product = {
            'code': row['Code'],
            'name': row['Name'],
            'category': row['Category'],
            'current_stock': float(row['Stock']),
            'last_cost_price': float(row['Cost']),
            'selling_price': float(row['Selling Price']),  ← NEW
        }
        add_product(tid, **product)
```

**Backward compatibility:**
- Old imports (without Selling Price column) still work (column defaults to NULL)
- Staff configure prices later via product form

---

## 16. Product-Management Implications

**Existing product management UI:**
- Inventory page: list products
- Product form: edit name, category, stock, cost, supplier

**Phase D additions:**
- Add "Selling Price (R)" input field to product form
- Display selling_price in product list (for POS cashier reference)
- Warn if price is NULL: "⚠ No selling price configured"

**Minimal changes:**
- Add one field to form
- Add one column to table display
- No major refactoring

---

## 17. Error-Handling Strategy

**POS error messages (friendly for cashier):**

| Error | Message | Action |
|-------|---------|--------|
| Insufficient stock | "Only 3 tyres available; you requested 4" | Keep basket, reduce qty |
| Missing selling_price | "Product does not have a price configured" | Contact manager |
| Invalid product | "Product no longer available" | Clear from basket |
| Invalid service | "Service unavailable" | Clear from basket |
| Credit + walk-in | "Please select a customer for credit sale" | Select customer |
| Invalid customer | "Customer not found" | Search again |
| Network error | "Connection lost. Please try again" | Retry (same key) |
| Duplicate/retry | Show original success (no error) | Clear basket, new sale |
| System error | "AutoStack is temporarily busy. Try again" | Retry (same key) |

**Error response structure:**
```json
{
  "success": false,
  "error": "User-friendly message",
  "error_type": "validation|network|system",
  "details": "Technical detail (log only, don't show)" (optional)
}
```

**Cashier workflow on error:**
1. Error message displayed clearly
2. Basket remains intact
3. User can edit quantities, remove items, retry
4. Retry uses same idempotency_key (safe)

---

## 18. Tenant Isolation / Security Strategy

**Authentication:**
- All POS routes require `@login_required`
- Current user's tenant_id used for all queries
- Example: `get_all_products(current_user.tenant_id, search)`

**Isolation layers:**
1. **Database layer:** All functions query `WHERE tenant_id = current_user.tenant_id` (implicit via get_conn)
2. **Route layer:** POST /pos/complete receives tid from authenticated user
3. **Transaction layer:** complete_sale(tid, ...) operates on tenant's isolated database

**Never trust browser:**
- Browser sends: item IDs, quantity, customer_id, payment_method
- Backend fetches: authoritative product data, pricing, stock, customer details
- Backend validates: all IDs belong to tenant, stock sufficient, price valid

**Example attack scenario (prevented):**
- Browser sends: `{customer_id: 9999, items: [{id: 100, quantity: 1}]}`
- If customer 9999 belongs to TENANT B and POS is TENANT A
- Backend: `complete_sale(tid_a, ...)` internally queries `get_customer(tid_a, 9999)`
- Result: customer not found (NULL) → validation error, sale rejected
- Tenant A cannot access Tenant B's data

**Complete isolation enforced by complete_sale() inside transaction:**
- All queries scoped to tid
- No cross-tenant data leakage possible

---

## 19. Exact Files Expected to Change/Create

| File | Type | Change | Lines |
|------|------|--------|-------|
| `app/routes/pos.py` | NEW | New POS routes | ~300 |
| `app/templates/pos.html` | NEW | POS UI | ~400 |
| `app/tenant_db.py` | MODIFY | Add pos_search_products() helper | +30 |
| `app/db_migrations.py` | MODIFY | Add Migration 17 (selling_price column) | +20 |
| `app/templates/base.html` | MODIFY | Add POS nav link | +3 |
| `static/pos.js` | NEW | POS client logic | ~500 |
| `tests/test_pos_transactions.py` | NEW | POS + complete_sale integration tests | ~600 |

**Optional (if needed):**
- `app/forms/product_form.py` — Add selling_price field (might already exist)
- `app/routes/inventory.py` — Update product form to handle selling_price column

**Total new code:** ~1,850 lines (UI + routes + tests, excluding migrations)

---

## 20. Test Plan

**Route/Authorization tests:**
- ✅ GET /pos/ requires login
- ✅ GET /pos/products/search requires login
- ✅ GET /pos/services requires login
- ✅ GET /pos/customers/search requires login
- ✅ POST /pos/complete requires login

**Tenant isolation tests:**
- ✅ Tenant A cannot see Tenant B's products
- ✅ Tenant A cannot see Tenant B's services
- ✅ Tenant A cannot see Tenant B's customers
- ✅ Tenant A cannot reference Tenant B's customer_id in sale
- ✅ Tenant A sale creates Tenant A invoice only

**Product search tests:**
- ✅ Search by tyre size (e.g., "205")
- ✅ Search by brand (e.g., "Michelin")
- ✅ Search by product code
- ✅ Search by name
- ✅ Case-insensitive search
- ✅ Excludes deleted products
- ✅ Shows current_stock

**Service tests:**
- ✅ GET /pos/services returns active services
- ✅ Services sorted by name
- ✅ Services include default_price

**Customer search tests:**
- ✅ Search by name
- ✅ Search by phone
- ✅ Search by vehicle registration
- ✅ Returns correct fields (id, name, phone, vehicle_registration)

**Basket logic tests:**
- ✅ Add product to basket
- ✅ Add service to basket
- ✅ Increase/decrease quantity
- ✅ Remove item
- ✅ Calculate subtotal correctly
- ✅ Apply discount correctly
- ✅ Calculate total = subtotal - discount (tax=0)

**Complete sale tests:**
- ✅ Successful cash sale
- ✅ Successful card sale
- ✅ Successful eft sale
- ✅ Successful credit sale (creates debtor)
- ✅ Credit without customer rejected
- ✅ Insufficient stock rejected (keeps basket)
- ✅ Missing selling_price rejected
- ✅ Invalid product rejected
- ✅ Invalid service rejected
- ✅ Duplicate request (idempotency) returns original result
- ✅ No duplicate stock deduction on retry
- ✅ No duplicate debtor on retry
- ✅ Invoice number unique
- ✅ Invoice number sequential

**Browser manipulation tests:**
- ✅ Browser-supplied unit_price ignored (backend price used)
- ✅ Browser-supplied total ignored (backend calculates)
- ✅ Browser cannot add quantity > stock

**Idempotency tests:**
- ✅ First request: success, duplicate=false
- ✅ Retry with same key: success, duplicate=true
- ✅ Retry creates no new invoice
- ✅ Retry deducts no additional stock
- ✅ Retry creates no additional debtor

**Integration with complete_sale() tests:**
- ✅ POST /pos/complete calls complete_sale() with correct params
- ✅ Response structure matches POST /pos/complete response
- ✅ Error from complete_sale() properly returned to browser

**Regression tests (ensure Phase C still works):**
- ✅ Phase C complete_sale() still passes all 32 tests
- ✅ Phase B customer search still works
- ✅ Phase A migration tests still pass
- ✅ Import regressions still pass

**UI/Cashier workflow tests (manual or Selenium):**
- ✅ Walk-in sale completes end-to-end
- ✅ Credit sale requires customer selection
- ✅ Product search results appear in real-time
- ✅ Basket displays correct totals
- ✅ Success message shows invoice number

**Total test count goal:** ~80 tests (50 unit + 30 integration/manual)

---

## 21. Out of Scope (Phase D)

❌ Refunds  
❌ Returns  
❌ Split payments  
❌ Cash-up/till reconciliation  
❌ Quotations  
❌ Supplier purchasing  
❌ Purchase orders (separate existing feature)  
❌ WhatsApp messaging  
❌ AI features  
❌ Loyalty programs  
❌ Multi-branch support  
❌ Advanced reports  
❌ VAT engine (tax always 0)  
❌ Manager price overrides  
❌ Complex staff permissions  
❌ Accounting integrations  

---

## 22. Recommended Implementation Order

### **Phase D.1: Foundation**
1. Create app/routes/pos.py with basic routes (GET /pos, GET search routes)
2. Create app/templates/pos.html with bare-bones layout
3. Implement GET /pos/products/search
4. Implement GET /pos/services
5. Implement GET /pos/customers/search (reuse existing)
6. **Tests:** Route/auth tests, search tests

### **Phase D.2: Basket logic**
7. Build client-side basket (add, remove, quantity)
8. Implement discount input
9. Calculate subtotal/total on client
10. Display nicely in template
11. **Tests:** Basket calculation tests

### **Phase D.3: Payment & complete_sale() integration**
12. Add payment method selector
13. Implement POST /pos/complete route
14. Call complete_sale() atomically
15. Handle response (success/error)
16. Display confirmation
17. **Tests:** Complete sale tests, idempotency tests

### **Phase D.4: Selling price column (if adopted)**
18. Create Migration 17 (add column)
19. Write backfill script
20. Update complete_sale() to read column
21. Update product forms to set column
22. Update import handlers
23. **Tests:** Regressions, new column tests

### **Phase D.5: Cashier UX polish**
24. Error messages (friendly)
25. Disable buttons during processing
26. Clear basket on success
27. Reset idempotency key
28. Mobile responsiveness
29. Keyboard shortcuts (optional: F1=new sale, F2=payment, etc.)
30. **Tests:** UI/manual tests

### **Phase D.6: Final validation**
31. Run all test suites (unit, integration, regression)
32. Manual end-to-end testing (all payment methods)
33. Tenant isolation spot-checks
34. Security review (browser manipulation tests)
35. Load testing (if needed)

---

## Summary Table

| Aspect | Recommendation |
|--------|-----------------|
| **Screen Layout** | Sidebar nav-free, full-width basket + search |
| **Product Search** | Enhanced search (tyre_size, brand, code, name, category) |
| **Customer Search** | Reuse existing search_customers() |
| **Services** | Display active services with prices |
| **Selling Price Storage** | Add products.selling_price column (Migration 17) |
| **Invoice Numbers** | Backend-generated (sequences table, atomic) |
| **Discount** | Invoice-level only, validated 0 ≤ discount ≤ subtotal |
| **Tax** | Always 0 in Phase D (VAT deferred to Phase E) |
| **Payment Methods** | Cash, Card, EFT, Credit (credit requires customer) |
| **Idempotency** | Browser-generated UUID, reused on retry, new key after success |
| **Transaction Engine** | Call complete_sale() (all logic atomic in Phase C) |
| **Error Handling** | User-friendly messages; keep basket intact on failure |
| **Tenant Isolation** | All queries scoped via current_user.tenant_id |
| **Browser Trust** | Never trust totals/prices/stock; validate on backend |
| **Routes Required** | 5 new routes (2 GET search, 1 POST complete, etc.) |
| **Files Created** | POS route, template, tests, optional JS (~1,850 LOC) |
| **Tests** | ~80 tests (unit, integration, regression, manual) |

---

## READY FOR PHASE D POS PLAN REVIEW — NO CODE CHANGED