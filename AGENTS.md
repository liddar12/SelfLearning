# Automated Trade Bot: agent working instructions

This repository is the shared backend for the investing bot and other projects.
The terminal is a separate repository: `liddar12/liddar-terminal`.

## Read before work

1. The current user request.
2. `CLAUDE.md` for the established architecture and hard constraints.
3. `docs/codex-handoff-2026-11-10.md` for the source handoff.
4. `docs/automation-setup.md` for reproducible tooling and current restrictions.

The current user request takes precedence over older proposed decisions.
The account context is $2,000. The historical $1,500 trade-loss figure is not
an approved risk policy for this account.

## Scope and operating discipline

- Work on a feature branch. Open a PR; never push directly to main.
- Do not merge or deploy SelfLearning until the handoff's Vercel production
  collision has a verified resolution. Preserve the NFL release branch.
- Tooling is SHADOW only. Do not enable AUTO, submit broker orders, add cron
  schedules, or provision/alter accounts as a side effect of setup.
- Finish authorized routine development and tests without asking about each
  implementation choice. If a genuine owner action is necessary, prepare the
  reviewable result first and identify the exact remaining blocker.
- Never inspect or print secret values. Do not source a .env file into a shell.
  Credentials enter only through an approved secret store or environment.
- Keep model output separate from deterministic quantity and risk limits.
  No log means no new exposure; protective exits need their own explicit policy.
- Preserve shared sports code and breadth parity. Do not merge the NFL release
  branch wholesale to obtain individual generic modules.

## Commands

```sh
python3 scripts/bootstrap_dev.py             # offline plan only
python3 scripts/bootstrap_dev.py --install   # explicit package installation
python3 scripts/bootstrap_dev.py --install --infrastructure  # optional pinned provider CLIs
.venv/bin/python scripts/automation_readiness.py --require-dev
.venv/bin/python scripts/verify_dev.py
```

The readiness command is an offline inventory. It never establishes that
services are connected or that a trading application is ready. The regression
command runs fixture/unit tests and withholds raw subprocess output.

## Handoff between Codex and Grace

Use GitHub branches, PRs, CI evidence and the structured readiness output as the
shared record. Do not assume an automatic Grace-to-Codex dispatch integration.
Report four separate facts: installed dependencies, authenticated service
connectivity, tested application behavior, and production/live eligibility.
Missing credentials are a configuration blocker, not a reason to invent data.
