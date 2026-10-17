# Backup-growth fix: isolated Linux test plan

Status: local-only CI preparation. No workflow has been dispatched, no repository
has been created, and nothing has been pushed. A local commit does not authorize
uploading code or starting a GitHub Actions run.

Approved application source: `6c54dcf066ab2a6cd3e90df1537f858169b0d7a2`
(branch `fix/migration-backup-growth`, schema version 18). The Linux runner checks
SHA-256 fingerprints of the four application source files from this exact commit.
The CI preparation changes only tests, workflow configuration, dependencies and
this guide; it does not change the migration implementation.

## What will run

- GitHub-hosted `ubuntu-24.04`, Python 3.12, one job, a ten-minute timeout.
- Manual `workflow_dispatch` only: no push, pull-request, scheduled or deployment triggers.
- The two migration test files only. No application server or application factory.
- 9 backup tests and 14 concurrency cases under both `spawn` and Linux `fork`:
  **37 passing cases expected, zero skips**. A coverage guard fails if either
  multiprocessing method is missing, cases are skipped, or the count differs.
- Fresh and populated synthetic SQLite databases, rollback-journal and WAL modes,
  pending upgrades with verified pre-migration backups, backup failure, whole-upgrade
  rollback/retry, writer and commit timeouts, worker crashes and waiting workers.
- Two workers each open and close 50 ordinary connections. There must be no backup
  directory and the database hash must remain unchanged.

All database handles opened by the parent are closed before workers are forked.
No background service or sync worker is started. The commit-contention test opens
its deliberate blocking reader only after its worker has already started.

## Isolation

The production repository tracks `.env.production`. Do not upload an archive of
that repository, its Git history, its data directory, backups or encryption keys.

Checkout is an explicit non-cone sparse allowlist, with no submodules, LFS,
persisted checkout credentials, cache or database artifact upload. Workflow
permissions are `contents: read`; there are no `secrets`, deployment environments,
Render steps, service containers or self-hosted runners.

Dependencies are installed from public PyPI into a new virtual environment before
testing. Only Flask, Flask-Login, cryptography and pytest are requested, with direct
versions matching local verification; pip resolves their transitive dependencies.
Python/SQLite versions and the installed dependency versions are printed.

The test step uses `sudo unshare --net`, drops back to the non-root runner user,
then starts Python through `env -i` with a short explicit environment allowlist.
The Python library search path is derived from the selected Python installation
and must exactly match `sys.base_prefix/lib`; an arbitrary inherited
`LD_LIBRARY_PATH` is not accepted. This preserves the Linux setup-python runtime
without admitting ambient credentials or library injection paths.
The new network namespace has no external interfaces. The runner verifies that
only loopback is visible and rejects unexpected environment variables, sensitive
source files, an incorrect Python/OS, changed application source or missing fork
support. Namespace creation failure stops the job; there is no less-isolated fallback.

The runner creates a fresh temporary synthetic data root and HOME. Pytest plugin
autoloading is disabled. SQLite databases and JUnit output stay on the disposable
VM and are not uploaded. No existing database is restored, copied or migrated.

## Using the existing private repository

A second repository is not technically required. This workflow does not consume
repository/organization production secrets: it never references the secrets or
vars contexts or a deployment environment. The checkout action uses only the
short-lived, read-only GitHub token and does not persist it. The test process
receives no GitHub token or other inherited credentials. Sparse checkout omits
the tracked `.env.production` and all database files. The pinned checkout action
also uses a blob-filtered fetch for sparse checkout.

However, repository-level side effects must be checked before approving a remote
run. A different workflow can trigger on `workflow_run` when this test finishes;
read-only permissions here cannot restrict that separate workflow. A future push
or workflow registration commit can also trigger Render auto-deploy or another
push workflow. Local review cannot establish the live repository's settings.
No authenticated GitHub CLI was available during this review, and no production
settings were accessed. Existing-private-repository readiness is therefore
**conditional**, not remotely verified.

Before upload/run approval, inspect only configuration/metadata and establish:

- The repository is private and standard GitHub-hosted Actions are allowed.
- No `workflow_run` consumer (including wildcard/name/branch matches) will turn
  this synthetic workflow into a deployment. Check any external workflow listener.
- Publishing `fix/migration-backup-growth` or registering the workflow cannot
  trigger Render deployment, deployment hooks, or other production automation.
- `.github/workflows/backupfix-linux.yml` is registered on the default branch,
  or a separately approved workflow-only registration can be made safely.
- The reviewed workflow revision is selected and includes all CI preparation files.

Stored production secrets need not be disclosed or copied. Do not modify Render
settings, deployment triggers or GitHub settings as part of this local task.
If those gates cannot be verified, do not upload or dispatch this workflow in
the existing repository. A separate sandbox is an alternative only if later
explicitly approved; none has been created.

## Exact launch procedure - requires a separate approval

1. Verify the repository-level gates above using read-only configuration review.
   The remote is `ameenosman1104-hash/autostack`; its privacy/settings have not
   been authenticated from this machine. Do not expose credential values.
2. Obtain approval to publish the reviewed local CI commit on
   `fix/migration-backup-growth`. No push is authorized by a local-commit approval.
3. If the workflow is not already registered on the default branch, obtain
   separate approval for a workflow-only registration after confirming it cannot
   auto-deploy. Do not merge the application fix into the production default
   branch merely to make the Run workflow button appear. GitHub requires the
   manual workflow to be registered on the default branch.
4. Once registration and publishing are approved, open the repository's Actions
   tab -> AutoStack synthetic SQLite migration checks -> Run workflow. Select
   branch `fix/migration-backup-growth`, whose tip must match the exact reviewed
   CI commit reported in chat. The app fingerprints must still match approved
   application commit `6c54dcf066ab2a6cd3e90df1537f858169b0d7a2`.
5. Obtain approval for the run itself, then click Run workflow once. No secrets,
   database uploads or input parameters are required.
6. Check the job log for Python 3.12, SQLite version, approved application source,
   all 14 `[fork-...]` and 14 `[spawn-...]` concurrency cases and 9 backup cases:
   **37 passed, zero skips**. A setup/isolation failure is not a test pass.
7. Record the run URL, tested CI SHA and result, then stop. A passing synthetic
   run neither verifies production storage nor authorizes a deployment.

If a future sandbox repository is explicitly approved instead, transfer only the
workflow, runner, test requirements, the two migration test files, the four
fingerprinted app files, and optionally this guide. Omit existing Git history,
`.env*`, databases, backups and encryption keys.

## Cost estimate

GitHub's standard Linux 2-core price is currently US$0.006 per minute beyond
included minutes. A roughly 2-5 minute run should cost US$0.012-0.030 if billable;
within remaining included minutes, additional compute cost is US$0. A ten-minute
job is approximately US$0.06 at that rate, subject to billing rounding/overhead.
Actual duration and account allowance have not been checked. No cache or artifact
storage is configured. Do not make the source public to obtain free runner minutes.

Sources (checked 2026-10-17):
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow
- https://docs.github.com/en/billing/reference/actions-runner-pricing
- https://www.man7.org/linux/man-pages/man1/unshare.1.html

## Local verification and remaining limits

The Windows host cannot execute Linux fork or network namespaces. Local tests
exercise spawn and explicitly skip fork; they cannot establish Linux success.
The workflow and shell syntax can be inspected locally. The first approved Ubuntu
run must verify dependency installation, namespace permissions and all 37 cases.
This targeted suite does not resolve the existing full-suite failures or prove
production storage, deployed configuration or database recoverability.

Local validation on 2026-10-17: **23 passed, 14 skipped** in 18.69 seconds.
All 14 skips are the intentionally Linux-only fork variants. YAML structure,
both embedded shell scripts (`bash -n`), Python syntax and approved-source
fingerprints passed validation. The Linux-only entry point correctly rejected
Windows before application imports. Ubuntu execution remains pending approval.

Final safety review: dependency installation precedes network isolation; the
Python 3.12 x64 runtime and pinned dependencies have compatible Linux
metadata/distributions. The library-path handling was corrected for setup-python
on Ubuntu. Actual Ubuntu execution, private-repository configuration and any
external deployment listeners remain unverified until separately approved review/run.

Final local review results (2026-10-17): **23 passed, 14 Linux-only skips** in
20.67 seconds. Collection confirmed exactly 14 fork, 14 spawn and 9 backup
cases. All four application fingerprints match the approved commit. YAML,
shell and Python syntax checks passed; a side-effect-free POSIX host-guard
simulation accepted valid metadata and rejected six unsafe configurations.
The Linux/Python 3.12 dependency wheel set resolved and downloaded successfully
without installation or execution. This supports Ubuntu compatibility but does
not replace an actual Ubuntu run. Existing-repository run clearance still needs
the read-only remote configuration gates described above.
