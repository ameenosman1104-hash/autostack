# Local Excel/CSV live connection implementation

Current finding: `live_excel_sync.py` explicitly returns a placeholder; it does not fetch Microsoft Graph data. The existing URL/API worker does not watch uploaded local files.

Completed safe preparation: `file_snapshot.py` reads bounded CSV/XLSX files, normalizes invoice/name/email/date/amount columns, rejects duplicate invoice IDs and invalid input, and detects email-only changes. It never writes customer records. Tests use temporary files only.

Proposed next stage requiring approval after automatic review blocked the initial patch:

1. Add a revocable, business-scoped device connection. Store only a hash of a random pairing token on the server. Keep the device token encrypted with Windows user protection. No database credentials on the device.
2. Provide a Windows background connector with explicit file/worksheet selection and a 15-second check. It uploads a complete validated snapshot over HTTPS after file saves. Computer must be on; the browser need not be open. Do not claim an uploaded copy stays linked.
3. Add connection, invoice mapping, conflict, and audit tables in an additive migration. Serialize each application transaction to prevent concurrent duplicate creation.
4. Match by source connection plus invoice identifier. Existing imported rows without a stable identifier require explicit matching confirmation; never merge automatically by name alone.
5. Apply validated field changes to matched records. Preserve blank-email values, custom reminders, and historical payments. Financial corrections require auditable reconciliation; conflicting edits are staged for Keep AutoStack / Use Source review. Missing rows are flagged only.
6. Authenticated Debtors page polling updates only the table/summary after a revision changes, preserving filters and open forms. Show offline, errors, conflicts, last successful sync, revoke, and sync-now controls.
7. Test two businesses, revocation, concurrent/replayed snapshots, email-only updates, amount changes, new rows, deletions, manual dates, conflict resolution, restart persistence, and actual file-to-browser delivery.

No live connection is active yet. The parser alone does not send or apply updates. Google/OneDrive and arbitrary external POS systems are separate connectors; do not advertise them as operational until implemented and tested.
