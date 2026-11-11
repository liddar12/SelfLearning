# Manage Automated Trade Bot with Grace from your phone

Updated November 10, 2026 after a fresh readiness audit. Start with the
[current audit and release plan](readiness-audit-2026-11-10.md), then use the
[shared implementation record](shared-supabase-implementation-2026-11-10.md)
and [engineering handoff](codex-grace-next-steps.md).

**Current next step:** review the controlled terminal Production release below.
The owner-protected Preview works, but the production terminal still serves
older code. Grace's environment publication/delegation is unknown; first check
for an existing published environment instead of recreating it.

## What is ready

The shared-project migration `20261110233747_shared_trading_isolation` is
applied to SelfLearningAI (`omavgimpnvrpuxqhfxpf`). Hosted permission checks
passed and rolled back their fixtures. One real owner is now enrolled;
worker count is zero and trading evidence tables remain empty.
Existing NFL catalog/default-grant fingerprints and record counts are unchanged;
the NFL health endpoint returned HTTP 200, `ok`, mode `L0`.

- Backend: [PR 15](https://github.com/liddar12/SelfLearning/pull/15), branch
  `codex/automation-tooling-shadow`. All six CI jobs passed at the inspected
  head `b2b1b0c12696f838a11ea55e5e267dd28adeb78f`
  ([run](https://github.com/liddar12/SelfLearning/actions/runs/42930357960));
  fresh local regression and database suites also passed.
- Terminal: [PR 13](https://github.com/liddar12/liddar-terminal/pull/13), branch
  `codex/terminal-owner-auth`. Fresh CI rerun passed 37 tests, build and a cookie-gated
  mocked browser flow at `344921dd0da7a3b31737b40dbfd00cfab2bfa581`
  ([run](https://github.com/liddar12/liddar-terminal/actions/runs/42929541393)).
- Six Auth/storage configuration entries are saved only for the terminal's
  Preview branch `codex/terminal-owner-auth` in `liddar-terminal-2tpb`.
- The repaired [terminal preview](https://liddar-terminal-2tpb-8p99j7x3w-liddar-terminal.vercel.app)
  is READY at that exact commit. Real owner sign-in, read-only Scores, reload,
  sign-out and the signed-out application response passed. Wrong-owner denial
  was tested in mocked CI, not with a second real account.

**Remaining:** close the terminal Production release gap, verify Grace's
existing environment and delegated task, and build the trading application.
The execution Dockerfile still runs a scaffold; Schwab/Robinhood adapters are
stubs and the implemented Alpaca adapter excludes options. This setup has not
enabled an unattended trading worker, trading schedule or live orders. Both PRs
remain draft. See the audit for exact test scope and missing modules.

## Who does what

| Component | Responsibility |
| --- | --- |
| Here | Connected GitHub/Vercel/Supabase setup, scoped deployment checks and owner handoffs |
| Codex CLI or desktop | Code, tests, debugging and PR preparation; local use is optional |
| Codex Cloud | Reusable engineering setup containing both repositories; each task has its own workspace |
| Grace | Coordinate the backlog, delegate engineering, inspect returned evidence and report exceptions |
| Deployed worker | Run the application with durable records, deterministic controls, reconciliation and recovery |
| Jimmy | Private sign-in/consent, environment publication and material operating decisions |

Codex Cloud can continue coding while your computer is asleep. A deployed
application worker remains a separate project milestone. See
[Codex Cloud](https://learn.chatgpt.com/docs/cloud).

## 0. YOU + AGENTS — review the terminal Production release

The [audit's scoped release plan](readiness-audit-2026-11-10.md) targets only
`liddar-terminal-2tpb`. After the deployment-gate decision and required checks,
agents configure matching Production Auth settings, merge tested terminal PR 13
through GitHub to main, and verify the resulting Production deployment.
SelfLearning PR 15 remains outside this release.
The current production `/api/claude` handler is publicly reachable and lacks
caller authentication; no paid request was made during the audit.

Review that concrete plan in this chat. The owner login, stored provider key,
shared migration and NFL production repair already exist; do not recreate
them. This terminal release is separate from building or
enabling a trading worker.

## 1. YOU — open the existing Grace conversation on your phone

Use the ChatGPT mobile app in the account and workspace that owns Grace. Reuse
the existing Grace conversation. Enable notifications in your phone settings
if you want alerts.

**Complete when:** Grace replies in the mobile app. Account-specific availability
still needs checking. Dots support the mobile app; mobile web is unsupported.
See [mobile messaging](https://learn.chatgpt.com/docs/dots/channels).

## 2. YOU + CODEX — verify and reuse the engineering environment

On web or desktop in Grace's account/workspace, open
**Settings → Codex Cloud → Environments**. First look for an existing published
environment with both `liddar12/SelfLearning` and `liddar12/liddar-terminal`.
If one exists, keep its exact name and proceed to Step 3. If it is a draft,
review and publish it. **Create environment** only if no suitable environment
exists; name the new one **Automated Trade Bot** and connect GitHub only if
access is missing. This chat has not established whether you already have one.

Select **Get started**. Ask Codex to use the two branches above, read repository
instructions and the current handoff, install the pinned dependencies, and
reproduce the required tests. Codex should resolve routine installation failures;
Jimmy supplies only missing private access or account decisions. Preserve any
existing work and the restored NFL production deployment.

Review the setup report, save, and select **Publish**. One environment can include
both repositories. See
[environment setup](https://learn.chatgpt.com/docs/environments/cloud-environments).

**Complete when:** the interface reports **Environment published**, and a new
task from that environment can access both repositories and run their checks.
Saving a draft alone does not complete this step.

## 3. YOU — give Grace the first bounded task

Send this in Grace's conversation, replacing the environment name if needed:

> Coordinate Automated Trade Bot using the published Codex Cloud environment
> named Automated Trade Bot, or first identify my existing suitable published
> environment. Do not create a duplicate. Read SelfLearning's AGENTS.md, CLAUDE.md,
> docs/readiness-audit-2026-11-10.md,
> docs/shared-supabase-implementation-2026-11-10.md and
> docs/codex-grace-next-steps.md. Use SelfLearning branch
> codex/automation-tooling-shadow (PR 15) and liddar-terminal branch
> codex/terminal-owner-auth (PR 13).
>
> First delegate a verification-only task to Codex. Inspect the current commits,
> run the existing tests in its workspace, and return the task link, environment
> name, branches, commit IDs, test results and blockers. Make no source, hosted
> configuration, production, broker or scheduling changes in this first task.
> Check the returned evidence yourself and report here. If access is missing,
> identify the exact connection needed instead of claiming delegation works.

**Complete when:** Grace returns an actual delegated task and checked results.
Her acknowledgment alone is insufficient. Give her the environment's exact name;
no special Grace “Tools” picker is assumed. Official
[delegation guidance](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
requires an existing configured environment and explains that tasks receive
their own supplied context.

## 4. COMPLETED — private owner login and preview acceptance

**Completed:** Jimmy created the private application login. Agents verified
the confirmed Auth identity, enrolled one owner, and configured the terminal
preview. The preview-cookie transport repair passed CI and real hosted
acceptance: private panels appeared, Scores read the shared trading store,
reload preserved access, and sign-out followed by reload returned to login.
Direct signed-out `/api/session` navigation returned the application's
`Sign in to continue.` response. Wrong-owner rejection is separately covered
by mocked CI; it was not tested with another real Auth account.

There is no need to create another account or reconnect the Supabase management
plugin. Open the [stable preview address](https://liddar-terminal-2tpb-git-codex-terminal-cf1c5b-liddar-terminal.vercel.app/)
when you want to use it. It retains Vercel Preview protection and may require
authorized preview access plus your normal private sign-in in a new browser.
Do not paste credentials into chat. No prediction rows were added by this
read-only check, and NFL production was not changed.

## 5. AGENTS — finish the operational milestones

Grace can then coordinate bounded Codex tasks and report material exceptions.
Agents own worker identity/session refresh, a manual shadow run, outcome
resolution, task-specific scoring and authenticated display. That first run
must produce durable evidence and zero orders.

Next verify restart recovery, duplicate prevention, broker-state reconciliation,
monitoring and a real worker stop control before scheduled operation. Later
broker consent and any live policy remain separate owner decisions.

**Complete when:** each milestone has observable run evidence and relevant
failure checks. Keep progress in GitHub and the handoff so Jimmy does not relay
logs. No recurring monitoring is established by this guide alone. Pausing Grace
also does not stop every delegated task, schedule or external worker; see
[dot controls](https://learn.chatgpt.com/docs/dots/controls).
