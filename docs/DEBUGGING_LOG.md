# ODTF Debugging Log — RHEL 9 Migration Testing

**Period:** 15–16 April 2026  
**Server:** codon-emdb-onedep-dev-03.ebi.ac.uk (RHEL 9)  
**Fork:** `lucas-ebi/onedep-test-framework` (from `f764a01`)  
**Plan:** 59 test depositions across EC, EM, NMR, XRAY, SSNMR, ND experiment types  

---

## Table of Contents

1. [Extensionless files crash `_upload_all_files`](#1-extensionless-files-crash-_upload_all_files)
2. [YAML generator indentation bug](#2-yaml-generator-indentation-bug)
3. [`config.py` `parse_task` not handling nested YAML structure](#3-configpy-parse_task-not-handling-nested-yaml-structure)
4. [Create deposition "Bad Request" for all entries](#4-create-deposition-bad-request-for-all-entries)
5. [Process killed on shared login node](#5-process-killed-on-shared-login-node)
6. [Response body lost on API errors](#6-response-body-lost-on-api-errors)
7. [Token invalidation across concurrent entries](#7-token-invalidation-across-concurrent-entries)
8. [Upload 403 — `updateDepositorTable` wipes depositor M2M link](#8-upload-403--updatedepositorstable-wipes-depositor-m2m-link)
9. [Submit 403 — missing Bearer token in submit view](#9-submit-403--missing-bearer-token-in-submit-view)
10. [`SynchronousOnlyOperation` — Django ORM in async context](#10-synchronousonlyoperation--django-orm-in-async-context)
11. [Rich Live display + stdout redirect conflict](#11-rich-live-display--stdout-redirect-conflict)
12. [Submit timeout — `sock_read` too short](#12-submit-timeout--sock_read-too-short)
13. [Processing monitor timeout — 30 min too short for EM](#13-processing-monitor-timeout--30-min-too-short-for-em)
14. [Create timeout — REST adapter 600s too short for EM](#14-create-timeout--rest-adapter-600s-too-short-for-em)
15. [EM metadata — `em_map_upload.pkl` missing for non-EM entries](#15-em-metadata--em_map_uploadpkl-missing-for-non-em-entries)
16. ["Invalid input file" — empty tempdep directory](#16-invalid-input-file--empty-tempdep-directory)
17. [Unlock request returned 500](#17-unlock-request-returned-500)
18. [`process/` endpoint returns 500 — missing `django_q_ormq` table](#18-process-endpoint-returns-500--missing-django_q_ormq-table)
19. [No qcluster worker running — tasks enqueued but never executed](#19-no-qcluster-worker-running--tasks-enqueued-but-never-executed)
20. [Deposit app outdated — v0.63.1 → v0.65.1 upgrade](#20-deposit-app-outdated--v0631--v0651-upgrade)

---

## 1. Extensionless files crash `_upload_all_files`

**Symptom:** `ValueError` from `f.split(".")` in `cli.py` line 311 when YAML plan contains files without extensions (e.g. `parameter-file`, `topology-file`, `nmr-peaks`, `nmr-restraints`).

**Root cause:** The YAML generator (`generate_odtf_plan.py`) produced file entries like `parameter-file` for NMR depositions. `_upload_all_files` splits on `.` to separate content type from format, but these have no extension.

**Affected entries:** 8 NMR/SSNMR/ND entries with extensionless filenames.

**Fix:** Fixed in the YAML generator — not an ODTF code change. The generator was updated to always emit `content_type.format` pairs (e.g. `nmr-restraints.mr`).

**Type:** External fix (generator script).

---

## 2. YAML generator indentation bug

**Symptom:** `parse_task` silently returned tasks with `files=None` or `source=None` because the YAML structure was flat instead of nested.

**Root cause:** `emit_yaml()` in `generate_odtf_plan.py` produced:

```yaml
  - upload:
    files:          # ← wrong: should be indented under upload
      - model.pdbx
```

Instead of:

```yaml
  - upload:
      files:        # ← correct: nested under upload
        - model.pdbx
```

**Fix:** Added 2 extra spaces of indentation in the generator's `emit_yaml()` function for `upload.files` and `compare_files` blocks.

**Type:** External fix (generator script).

---

## 3. `config.py` `parse_task` not handling nested YAML structure

**Symptom:** After fixing the YAML indentation, `parse_task` still returned `files=None` because it called `task_data.get("files")` on the outer dict instead of extracting from the nested `upload` sub-dict.

**Root cause:** `parse_task` treated `task_data` as a flat dict. With correct YAML, the structure is `{"upload": {"files": [...]}}`, so it needed `task_data["upload"].get("files")`.

**Fix (commit `2367900`):**

```python
# Before:
elif "upload" in task_data:
    return UploadTask(files=task_data.get("files"))

# After:
elif "upload" in task_data:
    upload_data = task_data["upload"] or {}
    return UploadTask(files=upload_data.get("files") if isinstance(upload_data, dict) else None)
```

Same pattern applied to `compare_files` and `compare_repos` tasks with defensive `isinstance` checks.

**Type:** Code fix in `odtf/config.py`.

---

## 4. Create deposition "Bad Request" for all entries

**Symptom:** All 59 entries returned HTTP 400 "Bad Request" on `create_deposition`. No useful error body was returned (see issue #6).

**Root cause:** The upstream `create_dep_task()` had a hardcoded email `wbueno@ebi.ac.uk` that didn't match the ORCID-registered depositor on the RHEL9 dev server. The server rejected every create because the email was invalid for the ORCID `0000-0003-1855-0871`.

**Investigation:** The Country enum was also investigated as a possible cause (`Country('GB')` is invalid — must use `Country('United Kingdom')`), but the YAML already had the correct value. The actual fix was simply changing the hardcoded email.

**Fix (commit `10072e9`):**

```python
# Before:
email="wbueno@ebi.ac.uk"

# After:
email="lucas@ebi.ac.uk"
```

After this change, all 59 creates succeeded immediately.

**Type:** Code fix in `odtf/cli.py`.

---

## 5. Process killed on shared login node

**Symptom:** Running 59 concurrent async entries on the shared login node caused the process to be OOM-killed. Log showed `Killed` or process simply disappeared.

**Root cause:** The login node is shared and has resource limits. 59 concurrent HTTP sessions + file I/O exceeded memory limits.

**Fix:** 
1. Used `--max-concurrent 5` to limit concurrency.
2. Ran via tmux on the server instead of directly from SSH.
3. Used `caffeinate -dims` on local machine to prevent sleep during long runs.

**Type:** Operational workaround.

---

## 6. Response body lost on API errors

**Symptom:** When API calls failed, the log only showed `response.reason` (e.g. "Bad Request") with no detail about what the server actually complained about.

**Root cause:** `_do()` in `aioapi.py` logged only `response.reason` and raised `DepositApiException(response.reason, response.status)` without reading the response body.

**Fix (commit `b8c78f0`):**

```python
# Before:
self._logger.error(msg=log_line)
raise DepositApiException(response.reason, response.status)

# After:
try:
    error_body = await response.text()
except Exception:
    error_body = "<could not read response body>"
self._logger.error(msg=f"{log_line} body={error_body}")
raise DepositApiException(f"{response.reason}: {error_body}", response.status)
```

**Type:** Code fix in `odtf/aioapi.py`.

---

## 7. Token invalidation across concurrent entries

**Symptom:** After create succeeded for some entries, others would get 403 on upload. Hypothesis: each entry calling `create_token()` independently invalidated the previous token.

**Investigation:** 
- First attempt: made all entries share a single token created upfront.
- This did NOT fix the 403 — the root cause was actually issue #8 (depositor M2M wipe), not token invalidation.

**Fix (commit `fe57809`):** Shared token was kept anyway as a good practice — reduces redundant token creation and avoids any potential invalidation race:

```python
# In run_all_entries():
shared_api_key = create_token(config.api.get("orcid"), expiration_days=7)

# Passed through to run_entry_tasks() and submit_task()
```

**What didn't work:** Sharing the token alone did NOT resolve the 403. The real fix was issue #8.

**Type:** Code fix in `odtf/cli.py` (kept as improvement, not the actual 403 fix).

---

## 8. Upload 403 — `updateDepositorTable` wipes depositor M2M link

**This was the most complex issue and required the deepest investigation.**

**Symptom:** `create_deposition` returned 201 (success), but immediately after, `upload_file` returned 403 `authentication_failed`. Curl with the same Bearer token also got 403.

**Investigation timeline:**

1. **Checked if async library was the issue** — tried synchronous `requests` library → same 403. Ruled out.
2. **Checked if token was the issue** — curl with correct Bearer token → still 403. Ruled out.
3. **Traced `TokenAuthMiddleware`** in the server-side Django code:
   - For `create` (no `dep_id` in path) → returns `AnonymousUser` immediately ✓
   - For `upload` (has `dep_id` in path) → looks up depositor via `user.depositor_set.filter(depositions__username=dep_id)` → **empty queryset** → 403
4. **Checked the database directly:**
   ```python
   dep.depositions.filter(username="D_800712").exists()  # False!
   ```
   The depositor M2M relationship was absent for newly created depositions.
5. **Found root cause in server-side code:** `updateDepositorTable()` in `apilayer.py`:
   ```python
   self.user.depositor_set.set(new_orcids)  # new_orcids is [] for brand new depositions!
   ```
   The call chain was: `build()` → `initProgress()` → `saveListToPickle` → `buildIntegrated` → `updateDepositorTable()`. Since `page_orcids` is empty for brand new depositions, it called `.set([])` which **wiped** the depositor link that `_add_users()` had just created.
6. **Confirmed via Apache log:** `"New ORCID IDs for depositions 'D_800712': []"`
7. **Manual fix verified:** `dep.depositions.add(u)` in Django shell → upload succeeded.

**Fix (commit `254874b`):** Added a workaround in `cli.py` to re-link the depositor immediately after create:

```python
# Workaround: server-side updateDepositorTable wipes the depositor
# link that _add_users() creates during build(). Re-add it here.
from asgiref.sync import sync_to_async
from wwpdb.apps.deposit.main.models import Depositor, DepositionDjango

@sync_to_async
def _relink_depositor():
    depositor, _ = Depositor.objects.get_or_create(orcid=orcid)
    dep_obj = DepositionDjango.objects.get(username=copy_dep.dep_id)
    dep_obj.depositor_set.add(depositor)

await _relink_depositor()
```

**What didn't work first:**
- Sharing tokens (issue #7) — irrelevant
- Switching to sync library — same result
- Suspecting cookie vs Bearer auth mismatch — irrelevant

**Note:** This is a **server-side bug** in `py-wwpdb_apps_deposit`. The ODTF workaround should be removed once the server is fixed.

**Type:** Client-side workaround for server bug, in `odtf/cli.py`.

---

## 9. Submit 403 — missing Bearer token in submit view

**Symptom:** Submit step got 403 even after upload succeeded. The submit view GET returned 403.

**Root cause:** `submit_task` used only cookie auth (`depositor-orcid` cookie) to GET the view endpoint and obtain the CSRF token. The server's `TokenAuthMiddleware` requires Bearer token for endpoints that include a `dep_id` in the path.

**Fix (commit `254874b`, same commit as #8):**

```python
# Added Bearer token header to the submit view GET:
auth_headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
async with session.get(
    url=view_url,
    cookies={"depositor-orcid": orcid_cookie},
    headers=auth_headers,  # ← added
    ssl=False
) as response:
```

Also threaded `api_key` parameter through `submit_task()` and `run_entry_tasks()`.

**Type:** Code fix in `odtf/cli.py`.

---

## 10. `SynchronousOnlyOperation` — Django ORM in async context

**Symptom:** After adding the depositor re-link workaround (#8), the first run crashed with:
```
django.core.exceptions.SynchronousOnlyOperation: You cannot call this from an async context - use a thread or sync_to_async.
```

**Root cause:** `Depositor.objects.get_or_create()` and `DepositionDjango.objects.get()` are synchronous Django ORM calls. They were invoked inside `async def create_dep_task()`, which Django detects and blocks.

**Fix (commit `7272c16`):** Wrapped the ORM calls with `@sync_to_async` decorator from `asgiref.sync`:

```python
from asgiref.sync import sync_to_async

@sync_to_async
def _relink_depositor():
    depositor, _ = Depositor.objects.get_or_create(orcid=orcid)
    dep_obj = DepositionDjango.objects.get(username=copy_dep.dep_id)
    dep_obj.depositor_set.add(depositor)

await _relink_depositor()
```

**Type:** Code fix in `odtf/cli.py`.

---

## 11. Rich Live display + stdout redirect conflict

**Symptom:** Running `odtf ... > onedep_test.log 2>&1` in tmux caused the process to hang. Only 23 lines were written to the log, then no growth for minutes.

**Root cause:** Rich's `Live` display writes ANSI escape codes and cursor control sequences to stdout. When stdout is redirected to a file, the Live renderer blocks waiting for terminal capabilities that don't exist.

**Fix:** Run without stdout redirect:
```bash
tmux new-session -d -s odtf "source env.sh && cd /tmp/lucas_analysis && odtf plan.yaml --max-concurrent 5"
```
Rich paints to the tmux PTY. File logging still captures structured logs via `file_logger`.

Alternatively, redirect to `/dev/null` or use a `--no-live` flag (not implemented).

**Type:** Operational workaround (execution method change).

---

## 12. Submit timeout — `sock_read` too short

**Symptom:** Multiple entries (D_800764/7beq, D_800763/6zhb, D_800766/5ia9) failed during submit with aiohttp timeout errors. The submit POST triggers server-side processing that can take minutes.

**Root cause:** aiohttp's default `sock_read` timeout (~5 min) was too short for the server-side submit processing, especially for larger structures.

**Fix (commit `0433c24`):**

```python
# Before:
timeout = aiohttp.ClientTimeout(total=1800)

# After:
timeout = aiohttp.ClientTimeout(total=3600, sock_read=1800)
```

Applied to both `submit_task()` and `unlock_deposition()`.

**Type:** Code fix in `odtf/cli.py`.

---

## 13. Processing monitor timeout — 30 min too short for EM

**Symptom:** D_800769 (5foj, EM entry) hit "Processing timeout after 30 minutes" in the monitor loop.

**Root cause:** EM entries are processed on the SLURM cluster and routinely take longer than 30 minutes, especially for validation.

**Fix (commit `0433c24`):**

```python
# Before:
async def monitor_processing(..., timeout_minutes=30):

# After:
async def monitor_processing(..., timeout_minutes=120):
```

**Type:** Code fix in `odtf/cli.py`.

---

## 14. Create timeout — REST adapter 600s too short for EM

**Symptom:** D_1292116569 (emd-13292, EM entry) timed out during `create_deposition`. The aiohttp `TimeoutError` was raised.

**Root cause:** `AsyncRestAdapter` default timeout was 600s. EM creates involve building the initial deposition structure, running integrity checks, and setting up archive directories — this can exceed 10 minutes.

**Fix (commit `0433c24`):**

```python
# Before (aioapi.py):
def __init__(self, ..., timeout: int = 600, ...):

# After:
def __init__(self, ..., timeout: int = 1800, ...):
```

**Type:** Code fix in `odtf/aioapi.py`.

---

## 15. EM metadata — `em_map_upload.pkl` missing for non-EM entries

**Symptom:** 14+ entries crashed with `"Contour level or pixel spacing not found in pickle file. Can't continue automatically."` during upload.

**Investigation:**
1. Initial quick fix: changed `raise Exception(...)` to `file_logger.warning(...)` — user correctly pointed out this was wrong.
2. Deeper investigation revealed: `parse_voxel_values(em_map_upload.pkl)` was called **unconditionally** at the top of `_upload_all_files` for every entry, even EC (electron crystallography) entries that have no EM maps at all.
3. The `em_map_upload.pkl` is generated by the legacy deposition GUI when a user uploads a map and enters contour/spacing values. Many production entries never had it.
4. Verified in production: `em_map_upload.pkl` also didn't exist on the prod server for these entries.
5. EC entries only have `model.pdbx` and `structure-factors.mtz` — no map files.

**Root cause:** Two problems:
1. `parse_voxel_values` called unconditionally, even for entries with no EM maps.
2. When the pickle was missing, it raised a hard exception instead of gracefully skipping.

**Fix (commit `81a3758`):**

```python
# Before:
contour_level, pixel_spacing = parse_voxel_values(os.path.join(arch_pickles, "em_map_upload.pkl"))

# After:
em_map_pkl = os.path.join(arch_pickles, "em_map_upload.pkl")
contour_level, pixel_spacing = (None, None)
if os.path.exists(em_map_pkl):
    contour_level, pixel_spacing = parse_voxel_values(em_map_pkl)
```

And for the metadata update block:

```python
# Before:
if contour_level:
    ...
else:
    raise Exception("Contour level or pixel spacing not found...")

# After:
if contour_level and pixel_spacing:
    ...
else:
    file_logger.warning("No em_map_upload.pkl found for %s — ...", test_entry.dep_id)
```

**Design principle (from user):** "If the original deposition in archive has it, then use it. Otherwise, don't."

**Type:** Code fix in `odtf/cli.py`.

---

## 16. "Invalid input file" — empty tempdep directory

**Symptom:** D_1200005582 (5ocv) failed upload with:
```
onedep_deposition.exceptions.DepositApiException: Invalid input file
```
at `aioapi.py` line 529.

**Root cause:** The tempdep directory `/hps/nobackup/pdbe/onedep/data/rhel9_dev/tempdep/D_1200005582/` existed but was **empty**. The source data files were never copied during the rsync from production. `filesystem.locate()` resolved a path, but `os.path.exists(file_path)` returned False.

**Fix:** Data issue — not a code bug. This entry needs to be either:
- Re-synced from production with its actual data files, or
- Excluded from the test plan.

**Type:** Data/infrastructure issue. No code change.

---

## 17. Unlock request returned 500

**Symptom:** Log line: `WARNING - Unlock request returned 500, continuing anyway`

**Root cause:** The unlock endpoint on the server returned HTTP 500 (internal server error). The code already handled this gracefully with a warning and continued.

**Impact:** Non-fatal. The entry continued processing despite the failed unlock.

**Fix:** No fix needed in ODTF — the server-side unlock endpoint needs investigation.

**Type:** Server-side issue. No code change.

---

## 18. `process/` endpoint returns 500 — missing `django_q_ormq` table

**Symptom:** `POST /depositions/{dep_id}/process` returned HTTP 500 for every entry during Run 4. The Apache error log showed:
```
MySQLdb.ProgrammingError: (1146, "Table 'depui_django.django_q_ormq' doesn't exist")
```

**Root cause:** The deposit app v0.63.1 introduced Django-Q2 (`django-q2~=1.6.2`) as a task queue in commit `d9937fce` (June 2025), replacing the previous `threading.Thread` approach in the `process_deposition` view. The `process/` endpoint now calls `async_task()` which enqueues work via the Django ORM broker into the `django_q_ormq` table. However, the required Django-Q2 database migrations had never been applied on the RHEL9 dev server.

**Background:** This change was tracked in a Jira ticket titled *"Use a task queue for file processing in the API process/ endpoint"*. The original `threading.Thread` approach was not thread-safe with Django ORM and caused crashes when querying deposition status during processing. Django-Q2 was chosen over Celery because it uses the database as a broker, avoiding the need for Redis infrastructure at every site.

**Fix:** Applied Django-Q2 migrations:
```bash
python -m wwpdb.apps.deposit.manage migrate
```

This created 17 migration steps for the `django_q` app, including the `django_q_ormq` broker table.

Verified with:
```bash
python -m wwpdb.apps.deposit.manage showmigrations
```
All 17 `django_q` migrations showed `[X]`.

**Type:** Server-side deployment gap. No code change.

**See also:** [Django-Q2 Setup Guide](DJANGO_Q2_SETUP.md) for full setup instructions.

---

## 19. No qcluster worker running — tasks enqueued but never executed

**Symptom:** After fixing the missing table (issue #18), the `process/` endpoint returned 200 and `async_task()` successfully enqueued tasks into `django_q_ormq`. However, depositions remained stuck in processing state indefinitely — no worker was picking up the tasks.

**Root cause:** Django-Q2 requires a separate `qcluster` worker process to consume tasks from the broker queue. This process had never been started on the RHEL9 dev server. There were no systemd units, no supervisor configs, and no running processes:

```bash
ps aux | grep qcluster | grep -v grep
# (empty)

systemctl --user list-units | grep django
# (empty)

find /etc/supervisor* -name '*.conf' 2>/dev/null | xargs grep qcluster
# (empty)
```

**Fix:** Created a systemd user service at `~/.config/systemd/user/djangoq.service`:

```ini
[Unit]
Description=Django-Q2 Worker for OneDep DepUI
After=network.target

[Service]
Type=simple
WorkingDirectory=/nfs/production/gerard/pdbe/onedep/deployments/rhel9_dev
ExecStart=/bin/bash -c '. /nfs/production/gerard/pdbe/onedep/site-config/init/env.sh --siteid PDBE_RHEL9_DEV --location pdbe && /nfs/production/gerard/pdbe/onedep/deployments/rhel9_dev/venv/onedep_venv/bin/python -m wwpdb.apps.deposit.manage qcluster'
StandardOutput=append:/nfs/production/gerard/pdbe/onedep/deployments/rhel9_dev/servers/rhel9_dev/apache-v24/logs/djangoq.log
StandardError=append:/nfs/production/gerard/pdbe/onedep/deployments/rhel9_dev/servers/rhel9_dev/apache-v24/logs/djangoq.log
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

Registered and started:
```bash
systemctl --user daemon-reload
systemctl --user enable djangoq
systemctl --user start djangoq
```

Verified: the cluster started with 6 workers, 1 monitor, 1 guard, and 1 pusher (10 processes total):
```
[INFO] - cluster.start - Q Cluster mockingbird-sodium-early-papa starting.
[INFO] - cluster.guard - Q Cluster mockingbird-sodium-early-papa [DjangORM] running.
```

**Caveat:** `loginctl show-user w3_pdb05` shows `Linger=no`, meaning the service stops when all SSH sessions end. An admin needs to run `sudo loginctl enable-linger w3_pdb05` for persistence.

**Type:** Server-side deployment gap. No code change.

**See also:** [Django-Q2 Setup Guide](DJANGO_Q2_SETUP.md) for full setup instructions.

---

## 20. Deposit app outdated — v0.63.1 → v0.65.1 upgrade

**Symptom:** The deposit app on the RHEL9 dev server was at v0.63.1 (commit `b918bd21`) while the upstream master had advanced to v0.65.1 (commit `77f4b6a8`). This meant the server was missing bug fixes, new features, and the pending migration `main.0007_alter_depositor_full_name`.

**Fix:** Updated the deposit app:
```bash
cd $DEPLOY_DIR/source/py-wwpdb_apps_deposit
git pull       # b918bd21..77f4b6a8, 38 files changed
pip install -e .
python -m wwpdb.apps.deposit.manage migrate  # applied main.0007_alter_depositor_full_name
```

Restarted Apache to pick up the new code:
```bash
$APACHE_PREFIX_DIR/bin/httpd -k graceful -f $TOP_WWPDB_SITE_CONFIG_DIR/apache_config/httpd.conf
```

**Type:** Server-side deployment gap. No code change.

---

## Summary of Commits

| Commit | Description |
|--------|-------------|
| `10072e9` | Update email address in `create_dep_task` |
| `b8c78f0` | Enhance error logging — include response body on API failures |
| `fe57809` | Share single token across concurrent entries |
| `2367900` | Fix `parse_task` for nested YAML keys (`upload`, `compare_files`, `compare_repos`) |
| `254874b` | Workaround: re-link depositor after create; add Bearer token to submit |
| `7272c16` | Wrap Django ORM calls with `sync_to_async` |
| `0433c24` | Increase timeouts: REST adapter 1800s, monitor 120min, submit sock_read 30min |
| `81a3758` | Only read `em_map_upload.pkl` if it exists in source archive |

## Run Results (Run 2, after timeout + EM metadata fixes)

| Outcome | Count |
|---------|-------|
| Submitted successfully | ~6 |
| Contour/pixel spacing missing (hard fail) | 14 |
| Submit timeout | 10 |
| Submit 403 | 4 |
| Create timeout | 4 |
| Processing timeout | 1 |
| Invalid input file (missing data) | 1 |
| **Total processed** | ~25 of 59 |

*Note: Run 2 used the old code (pre-timeout/metadata fixes). Run 3 was started with all fixes deployed.*

## Outstanding Issues (Not ODTF Bugs)

1. **Server-side `updateDepositorTable` bug** — the workaround in ODTF should be removed once `py-wwpdb_apps_deposit` is fixed server-side.
2. **Missing source data** — some entries have empty tempdep directories on RHEL9 dev; needs re-sync from production.
3. **Unlock 500** — server-side endpoint needs investigation (`KeyError: 'message'` in `testviews.py` line 268 when `depositDataSync.sync_single` fails because the source deposit path doesn't exist).
4. **qcluster user lingering** — `Linger=no` on w3_pdb05 means the djangoq systemd user service dies when all SSH sessions end. Needs `sudo loginctl enable-linger w3_pdb05` for persistence.
