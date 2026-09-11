# AutoStack launch safety: implementation and migration boundary

## Implemented without a financial migration
- Original debt amounts and historical payments remain unchanged.
- Remaining balances are calculated consistently for the list, totals, reports and reminder text.
- Future payment recording validates amount, debtor existence and overpayment and commits atomically.
- Private pages are not cached; the updated worker removes old AutoStack caches.
- POST forms and same-origin AJAX require CSRF tokens; disabled users cannot reuse a session.
- Purchase-date reminder calculation preserves manual dates and advances recurring schedules after a successful send.

## Proposed next migration (not applied)
1. Quiesce writes and create SQLite backup-API snapshots of main and tenant databases. Verify integrity and a restore into an isolated directory before proceeding. Keep existing financial amounts unchanged.
2. Replace best-effort startup ALTER statements with transactional versioned migrations. Add missing product columns using constant defaults, then backfill only timestamps. Stop on storage failures instead of creating an in-memory database.
3. Add debtor due_date, sync_snapshot and sync_pending fields and a reminder_dispatch ledger with a unique debtor/schedule key. These support conflict review and durable duplicate prevention. Do not delete customer rows.
4. Provision an encryption key separately from the database and source repository. Encrypt saved credential values, preserving a tested rollback snapshot and key recovery procedure. Never print credential values. Removing plaintext from active settings does not remove it from old backups, which need secure retention.
5. Only after the ledger and sync tests pass, configure a background worker on persistent storage. Require explicit reminder enablement. On uncertain delivery, hold for review rather than automatically resending.

## Deployment verification still required
- Confirm Render uses a persistent disk and configure AUTOSTACK_DATA_DIR to that mounted directory; keep SECRET_KEY stable in environment configuration.
- Verify restoration, encryption-key recovery, SMTP delivery, scheduler operation and native PWA installation in a real browser.
- Validate import matching/conflicts and existing synced balances before enabling unattended sync.
- Login throttling and a complete security review remain outstanding.

Automatic approval review rejected the earlier combined database/credential rewrite. No database migration or credential conversion has been applied. The proposed migration above requires approval before implementation/application.

Read-only preflight: python scripts/database_preflight.py <data_directory>
The report includes schema/integrity and counts only, never credentials or customer details.
