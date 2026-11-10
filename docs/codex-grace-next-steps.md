# Next steps for Codex, Grace, and this chat

Updated November 10, 2026 after the Vercel repair and verified Supabase
reconnection. This guide uses the reviewed handoff and setup branches below.
It does not authorize live trades or change account permissions.

- Backend: `liddar12/SelfLearning`, branch `codex/automation-tooling-shadow`.
- Terminal: `liddar12/liddar-terminal`, branch `codex/terminal-owner-auth`.
- Account context: $2,000. The older $1,500 trade-loss figure is not an approved
  policy for this account.

## Updated owner checklist after Supabase reconnection

SelfLearning's intended NFL deployment has been restored. Both production
aliases now resolve to the verified release deployment, and the hosted health
endpoint returned HTTP 200. Jimmy confirmed saving Production Branch Tracking
to `release/optimized-nfl-runtime-20261018`; that setting is owner-reported,
not independently read through the API. See the
[dated repair record](vercel-production-repair-2026-11-10.md) for exact evidence
and its limits. Preserve the rollback's pause in automatic production-domain
assignment. Both setup PRs remain draft; neither has been merged.

**Supabase access now works.** The intended `SelfLearningAI` project
(`omavgimpnvrpuxqhfxpf`) is ACTIVE_HEALTHY in the SelfLearningAI organization.
Read-only catalog inspection confirms an existing shared learning/NFL store.
See the [access and isolation record](supabase-access-and-isolation-2026-11-10.md).

**Current decision:** Jimmy approved sharing the existing SelfLearningAI project
for NFL and trading. A separate project and organization-cost quote are no
longer prerequisites. The [shared-project implementation record](shared-supabase-implementation-2026-11-10.md)
supersedes the earlier separate-project proposal. Trading uses new schemas,
fixed security-invoker RPCs and explicit owner/worker Auth identities; its
runtime must not use a broad project service key.

| Step | Owner intervention | Work delegated to the agents |
|---|---|---|
| Trading storage | Existing SelfLearningAI project selected | Shared migration applied; hosted permissions and rollback cleanup verified; existing NFL catalog/grants/counts preserved |
| Supabase access | Completed and verified; no repeat reconnection needed | Existing learning/NFL catalog inspected read-only; preserve that database and the separate AYSO project |
| Vercel team consent | Completed and verified | Use the existing authorized connection for scoped verification |
| SelfLearning production repair | Branch Tracking save confirmed by Jimmy; no repeat dashboard action needed now | Rollback, both production aliases, deployment provenance and hosted health verified; preserve the automatic-assignment pause |
| Codex environment | Select both repositories in Grace's actual ChatGPT account/workspace and publish the tested environment | Install tools and reproduce the checks using the committed setup |
| Grace handoff | Give Grace the coordination prompt once and identify the published environment | Prove a delegated Codex task returns, then manage routine engineering and exceptions |
| Private credentials and owner login | Complete provider OAuth, secret entry, and owner password/email verification when required | Configure references and run secret-safe connectivity/integration checks |
| Live operating policy | Later: select the funded broker/account and approve instruments, position/loss limits and the live stage | Prepare a concrete policy and evidence from shadow/paper operation; enforce it deterministically |

No hosted database changes have been performed. The terminal's preview build
compatibility work belongs to the coding task and requires no owner key change.

Use free services where suitable and keep new automation spending within
the owner's existing $50/month additional budget across automation projects.
Do not treat that operations budget as authority to fund or trade an account.

## 1. Who does what

| Role | Next responsibility |
|---|---|
| Codex | Run setup, test both branches, implement scoped engineering tasks, and produce reviewable PRs with evidence. |
| Grace | Coordinate the backlog, delegate to Codex once her environment access works, follow up on tasks, and present decisions or exceptions. |
| This chat | Implement and verify trading isolation inside the selected existing project, preserve NFL services, and configure the scoped Auth integration. |
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
| Vercel | Team access and runtime routing/health are verified; Jimmy confirmed the branch-setting save. No repeat repair step is needed. Preserve the rollback's automatic-assignment pause. |
| Supabase | Access and use of the existing project are authorized. No new project is needed. |
| Terminal owner sign-in | Create the owner Auth user privately in the existing project. Agents verify its UUID, enroll it in the trading allowlist, and configure the terminal. Preserve shared project Auth settings. |
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

## 4. Preserve the completed production repair

The [repair record](vercel-production-repair-2026-11-10.md) resolves the
historical production-collision prerequisite for the observed routing and
health scope. Production now serves `dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3`, built
from `release/optimized-nfl-runtime-20261018` at
`aacb19c73122f257449e977df40f2a2733991b2f`. The production hostname lookup
confirmed that exact deployment, branch and commit with READY/production state.
This does not establish database, ingestion or full application readiness.

**Preserve the rollback's pause in automatic production-domain assignment.**
A later promotion reenables automatic assignment; do not promote as cleanup.
Both setup PRs remain draft. Repair completion does not merge either PR,
approve a deployment, or replace the normal review and regression gates.

The offline readiness script still emits
`HANDOFF_VERCEL_PRODUCTION_COLLISION_REQUIRES_VERIFIED_RESOLUTION`. It is a
static prerequisite copied from the historical handoff, not a live Vercel
probe. Read it alongside the dated repair record; do not repeat the repair or
change automation permissions to make the offline output appear green.

Primary references: [production branch configuration](https://vercel.com/docs/git#customizing-the-production-branch)
and [Instant Rollback](https://vercel.com/docs/instant-rollback).

Use the terminal target `liddar-terminal-2tpb`. Leave the original March
`liddar-terminal` project and `liddar-terminal.vercel.app` domain alone.
Do not merge merely to expose a manual workflow-dispatch button.

## 5. Shared-project trading storage

The selected target is existing SelfLearningAI `omavgimpnvrpuxqhfxpf`.
The additive migration and hosted rollback-only verification are complete;
real owner/worker Auth integration remains open. Use the
[mobile/Grace checklist](mobile-grace-setup.md) for the owner's next steps.
Read [the implementation and verification record](shared-supabase-implementation-2026-11-10.md)
for current status. The old fresh-project candidate under
`tools/automation/supabase` remains guarded and unapplied; it is not the
shared-project migration. The new additive implementation is under
`tools/automation/supabase-shared`.

The terminal uses a live-verified owner JWT and publishable key with fixed
`trade_bot_*` RPCs. A separate allowlisted worker identity is required by the
Python adapter. Database RLS and object grants constrain both identities;
project-wide service keys are not accepted for trading access. The new
principal allowlist starts empty. Existing NFL tables, journals, function
grants and project-wide Auth settings are preserved.

Owner imports are labeled unverified. Workers can append outcomes, but
`worker_submitted` does not itself establish verified forward evidence.
Shared evaluation keeps task scores and fitting datasets separate, excludes
unverified imports from learning proposals, and never promotes changes or
alters trading risk limits.

Create the owner login privately when requested. Agents can then confirm the
Auth identity and enroll/configure it without receiving the password. A real
owner-session test, separate worker credential lifecycle and complete runner
integration remain distinct from migration or unit-test success.

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
> docs/automation-service-readiness.md, docs/vercel-production-repair-2026-11-10.md,
> docs/supabase-access-and-isolation-2026-11-10.md and this next-steps guide.
>
> Run the reproducible setup and both repositories' tests. Report installed
> dependencies, authenticated services, tested application behavior and
> production eligibility separately. Do not claim connections from env-variable
> presence or file existence. Verify the prepared changes and leave reviewable
> PRs. The Vercel repair is recorded; preserve its automatic-assignment pause.
> Both setup PRs remain draft. Do not merge, promote or deploy in this task.
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
> production eligibility separate. Use the dated Vercel repair record rather
> than repeating a completed repair. Keep both setup PRs draft pending review
> and preserve the rollback's automatic-assignment pause. Do not create trading
> schedules, merge main, promote deployments, enable live trading, or reinterpret
> the old $1,500 trade-loss figure as approval for my $2,000 account.

### For this chat

> Continue from the recorded Vercel repair and verified Supabase reconnection.
> Preserve the existing NFL deployment, database and rollback pause. The
> selected target is the existing SelfLearningAI project. Use the new additive
> shared-project implementation and scoped owner/worker identities. Do not
> create a second project or reuse broad service keys for trading.
> Finish owner Auth and database integration with exact targets.
> Review Codex's evidence and keep Grace's environment/delegation status honest.
> Continue independent engineering while setup is pending; keep live trading
> and schedules disabled.

## 8. What "ready for the next stage" means

The immediate success condition is: both prepared branches are available,
the actual Codex/Grace environment reproduces the tests, one delegated task
returns successfully, and the trading area in the selected shared project passes hosted
Auth/storage integration. Supabase management/catalog access and production
routing/health are now verified in the dated records; trading application
readiness and existing NFL ingestion remain separate.

The next engineering milestone is a manually started shadow run that writes a
real stamped prediction to the isolated store, later resolves it, scores it,
and displays the result to the authenticated owner with zero broker orders.
Only after observing that path should scheduled shadow operation be considered.
