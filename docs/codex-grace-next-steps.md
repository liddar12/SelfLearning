# Next steps for Codex, Grace, and this chat

Prepared November 10, 2026. This guide uses the reviewed handoff and the setup
branches below. It does not authorize live trades or change account permissions.

- Backend: `liddar12/SelfLearning`, branch `codex/automation-tooling-shadow`.
- Terminal: `liddar12/liddar-terminal`, branch `codex/terminal-owner-auth`.
- Account context: $2,000. The older $1,500 trade-loss figure is not an approved
  policy for this account.

## Updated owner checklist after Vercel consent

The Vercel team grant now works. Authenticated reads confirm the intended
`self-learning` and `liddar-terminal-2tpb` projects. The current SelfLearning
production aliases point at the newer main build; the prior NFL production
deployment from the handoff is READY and belongs to the expected release branch.
No production rollback or branch-setting change has been performed.

| Step | Owner intervention | Work delegated to the agents |
|---|---|---|
| Vercel team consent | Completed and verified | Inspect deployments and logs through the connected tool |
| SelfLearning production branch | In the existing Vercel project, open Settings → Environments → Production → Branch Tracking; set `release/optimized-nfl-runtime-20261018` if different, save, and confirm the saved value | Verify restoration of the intended runtime and production aliases; branch tracking and rollback are separate changes |
| Supabase access | Connect the personal account/organization owning the intended project; current access still exposes only AYSO | Inspect schema, isolation and grants; prepare and validate the appropriate database setup |
| Codex environment | Select both repositories in Grace's actual ChatGPT account/workspace and publish the tested environment | Install tools and reproduce the checks using the committed setup |
| Grace handoff | Give Grace the coordination prompt once and identify the published environment | Prove a delegated Codex task returns, then manage routine engineering and exceptions |
| Private credentials and owner login | Complete provider OAuth, secret entry, and owner password/email verification when required | Configure references and run secret-safe connectivity/integration checks |
| Live operating policy | Later: select the funded broker/account and approve instruments, position/loss limits and the live stage | Prepare a concrete policy and evidence from shadow/paper operation; enforce it deterministically |

The connected Vercel update-project tool does not expose production branch
tracking, and the cloud dashboard browser is signed out. The documented UI
location above is the smallest current owner action; no raw credential is
needed in chat. The terminal's preview build compatibility fix belongs to
the coding task and requires no owner key change.

Use free services where suitable and keep new automation spending within
the owner's existing $50/month additional budget across automation projects.
Do not treat that operations budget as authority to fund or trade an account.

## 1. Who does what

| Role | Next responsibility |
|---|---|
| Codex | Run setup, test both branches, implement scoped engineering tasks, and produce reviewable PRs with evidence. |
| Grace | Coordinate the backlog, delegate to Codex once her environment access works, follow up on tasks, and present decisions or exceptions. |
| This chat | Help connect the correct accounts, verify the reported deployment problem, review the architecture and results, and prepare concrete owner decisions. |
| External worker, once built | Run the actual trading application and its deterministic controls. A chat conversation is not the worker. |
| Jimmy | Complete private account consent, select the correct database/hosting account and any paid tier, and eventually authorize the live operating policy. |

The official [Dot task documentation](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
describes Codex delegation when an appropriate Cloud environment is available.
Grace's access to that environment has not been demonstrated in this session.
First prove one delegated task and returned result; then use that connection
for routine engineering. See [Cloud environment setup](https://learn.chatgpt.com/docs/environments/cloud-environments).

## 2. Publish the reusable Codex environment once

In the signed-in account used by Grace, publish or select a Cloud environment
with access to **both** GitHub repositories. It needs Python 3.11+, **Node
22.13+ within Node 22.x**, npm, Git, and the package-download access needed by
the lockfiles and Playwright. Node 22.x matches the terminal's deployment
engine. Use the environment's supported setup controls; do not assume the
tools installed in this chat are installed on another host.

Give Codex the setup prompt in section 7. Codex should run the commands;
Jimmy should not have to relay every command or log.

The following creates predictable sibling checkouts in a writable workspace.
Use it **after the two prepared branches have been published**. If the
environment already contains these repositories, Codex should locate them,
preserve local work, and select the prepared branches instead of making
duplicate checkouts. Do not silently fall back to main if a branch is missing.

```sh
mkdir -p automated-trade-bot
cd automated-trade-bot
git clone --single-branch --branch codex/automation-tooling-shadow https://github.com/liddar12/SelfLearning.git SelfLearning
git clone --single-branch --branch codex/terminal-owner-auth https://github.com/liddar12/liddar-terminal.git liddar-terminal

cd SelfLearning
python3 scripts/bootstrap_dev.py --install --infrastructure
.venv/bin/python scripts/automation_readiness.py --require-dev
.venv/bin/python scripts/verify_dev.py
npm run --prefix tools/automation/infrastructure verify

cd ../liddar-terminal
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:browser
```

The terminal browser test uses mocked provider responses. It proves the
implemented sign-in flow under fixtures, not a connection to the owner's real
Supabase account. If Chromium needs operating-system dependencies, use the
Cloud environment's supported package setup; do not weaken browser or network
security to get a green result.

The backend setup installs pinned Python tools and the optional pinned
Netlify, Vercel and Supabase CLIs. It does not log into those services. It
creates no trading schedule and places no orders.

## 3. Prepared tools and authenticated accounts are different

The backend's `.codex/config.toml` contains project-scoped MCP definitions for
trusted local Codex clients. Registration is not OAuth consent. It does not
install plugins into Grace's account or establish Codex Cloud connectivity.

For a **trusted local Codex client**, from SelfLearning:

```sh
codex mcp list
codex mcp login vercel
codex mcp login supabase
codex mcp login netlify
```

Only authenticate services that are needed. Complete provider consent
privately. Grace/ChatGPT plugins and the selected Cloud environment need their
own verified account connections. Never paste access tokens, passwords, secret
keys, or OAuth callback URLs into chat or a PR.
See [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp).

| Connection | Exact owner action still needed |
|---|---|
| GitHub and Cloud | Grant access to both repositories and make the selected environment available to Grace/Codex. |
| Vercel | Team access is now verified. Complete the production Branch Tracking dashboard step above; the runtime restoration still needs verification. |
| Supabase | Connect the account/organization owning the intended learning project, reported as `omavgimpnvrpuxqhfxpf`, then verify the actual project. Do not substitute AYSO. |
| Terminal owner sign-in | Provision the owner's verified Supabase Auth account privately, disable public signup, and set its immutable UUID as `TERMINAL_OWNER_USER_ID` on the server. Follow the terminal's `docs/owner-auth-setup.md`. |
| Alpaca paper | Supply paper-only `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` through the approved secret mechanism, if Alpaca is the selected paper broker. |
| Model providers | Choose the provider/account and provide runtime credentials privately. Actual model IDs and entitlements must be verified before calls. |
| Netlify | Complete OAuth and identify a site only if Netlify is used. No duplicate deployment or site migration is required now. |

Schwab developer OAuth remains separate work. Robinhood's prepared MCP entry
is disabled, and enabling it would not implement this bot's risk engine or
multi-leg options support. No Fidelity execution connection was established.

After the paper credentials are available, this is an explicitly read-only
connection check from SelfLearning:

```sh
.venv/bin/python scripts/alpaca_paper_preflight.py --check-paper
```

It performs one GET against the fixed paper account endpoint, submits no
orders, and does not print account or financial values. Its result does not
establish paper-strategy performance or options readiness.

## 4. Resolve the production problem before merging

**Do not merge either setup branch to main until the reported Vercel
production collision has been verified and repaired.**

Authenticated Vercel reads confirm that SelfLearning's production aliases now
point to the main build `dpl_8gTsvAs91dar9ss9jJX9Ay7y9J8q`. The prior deployment
`dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3` is READY, belongs to the same project, and was
built from `release/optimized-nfl-runtime-20261018` at `aacb19c`.
The dashboard Branch Tracking setting is not exposed by the connected tool;
use the owner step above. Inspect runtime behavior before restoring the verified
intended deployment, correct the routing, then observe the actual result.
An Instant Rollback disables automatic production-domain assignment; a later
promotion reenables it. Do not silently add that promotion as cleanup.

Primary references: [production branch configuration](https://vercel.com/docs/git#customizing-the-production-branch)
and [Instant Rollback](https://vercel.com/docs/instant-rollback).

Use the terminal target `liddar-terminal-2tpb`. Leave the original March
`liddar-terminal` project and `liddar-terminal.vercel.app` domain alone.
Do not merge merely to expose a manual workflow-dispatch button.

## 5. Database candidate: prepared, unapplied, fresh-project only

`tools/automation/supabase` contains a **candidate for an empty dedicated
database**, read-only diagnostics, and local disposable PostgreSQL tests.
Nothing in that folder has been applied to a hosted database. It is not an
upgrade script for an uninspected learning/NFL database.

Local verification, from SelfLearning:

```sh
npm ci --prefix tools/automation/supabase --no-audit --no-fund
npm test --prefix tools/automation/supabase
.venv/bin/python -m unittest discover -s tools/automation/supabase/test -p 'test_*.py'
```

The next hosted step is to inspect the intended project's schema, grants,
migrations, isolation and cost. If a new project is selected, review the exact
organization and tier before creating it. The unrelated AYSO project must not
be repurposed.

Enable the terminal in the order documented in its owner-auth runbook:
configure owner sign-in first, verify anonymous and wrong-owner denial, then
supply the server database/research secrets and perform disposable end-to-end
checks. Public browser settings may contain only the approved public URL and
publishable/anon key. A modern `SUPABASE_SECRET_KEY` stays on the server;
the legacy service-role key is an alternative, not an extra browser credential.

Hosted storage, the Python store adapter, outcome resolution, and correct
scoring still need an integrated test. A successful schema test or owner login
does not prove the learning loop works.

## 6. Codex implementation queue

Continue with small feature branches and reviewable PRs. The six source-handoff
tasks remain:

| Task | Required evidence |
|---|---|
| Reconciliation | Position/order differences, missing/extra symbols, partial fills and rounding are handled; material mismatch returns a halt. |
| Signal registry and NEVER-REGRESS gate | Signals start at weight zero; held-out evaluation rejects ties and regressions. |
| Options odds engine | Seeded simulations/bootstraps, explicit units and multipliers, maximum-loss calculation, and known-answer probability tests. |
| Judgment providers | Injectable transports, verified model identifiers, validated outputs and receipts; malformed output produces an explicit error. |
| Shadow runner | Timestamped predictions from fixture inputs, risk verdicts, durable logs and **zero orders**; manual dispatch only. |
| Terminal series/ratio routes | Shared data helpers with byte-identical breadth behavior and parity tests. |

These tasks do not replace the separate authentication, hosted-store, outcome
resolver, persistent order-state and recovery work. Reinspect current code
instead of assuming an earlier audit or a capability flag proves correctness.

**No scheduled bot runs until the hosted store and runnable application work
together. No live trading or autonomy promotion in this setup work.** Risk
limits for the $2,000 account remain a concrete owner decision, not a copied
$1,500 value from an older conversation.

## 7. Copy/paste starter prompts

### For Codex

> Continue the Automated Trade Bot in the existing SelfLearning and
> liddar-terminal repositories. Start from codex/automation-tooling-shadow and
> codex/terminal-owner-auth, preserving any current work. Read AGENTS.md,
> CLAUDE.md, the November 10 handoff, docs/automation-setup.md,
> docs/automation-service-readiness.md and this next-steps guide.
>
> Run the reproducible setup and both repositories' tests. Report installed
> dependencies, authenticated services, tested application behavior and
> production eligibility separately. Do not claim connections from env-variable
> presence or file existence. Verify the prepared changes and leave reviewable
> PRs; do not merge or deploy while the Vercel collision is unresolved.
>
> Then implement the first unblocked handoff engineering task in a separate
> branch with meaningful tests. Continue routine fixes and verification without
> asking me about each implementation choice. Keep SHADOW, no schedules, no
> broker orders, no live policy changes, no unrelated database use, and no
> wholesale NFL-branch merge. Never print credentials. When an owner action is
> truly required, identify the exact blocker and the already-prepared result
> that makes my action concrete.

### For Grace

> Coordinate my Automated Trade Bot using the configured Cloud environment
> with SelfLearning and liddar-terminal. Read the prepared branches and
> docs/codex-grace-next-steps.md. First delegate one bounded setup/test task to
> Codex and verify that its result returns to you. If that capability or
> environment access is missing, identify the one account/environment action
> needed; do not imply that delegation is already working.
>
> Keep GitHub PRs, CI results and the shared checklist as the project record.
> Delegate routine engineering and follow up on failures. Give me concise
> progress and material exceptions instead of asking me to relay logs. Keep
> tool installation, service authentication, application verification and
> production eligibility separate. Do not create trading schedules, merge main
> before the Vercel issue is resolved, enable live trading, or reinterpret the
> old $1,500 trade-loss figure as approval for my $2,000 account.

### For this chat

> Help finish the account and deployment prerequisites for this handoff.
> Verify the correct Vercel team/project before repairing the reported
> production collision. Verify the intended Supabase project before choosing
> reuse or a fresh-project migration. Prepare any cost or owner-access decision
> with the exact target and effect. Review Codex's evidence and keep Grace's
> environment/delegation status honest. Continue independent setup work while
> account access is pending; keep trading and schedules disabled.

## 8. What "ready for the next stage" means

The immediate success condition is: both prepared branches are available,
the actual Codex/Grace environment reproduces the tests, one delegated task
returns successfully, and the correct account access allows the production
and database questions to be resolved.

The next engineering milestone is a manually started shadow run that writes a
real stamped prediction to the isolated store, later resolves it, scores it,
and displays the result to the authenticated owner with zero broker orders.
Only after observing that path should scheduled shadow operation be considered.
