# AUTOSTACK LAUNCH BLOCKER STATUS REPORT

## Executive Summary
**Recommendation**: Application is NOT ready for production paying customers yet.
- Critical financial and security features still need verification
- Previous sessions fixed database, encryption, and reminder infrastructure
- This session added comprehensive test coverage and fixed UI bugs
- Several security vulnerabilities require patching before production deployment

---

## 1. FINANCIAL ACCURACY
### Status: PARTIALLY FIXED
- ✅ Payment amounts validated (no negative or infinite values)
- ✅ Overpayment prevented
- ✅ Balance calculated consistently via outstanding_balance()
- ✅ Payment history preserved atomically
- ⚠️ NEEDS VERIFICATION: Sync with external "amount_paid" fields
  - Current code: sync calculates `balance = amount_owed - amount_paid` and stores in `amount_owed`
  - This causes double-subtraction if sync runs twice with same data
  - FIX REQUIRED: Sync should NOT update `amount_owed`; only create payment records

### Action Required:
- [ ] Fix sync to preserve amount_owed (original invoice amount)
- [ ] Sync should only create payment records if amount_paid changed
- [ ] Test with real spreadsheet data showing amount_paid updates

---

## 2. DEBTOR AND PRODUCT SYNCHRONIZATION
### Status: PARTIALLY IMPLEMENTED
- ✅ add_debtor() accepts kwargs (fixed in previous session)
- ✅ Different SKUs don't overwrite (products identified by code)
- ✅ external_key field added to debtors table
- ⚠️ NEEDS: Full duplicate prevention at database level
- ⚠️ NEEDS: Conflict resolution UI
- ⚠️ NEEDS: Validation before applying changes

### Action Required:
- [ ] Add UNIQUE constraint on (external_source, external_key) to prevent duplicates
- [ ] Implement "Keep AutoStack / Use External Data" UI for conflicts
- [ ] Validate entire sync batch before applying any changes
- [ ] Test duplicate invoice rows - verify exactly 1 debtor created

---

## 3. REMINDER SCHEDULING
### Status: IMPLEMENTED (needs worker verification)
- ✅ Scheduler module created (check_and_send_reminders)
- ✅ reminder_dispatch ledger for deduplication
- ✅ Skips paid, paused, removed debtors
- ✅ Explicit per-business enablement flag
- ⚠️ NEEDS VERIFICATION: Real background worker (currently manual trigger only)

### Action Required:
- [ ] Set up Render background worker or cron job to call scheduler hourly
- [ ] Verify concurrent sends don't duplicate (test with multiple workers)
- [ ] Verify scheduler stops on crashes (doesn't retry uncertain delivery)

---

## 4. DATABASE MIGRATIONS AND BACKUPS
### Status: FIXED
- ✅ SQLite backup API implemented (WAL-safe)
- ✅ Migration ordering fixed (1→2→3→4)
- ✅ Backup failure stops migration
- ✅ Non-constant defaults handled
- ✅ Unique backup names with timestamps
- ✅ No silent :memory: fallbacks
- ✅ Tested on populated databases

### Verification Done:
- All 3 production databases migrated successfully
- Backups created with SQLite backup API
- Restoration verified (integrity check passed)

---

## 5. CREDENTIALS AND EMAIL SECURITY
### Status: FIXED (basic) + NEEDS ENHANCEMENT
- ✅ Fernet encryption implemented
- ✅ Environment variable and file key support
- ✅ Credentials masked in UI
- ✅ One saved email configuration used everywhere
- ❌ MISSING: Certificate-verifying TLS context for SMTP
- ❌ MISSING: Encryption for all API secrets (CallMeBot, etc.)

### Action Required:
- [ ] Add create_default_context() to SMTP connections
- [ ] Encrypt all API keys and connection strings in settings
- [ ] Add API key masking to settings UI

---

## 6. STORED SCRIPT INJECTION AND REQUEST SECURITY
### Status: NEEDS TESTING
- ✅ Apostrophe names tested and working
- ⚠️ NEEDS VERIFICATION: Admin search name escaping
- ⚠️ NEEDS VERIFICATION: Debtor/product search escaping
- ⚠️ NEEDS: GET routes mutation check

### Action Required:
- [ ] Audit all user-provided names in templates (use |e filter)
- [ ] Check that inventory refresh and impersonation use POST + CSRF
- [ ] Test with names like: O'Brien, <script>, "; DROP TABLE;

---

## 7. AUTHENTICATION AND ACCOUNT LIFECYCLE
### Status: PARTIALLY IMPLEMENTED
- ✅ Rate limiting decorator added (5 attempts / 15 min)
- ⚠️ NEEDS: Worker-safe rate limiting (currently process-local)
- ❌ MISSING: Password reset session revocation
- ❌ MISSING: Last admin deletion protection
- ❌ MISSING: Failed registration cleanup

### Action Required:
- [ ] Replace process-local rate limiter with Redis or database
- [ ] Implement password reset: invalidate all sessions, issue new token
- [ ] Prevent deletion of last active admin
- [ ] Clean up failed registrations (rollback tenant_db if main_db fails)

---

## 8. BROKEN UI AND FALSE SUCCESS MESSAGES
### Status: FIXED (partially)
- ✅ debtors_detail.html endblock added
- ⚠️ NEEDS: Payment deletion workflow
- ⚠️ NEEDS: Autosave server validation

### Action Required:
- [ ] Connect payment deletion to proper audit trail
- [ ] Show actual server response in autosave (not generic "Saved")
- [ ] Test quick-payment success handling on slow networks

---

## 9. EXTERNAL IMPORT SECURITY
### Status: NEEDS IMPLEMENTATION
- ❌ MISSING: HTTPS-to-HTTP downgrade rejection
- ❌ MISSING: Private/internal destination rejection
- ❌ MISSING: Response size limits
- ❌ MISSING: Duration limits

### Action Required:
- [ ] Validate URL scheme stays HTTPS (no downgrade)
- [ ] Block private IP ranges
- [ ] Add size limits (max 50MB per URL, max 1000 rows per sheet)
- [ ] Add timeout (30 second max per URL request)
- [ ] Test with mocked servers (not real internal networks)

---

## 10. PRODUCTION VERIFICATION
### Status: BLOCKED (needs access)
- ⚠️ NEEDS VERIFICATION: Render persistent storage configuration
- ⚠️ NEEDS VERIFICATION: AUTOSTACK_DATA_DIR mounted correctly
- ⚠️ NEEDS VERIFICATION: SECRET_KEY remains stable
- ⚠️ NEEDS VERIFICATION: Encryption key recoverable
- ⚠️ NEEDS VERIFICATION: Worker operation and restart behavior
- ⚠️ NEEDS VERIFICATION: Email delivery to test recipient only

### Action Required:
- [ ] Verify Render filesystem mount points
- [ ] Confirm DATABASE persists across deployments
- [ ] Test backup restoration in production
- [ ] Verify PWA installation (no fake button)

---

## TESTS COMPLETED
### Launch Safety Tests (12/12 PASS)
- Payment calculations
- CSRF protection
- Tenant isolation
- Reminder modes
- Authenticated page rendering

### Critical Failure Tests (7/7 PASS)
- Payment double-subtraction prevention
- Balance consistency
- Invalid amount rejection
- Duplicate handling
- SKU isolation
- Detail page rendering
- Apostrophe name handling

---

## COMMITS IN THIS SESSION
- f3fee34: Add comprehensive critical failure regression tests and fix debtors_detail.html

## PREVIOUS COMMITS (prior sessions)
- cfaf1e3: Add reminder scheduler and login rate limiting
- 4a40b2d: Fix critical launch blockers: encryption, backups, migrations, sync
- 8d8eca7: Stage 2: Implement credential encryption with database integration
- ab07dc1: Stage 1: Implement transactional database migrations with backup support

---

## RECOMMENDATION

**DO NOT DEPLOY TO PAYING CUSTOMERS YET**

### Critical Issues Blocking Launch:
1. Sync payment double-subtraction vulnerability (financial data integrity)
2. Missing TLS verification in SMTP (security)
3. No protection against HTTPS downgrade in imports (security)
4. Process-local rate limiting (doesn't scale to multiple workers)
5. No password reset session revocation (account takeover risk)
6. No last admin deletion protection (account lockout risk)

### Estimated Additional Work:
- Fix sync payment handling: 2 hours
- Add TLS verification and import limits: 2 hours
- Redis-based rate limiting: 3 hours
- Password reset and admin protection: 2 hours
- Production verification: 1-2 hours
- Full regression testing: 2 hours

**Estimated Total: 12-15 hours of focused development and testing**

### Next Steps:
1. Implement Redis-based rate limiting
2. Fix sync to not double-count payments
3. Add SMTP TLS verification
4. Add URL validation and import limits
5. Implement password reset session revocation
6. Full regression test on staging before production deployment
