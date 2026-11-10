# Automation development setup

Baseline: SelfLearning main `46be142de18823ec86be2e33d8a421ce272616a8`.
Source instructions: [Codex handoff, November 10](codex-handoff-2026-11-10.md).

This tooling prepares an isolated checkout for development. It does not install
anything on Jimmy's M1 or M5 unless the commands are run there. A successful
development check is not a connected service, an operating bot, or permission
to trade.

## Current restrictions

The supplied handoff records a production collision: the `self-learning`
Vercel project publishes main and PR #14 replaced the NFL runtime. Keep main
merges and production deployments blocked until that project's rollback and
production-branch configuration have been verified. Leave the March
`liddar-terminal` project and domain alone.

The profile in `config/automation-readiness.json` is an inventory contract,
not a runner configuration. It fixes SHADOW, disables live trading and
schedules, records $2,000 of intended capital, and leaves the live risk policy
unapproved. No component should treat changing this file as autonomy promotion.
The older $1,500 maximum loss belongs to prior context; it is not approved for
this $2,000 account.

Hosted storage, real application execution and recovery, broker permissions,
and the final live risk policy still need their respective verification or
owner authorization. The offline checker reports these separately and cannot
mark them green.

## One-time setup in a checkout

Requires Python 3.11+ and Git. No administrator account is needed.

```sh
python3 scripts/bootstrap_dev.py
python3 scripts/bootstrap_dev.py --install
.venv/bin/python scripts/automation_readiness.py --require-dev
.venv/bin/python scripts/verify_dev.py
```

To install the optional pinned Vercel, Supabase and Netlify CLIs in the same
checkout, use this single setup command instead (requires Node 22.13+ and npm):

```sh
python3 scripts/bootstrap_dev.py --install --infrastructure
```

It runs the same Python setup, then `npm ci --no-audit --no-fund` and the
version verifier in `tools/automation/infrastructure`. Both installation and
verification output are captured; failures report only a fixed stage and exit
code. It does not log into any provider or deploy anything. Omitting
`--infrastructure` keeps setup Python-only, including existing Python CI.
`--infrastructure` without `--install` remains a plan with no downloads.

The first command only prints its plan. The second explicitly downloads pinned
test/build tools into `.venv` and installs the three existing packages editable,
without their optional live-service dependencies. It does not modify global
Python, log into a provider, or configure a broker. It refuses to overwrite a
non-virtualenv directory at `.venv`. On Windows, use `.venv/Scripts/python.exe`
for the last two commands; this change has been exercised on Linux only.

`requirements-dev.lock` pins the selected tool versions, including build tools.
Package source remains the current checkout. No package secrets are required.
Installer output is captured and not printed, because package index URLs and
errors may contain credentials.

If setup fails, the script identifies the failed stage without echoing its
captured output. Diagnose with the environment's package/network configuration;
do not paste raw credentials or credential-bearing URLs into chat.

## What the JSON reports mean

| Section | What it establishes |
|---|---|
| development | Python/module availability in the interpreter running the command |
| optional_cli_presence | Whether executable names are on PATH; connectors may work without these CLIs |
| credentials | Only whether the explicitly named variables are nonempty; values are never included |
| connected_services | Not checked by the offline inventory |
| application.source_presence | Whether expected files/directories exist; they can still be stubs |
| application verification fields | Not tested by the inventory |
| production_and_live | Blocked; this tool never authorizes trading or scheduling |

No .env file is loaded automatically. `.env.example` is documentation for
variable names only. It is not a shell script or a place to store real secrets.
Use the runtime's secret manager or approved environment settings.

`scripts/verify_dev.py` runs the three regression suites, the walk-forward
smoke, and `tests/tooling`. It removes account/API/deployment credential
variables from child environments and prints only fixed check names, return
codes and parsed test counts. This proves fixture/unit behavior, not hosted
storage, broker access, a deployed runtime, or full options support. To diagnose
a failure, rerun its named test suite in this checkout after ensuring that live
credentials are absent.

## CI and sharing status

For Alpaca paper configuration, run the separate offline preflight:

```sh
.venv/bin/python scripts/alpaca_paper_preflight.py
```

Only after paper credentials are available through the approved environment,
an explicit read-only connection test is available:

```sh
.venv/bin/python scripts/alpaca_paper_preflight.py --check-paper
```

That opt-in performs one GET to the fixed Alpaca paper account endpoint. It
refuses redirects and has no order, cancel, configurable URL or live mode.
It reports only fixed statuses, never account identifiers, balances or raw
error bodies. Paper connectivity still does not prove an end-to-end bot or
options execution. Missing credentials are expected until the account is set up.

Existing CI continues to run all three package suites. The added tooling job
uses the same pinned setup and checks the offline tooling on pull requests.
Manual dispatch is declared without any schedule; GitHub may not expose a new
dispatch entry until its workflow definition is on the default branch. Do not
merge merely to activate the button while the production collision is unresolved.

Codex should use feature branches and reviewable PRs. Grace can interpret the
JSON, PR status and CI results when she has access to them. This repository does
not create an automatic handoff between products or install a Grace connector.

Recommended exception report: exact blocked component, observed evidence,
what remains safe to do, and the smallest required owner action. Routine
dependency setup and fixture tests need no repeated approval once authorized.

## Next implementation work

The [Codex and Grace handoff](codex-grace-next-steps.md) gives the owner setup
sequence and starter tasks. The [Supabase candidate](../tools/automation/supabase/README.md)
contains an unapplied, fresh-project-only schema and read-only probes. It is
kept outside the root migration runner. CI executes its disposable SQL tests
and Python checker tests without hosted credentials.

The source handoff's six bot tasks remain separate from this tooling change.
In particular, no reconciliation engine, options odds engine, model provider,
trading runner, hosted-store extraction, live adapter, or trading schedule is
created here. Continue those tasks on scoped branches using their acceptance
criteria and the established account permissions.
