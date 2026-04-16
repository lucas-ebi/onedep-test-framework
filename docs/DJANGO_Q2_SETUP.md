# Django-Q2 Setup Guide for OneDep DepUI

## Background

The OneDep deposit API's `process/` endpoint requires a background task queue to handle deposition processing asynchronously. Previously, processing was done in a spawned `threading.Thread`, but since Django ORM is not thread-safe, this caused internal server errors when querying deposition status during processing.

Django-Q2 (`django-q2~=1.6.2`) was introduced as a lightweight task queue that uses the database (via Django ORM) as a broker — avoiding the need for Redis or other external message brokers. It was added to `wwpdb.apps.deposit` in v0.63.1 (commit `d9937fce`, June 2025).

> **Reference**: Jira ticket *"Use a task queue for file processing in the API process/ endpoint"*

## Prerequisites

- `wwpdb.apps.deposit` >= v0.63.1 (ships `django-q2~=1.6.2` as a dependency)
- OneDep environment sourced via `env.sh`
- Access to the OneDep MySQL database used by the DepUI Django app

## Step 1 — Update the Deposit App

Pull the latest version and install it:

```bash
cd $DEPLOY_DIR/source/py-wwpdb_apps_deposit
git pull
pip install -e .
```

Verify the installed version:

```bash
pip show wwpdb.apps.deposit | grep Version
# Version: 0.65.1
```

## Step 2 — Apply Database Migrations

Django-Q2 requires 17 migration steps to create its ORM broker tables (`django_q_ormq`, `django_q_task`, `django_q_schedule`, etc.):

```bash
python -m wwpdb.apps.deposit.manage migrate
```

Expected output:

```
Operations to perform:
  Apply all migrations: auth, contenttypes, depui, django_q, main, onedep_auth, sessions, sites
Running migrations:
  Applying django_q.0001_initial... OK
  Applying django_q.0002_auto_20150630_1624... OK
  ...
  Applying django_q.0017_task_cluster_alter... OK
```

> **Note**: If migrations were already partially applied, only the remaining ones will run.

## Step 3 — Verify Migrations

```bash
python -m wwpdb.apps.deposit.manage showmigrations
```

The `django_q` section should show all 17 migrations applied:

```
django_q
 [X] 0001_initial
 [X] 0002_auto_20150630_1624
 [X] 0003_auto_20150708_1326
 [X] 0004_auto_20150710_1043
 [X] 0005_auto_20150718_1506
 [X] 0006_auto_20150805_1817
 [X] 0007_ormq
 [X] 0008_auto_20160224_1026
 [X] 0009_auto_20171009_0915
 [X] 0010_auto_20200610_0856
 [X] 0011_auto_20200628_1055
 [X] 0012_auto_20200702_1608
 [X] 0013_task_attempt_count
 [X] 0014_schedule_cluster
 [X] 0015_alter_schedule_schedule_type
 [X] 0016_schedule_intended_date_kwarg
 [X] 0017_task_cluster_alter
```

## Step 4 — Start the Q Cluster Workers

The qcluster workers must be managed as a systemd user service. This keeps the workers running across SSH sessions, provides automatic restart on failure, and places logs alongside Apache logs for easy access.

### 4.1 — Create the service file

Create `~/.config/systemd/user/djangoq.service` with paths adapted to your site. The example below is for PDBe RHEL9 dev:

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

Adapt the following values for your site:

| Placeholder | Description | Example |
|---|---|---|
| `WorkingDirectory` | `$DEPLOY_DIR` | `/nfs/production/gerard/pdbe/onedep/deployments/rhel9_dev` |
| `--siteid` | `$WWPDB_SITE_ID` | `PDBE_RHEL9_DEV` |
| `--location` | `$WWPDB_SITE_LOC` | `pdbe` |
| Python path | Venv python binary | `$DEPLOY_DIR/venv/onedep_venv/bin/python` |
| Log path | Apache logs directory | `$APACHE_PREFIX_DIR/logs/djangoq.log` |

You can find these values by sourcing `env.sh` and running:

```bash
echo "WWPDB_SITE_ID=$WWPDB_SITE_ID"
echo "WWPDB_SITE_LOC=$WWPDB_SITE_LOC"  # same as wwpdb_site_loc in site.cfg
echo "DEPLOY_DIR=$DEPLOY_DIR"
echo "APACHE_PREFIX_DIR=$APACHE_PREFIX_DIR"
which python
```

### 4.2 — Register and start the service

```bash
mkdir -p ~/.config/systemd/user
# (create the service file as above)
systemctl --user daemon-reload
systemctl --user enable djangoq
systemctl --user start djangoq
```

### 4.3 — Verify

```bash
systemctl --user status djangoq
```

Expected output:

```
● djangoq.service - Django-Q2 Worker for OneDep DepUI
     Loaded: loaded (~/.config/systemd/user/djangoq.service; enabled; preset: disabled)
     Active: active (running) since Thu 2026-04-16 16:14:20 BST; 11s ago
   Main PID: 2110592 (python)
      Tasks: 10 (limit: 100386)
     Memory: 151.7M
```

You should also see the cluster name and workers in the log:

```bash
tail -20 $APACHE_PREFIX_DIR/logs/djangoq.log
```

```
[INFO] - cluster.start - none - Q Cluster mockingbird-sodium-early-papa starting.
[INFO] - worker.worker - none - Process-f9ab23afde67436481fbf609aa1198a6 ready for work at 2110617
[INFO] - worker.worker - none - Process-74fee732ffea4148ae606e7f24e64086 ready for work at 2110618
[INFO] - worker.worker - none - Process-9070ad134e8744cbb6d9532fa5bee07c ready for work at 2110619
[INFO] - worker.worker - none - Process-c5b246fa5f9a4f45bc8be8b2d766b8f5 ready for work at 2110620
[INFO] - worker.worker - none - Process-c7ccff20b25e4087b7ff9027aebe6e33 ready for work at 2110621
[INFO] - worker.worker - none - Process-00702ba9467c4c8b93668d80b6954ea2 ready for work at 2110622
[INFO] - monitor.monitor - none - Process-f747cefef21a4b8986ae53e0a226aa77 monitoring at 2110623
[INFO] - cluster.guard - none - Process-3f60fda09ee64a318f0f3070b9a17c86 guarding cluster mockingbird-sodium-early-papa [DjangORM]
[INFO] - pusher.pusher - none - Process-28e1e49d35d54c899ea53b30780d00c6 pushing tasks at 2110624
[INFO] - cluster.guard - none - Q Cluster mockingbird-sodium-early-papa [DjangORM] running.
```

A healthy cluster spawns ~10 processes: 6 workers, 1 monitor, 1 guard, 1 pusher, and the main process.

### 4.4 — Managing the service

```bash
systemctl --user start djangoq
systemctl --user stop djangoq
systemctl --user restart djangoq
systemctl --user status djangoq
journalctl --user -u djangoq -f    # live log stream
```

### 4.5 — Enable user lingering

By default, systemd user services are killed when all sessions for that user end. To keep the service running after logout:

```bash
# Requires root/admin privileges:
sudo loginctl enable-linger <username>

# Verify:
loginctl show-user <username> | grep Linger
# Linger=yes
```

Without lingering enabled, the qcluster will stop when you disconnect. This must be addressed before the service can be considered operational.

## Step 5 — Restart Apache

After updating the deposit app, Apache must be restarted so `mod_wsgi` loads the new code:

```bash
$APACHE_PREFIX_DIR/bin/httpd -k graceful -f $TOP_WWPDB_SITE_CONFIG_DIR/apache_config/httpd.conf
```

Or use the shell alias (available after sourcing `env.sh`):

```bash
restart_my_apache
```

## Operations Checklist

When deploying a new version of `wwpdb.apps.deposit`:

1. Pull and install the new version (`git pull && pip install -e .`)
2. Run migrations (`python -m wwpdb.apps.deposit.manage migrate`)
3. Restart qcluster (`systemctl --user restart djangoq`)
4. Restart Apache (`restart_my_apache` or `httpd -k graceful ...`)

## Troubleshooting

### `process/` endpoint returns 500

Check if the `django_q_ormq` table exists:

```bash
python -m wwpdb.apps.deposit.manage showmigrations | grep -A20 django_q
```

If migrations are not applied (`[ ]` instead of `[X]`), run `python -m wwpdb.apps.deposit.manage migrate`.

### Tasks enqueued but never executed

The qcluster worker is not running:

```bash
ps aux | grep qcluster | grep -v grep
```

If empty, start the service:

```bash
systemctl --user start djangoq
```

### Service dies after logout

`Linger=no` — see [Section 4.5](#45--enable-user-lingering).

### `env.sh` flags

The `env.sh` script requires `--siteid` and `--location` (or `-h`/`--host`) to configure the OneDep environment. Find the correct values from your `site.cfg`:

```
site_prefix = PDBE_RHEL9_DEV    →  --siteid PDBE_RHEL9_DEV
wwpdb_site_loc = pdbe           →  --location pdbe
```

## Architecture Notes

- **Broker**: Django ORM (database-backed, no Redis needed)
- **Dependency**: `django-q2~=1.6.2` (compatible with Django 3.2 and Python 3.8+)
- **Workers**: Default 6 worker processes per cluster
- **Where it's used**: Only the API `process/` endpoint (`async_task()` in `depositions.py`)
- **Future**: When OneDep moves to Django 4+, native async support or `django-q2` >= 1.7 may replace this setup
