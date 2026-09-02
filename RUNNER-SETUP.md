# Machine Setup for Self-Hosted Runner

The monthly snapshot workflow runs on a self-hosted GitHub Actions runner rather than GitHub's hosted runners. This document explains why, how to set it up, and how to manage it.

## Table of Contents

- [Why Self-Hosted?](#why-self-hosted)
- [How the Pipeline Works](#how-the-pipeline-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Verification](#verification)
- [Triggering a Run](#triggering-a-run)
- [When a Run Fails](#when-a-run-fails)
- [Scheduled Run Behavior](#scheduled-run-behavior)
- [Security Constraints](#security-constraints)
- [Troubleshooting](#troubleshooting)
- [Maintenance](#maintenance)
- [Uninstallation / Reconfiguration](#uninstallation--reconfiguration)
- [When to Use GitHub-Hosted Runners Instead](#when-to-use-github-hosted-runners-instead)

## Why Self-Hosted?

The monthly pipeline has two distinct phases:

1. **SEC-based ingestion**: Form 4 filings, shares outstanding, ticker fixes — works fine on GitHub-hosted runners
2. **Yahoo Finance data**: Stock prices, market caps, splits, benchmark ETFs — **blocked on GitHub-hosted runners**

Yahoo Finance blocks GitHub's IP ranges at the network level. In one run, the workflow hit 5,410 rate-limit errors, updated 0 of 3,844 market caps, and left prices/splits/benchmarks as no-ops. The same calls succeed in their thousands from a residential IP.

Rather than work around the block (proxies, API services, etc.), we run the workflow on a machine with an IP Yahoo accepts — typically the repo owner's Mac. SEC-based steps continue to work because the SEC does not block GitHub.

## How the Pipeline Works

Each workflow run **seeds from the previous GitHub release** rather than rebuilding from scratch. This is much faster (5-15 minutes instead of 45-60 minutes) and avoids re-fetching ~1.8 million Form 4 filings every month.

The workflow:
1. Downloads the database from the most recent `data-YYYY-MM` release (the compressed `.xz` file)
2. Decompresses it to `data/insider_signals.db`
3. Runs an incremental refresh: ingests only new Form 4 filings since the last build, updates prices/shares/market-caps, recomputes signals
4. Validates the updated database against data-quality contracts
5. If validation passes, compresses and publishes a new release; if it fails, the old release stays in place

This means:
- **A stale or corrupt prior release affects the next run.** If the previous month published bad data, the next run seeds from that bad data. Use `force_full_rebuild=true` to seed from scratch instead.
- **No published release means the next run starts from scratch.** If this is the first run ever, or if all prior releases were deleted, the workflow falls back to initializing an empty database and running the full pipeline.

The incremental approach is the default because it is much cheaper and faster, but it relies on the previous release being correct.

## Prerequisites

Before installing the runner:

- **Python 3.11+**: The pipeline requires Python 3.11 or later
- **15 GB free disk space**: The database is ~2 GB uncompressed (~200 MB compressed as `.xz`), plus workspace for the runner's checkout, build artifacts, and overhead
- **GitHub CLI (`gh`)**: Authenticated with admin access to this repo (for automatic token generation)
- **macOS or Linux**: The installer supports macOS (arm64/x64) and Linux (x64/arm64)

Check your Python version:
```bash
python3 --version
```

Install and authenticate the GitHub CLI if you haven't already:
```bash
brew install gh
gh auth login
```

## Installation

From the repository root:

```bash
bash scripts/install-runner.sh
```

The installer will:
1. Check prerequisites (Python, disk space, gh authentication)
2. Download the latest GitHub Actions runner for your OS and architecture
3. Generate a registration token (or prompt you for one if `gh` is unavailable)
4. Register the runner with the label `insider-signals` (plus the default `self-hosted`)
5. Install it as a service (launchd on macOS, systemd on Linux)
6. Start the service and verify it is running

**Installation log**: `logs/install-runner.log`

## Verification

The runner is installed to `$HOME/.github-runner/orioldc/stock-valuation-insider-signals` by default. If you set `RUNNER_HOME` before installation, adjust paths below accordingly.

1. **Check the runner appears in GitHub**:
   Go to https://github.com/orioldc/stock-valuation-insider-signals/settings/actions/runners
   
   You should see a runner named `<hostname>-insider-signals` with status "Idle" and labels `self-hosted, insider-signals, <OS>, <arch>`.

2. **Check the service status**:
   ```bash
   cd "$HOME/.github-runner/orioldc/stock-valuation-insider-signals"
   ./svc.sh status
   ```

3. **View runner logs** (if needed):
   ```bash
   tail -f "$HOME/.github-runner/orioldc/stock-valuation-insider-signals/_diag/Runner_"*.log
   ```

## Triggering a Run

The workflow runs automatically at 06:00 UTC on the 1st of each month (when the machine is awake). You can also trigger it manually on demand:

### Via GitHub CLI (recommended)

```bash
gh workflow run monthly-snapshot.yml --ref main
```

To specify optional inputs:

```bash
gh workflow run monthly-snapshot.yml --ref main \
  -f tag_suffix="-hotfix" \
  -f force_full_rebuild=true
```

### Via GitHub UI

1. Go to https://github.com/orioldc/stock-valuation-insider-signals/actions/workflows/monthly-snapshot.yml
2. Click **Run workflow** (green button in the top-right)
3. Leave branch as `main`
4. Optionally configure inputs (see below)
5. Click **Run workflow** (green button in the dialog)

### Workflow Inputs

All inputs are optional:

- **`tag_suffix`**: Append a suffix to the release tag (e.g., `-hotfix` produces `data-2026-09-hotfix` instead of `data-2026-09`). Use this when publishing multiple releases in the same month.
- **`force_full_rebuild`**: Rebuild the database from scratch instead of seeding from the previous release. This is slow (~45-60 minutes) and should only be used when the prior release is corrupted or the schema changed. The default incremental path is much faster (~5-15 minutes).
- **`backfill_current_quarter`**: Force a full-universe backfill of the current quarter's Form 4 filings via live XML fetch. This is very slow (~90-120 minutes) and is normally only triggered automatically when the workflow detects data volume is incomplete. Use this if you suspect missing filings that the automatic check did not catch.

### Watching the Run

After triggering, watch progress with:

```bash
# List recent runs
gh run list --workflow=monthly-snapshot.yml

# View a specific run (use the ID from the list above)
gh run view <run-id>

# Follow the run live (updates every few seconds)
gh run watch <run-id>
```

Alternatively, open the run in your browser:
```bash
gh run view <run-id> --web
```

### What to Expect

- **Duration**: 5-15 minutes for incremental refresh (the default), 45-60 minutes for full rebuild, 90-120 minutes if backfilling the current quarter.
- **Concurrency**: The workflow uses queue mode. If you trigger a second run while one is already running, the new run will wait rather than cancelling the first. Both will complete in sequence.
- **Output**: If the run succeeds, a new GitHub release tagged `data-YYYY-MM` (plus any suffix you specified) will appear with the compressed database and supporting files as assets.

## When a Run Fails

The workflow includes a validation gate that blocks publication if critical data-quality checks fail. A blocked run is expected behavior, not an emergency — the gate exists to prevent publishing a broken database.

### What Happens When Validation Fails

- The workflow run will show as **failed** (red X) at the "Validate data contract" step
- **No release is published** — the previous month's release remains in place and continues to serve installs
- The validation report is uploaded as an artifact so you can see what failed

This is the intended behavior. A failed validation means the database was not good enough to publish, and the previous known-good release stays live rather than being replaced with bad data.

### Diagnosing the Failure

Every workflow run uploads a validation report, whether it passed or failed. To download it:

```bash
# List recent runs to find the failed run's ID
gh run list --workflow=monthly-snapshot.yml

# Download the validation report artifact
gh run download <run-id> -n validation-report
```

This creates a `validation-report/` directory with two files:
- `validation.txt` — human-readable report with pass/fail for each check
- `validation.json` — machine-readable version with measured vs. expected values

### Reading the Report

Open `validation.txt` and look for lines marked **CRITICAL** or **WARN**:

- **CRITICAL**: Must pass for publication to proceed. A CRITICAL failure blocks the release and fails the workflow.
- **WARN**: Informational; does not block publication.

Each failing check shows:
- What was checked (e.g., "Minimum transaction count for current year")
- The measured value (e.g., "Found 45,203 transactions")
- The expected value (e.g., "Expected ≥50,000")

Example of a failing check:

```
[CRITICAL] ❌ base_rates.tables_exist
  → Missing required tables: base_rate_model, base_rate_metadata
```

### Fixing and Re-running

1. **Identify the root cause** from the validation report. Common failures:
   - Missing or incomplete data (Form 4 ingestion failed, API rate limits hit)
   - Database schema mismatch (manual schema edits, migration not run)
   - Stale or corrupt seed data (previous release was bad, or download failed)

2. **Fix the underlying issue**. This might mean:
   - Re-running the workflow with `force_full_rebuild=true` to seed from scratch
   - Manually running a specific backfill script on the runner's copy of the database
   - Fixing a bug in the ingestion code and pushing the fix to `main`

3. **Trigger a new run** (see "Triggering a Run" above).

The workflow is idempotent — you can re-run it as many times as needed. Each run seeds from the previous published release, so a failed run does not corrupt the next attempt.

### Common Validation Failures

**Missing base-rate tables**: The `train_base_rates.py` step failed. Check that step's log output in the workflow run. Often caused by insufficient historical data or a Python error during training.

**Low transaction count**: Form 4 ingestion is incomplete. The workflow will usually auto-trigger a current-quarter backfill if this happens, but you can force one with `backfill_current_quarter=true`.

**Missing price data**: The Yahoo Finance fetch failed (rate limits, network issues). These steps are marked `continue-on-error: true`, so they log warnings but don't fail the workflow. Price-related validation checks are typically WARN, not CRITICAL.

## Scheduled Run Behavior

The workflow is scheduled to run at 06:00 UTC on the 1st of every month.

**What happens if the machine is asleep or offline?**

GitHub queues the workflow run for self-hosted runners. When the machine wakes or comes online and the runner service starts, the queued run executes. This is different from GitHub-hosted runners, which fail immediately if unavailable.

In practice: if your machine is asleep at the scheduled time, the workflow will run when you wake it. The database will be slightly delayed but not skipped.

## Security Constraints

### No `pull_request` Trigger

**CRITICAL**: The monthly-snapshot workflow must never have a `pull_request` trigger. The workflow file includes a prominent warning comment to prevent accidental addition.

**Why?**

- This repository is public.
- The workflow runs on a self-hosted runner (the owner's machine).
- A `pull_request` trigger would allow **any fork** to propose a change that, when a PR is opened, executes arbitrary code on the owner's machine.
- `workflow_dispatch` and `schedule` triggers are safe because they only run code from the main branch, which is protected.

If you add a `pull_request` trigger, you are giving strangers code execution on your machine. Do not do this.

### Runner Isolation

The self-hosted runner:
- Uses the repository's existing Python environment and dependencies
- Writes to the repository's `data/` directory
- Has the same filesystem access as your user account

This is acceptable for a single-owner private workflow but would be unacceptable for a multi-tenant or public-contribution scenario. The runner is configured with the custom label `insider-signals` so it cannot be used by other workflows or repositories.

## Troubleshooting

### Runner Not Appearing in GitHub

- Check that `gh auth status` shows you are authenticated
- Verify you have admin access to the repository
- Check the log at `logs/install-runner.log` for errors

### Service Not Starting

On macOS:
```bash
launchctl list | grep actions.runner
```

On Linux:
```bash
systemctl --user status actions.runner.*
```

If the service is not running, check the diagnostics logs:
```bash
tail -f "$HOME/.github-runner/orioldc/stock-valuation-insider-signals/_diag/Runner_"*.log
```

### Workflow Still Failing with Rate Limits

If the workflow is still hitting Yahoo Finance rate limits even on the self-hosted runner:
1. Verify the workflow is actually using the self-hosted runner (check the run logs for the runner name)
2. Check that your IP is not being blocked for other reasons (VPN, corporate network, etc.)
3. The workflow steps are marked `continue-on-error: true` for non-critical data; failures in prices/splits/market-caps are logged but do not block publication

### Disk Space

The runner's work directory is `$HOME/.github-runner/orioldc/stock-valuation-insider-signals/_work`. Each workflow run downloads the repository and builds the database in this workspace. Old run artifacts are cleaned up automatically, but if you run low on disk space, you can manually clean the work directory:

```bash
rm -rf "$HOME/.github-runner/orioldc/stock-valuation-insider-signals/_work"/*
```

The database itself lives in the repository's `data/insider_signals.db` (not in the runner's work directory) and is reused across runs.

## Maintenance

### Updating the Runner Software

GitHub periodically releases new runner versions. The installer always fetches the latest version, so to upgrade:

```bash
bash scripts/install-runner.sh
```

This will reconfigure the runner with the latest software. Alternatively, the runner auto-updates itself when it detects a new version is available (you will see update messages in the runner logs).

### Changing the Runner Name or Labels

Re-run the installer. It will detect the existing configuration and offer to reconfigure, which includes deregistering the old runner and registering a new one.

### Moving to a Different Machine

1. Uninstall the runner on the old machine:
   ```bash
   bash scripts/install-runner.sh --uninstall
   ```

2. Install the runner on the new machine:
   ```bash
   bash scripts/install-runner.sh
   ```

Only one runner with the `insider-signals` label should be active at a time. The workflow's concurrency group ensures that if two runners somehow both pick up the job, they will not run concurrently.

## Uninstallation / Reconfiguration

### Uninstallation

To stop and remove the runner:

```bash
bash scripts/install-runner.sh --uninstall
```

This will:
1. Stop the service
2. Remove the service from launchd/systemd
3. Deregister the runner from GitHub
4. Delete the runner directory (`$HOME/.github-runner/orioldc/stock-valuation-insider-signals` by default)

### Reinstallation / Reconfiguration

The installer is idempotent and safe to re-run. If the runner is already configured:
- By default, it will **reconfigure** (stop the service, deregister, re-register, restart)
- With `--skip-config`, it will skip reconfiguration (useful after runner software upgrades)

## When to Use GitHub-Hosted Runners Instead

If Yahoo Finance lifts its IP-based block or if you migrate to a different data source that works from GitHub's IPs, you can switch back to GitHub-hosted runners by changing:

```yaml
runs-on: [self-hosted, insider-signals]
```

to:

```yaml
runs-on: ubuntu-latest
```

And removing the concurrency block (GitHub-hosted runners are ephemeral and do not need concurrency control).

The self-hosted runner setup is a workaround for a specific network-level block, not a permanent architecture decision.
