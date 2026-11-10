# Manage Automated Trade Bot with Grace from your phone

Updated November 10, 2026. Use this guide with the
[shared implementation record](shared-supabase-implementation-2026-11-10.md)
and [engineering handoff](codex-grace-next-steps.md).

## What is ready

The shared-project migration `20261110233747_shared_trading_isolation` is
applied to SelfLearningAI (`omavgimpnvrpuxqhfxpf`). Hosted permission checks
passed and rolled back their fixtures: all five new tables remain empty.
Existing NFL catalog/default-grant fingerprints and record counts are unchanged;
the NFL health endpoint returned HTTP 200, `ok`, mode `L0`.

- Backend: [PR 15](https://github.com/liddar12/SelfLearning/pull/15), branch
  `codex/automation-tooling-shadow`. All six CI jobs passed at
  `27b2ed9cb3891c9f1263812946929d9d8a01a308`
  ([run](https://github.com/liddar12/SelfLearning/actions/runs/42924813163)).
- Terminal: [PR 13](https://github.com/liddar12/liddar-terminal/pull/13), branch
  `codex/terminal-owner-auth`. CI passed 37 tests, build and browser flow at
  `e060b69c1e94886a6df94f94d5eb20a380b247a9`
  ([run](https://github.com/liddar12/liddar-terminal/actions/runs/42924298479)).
- The terminal preview is ready, but `/api/session` returns HTTP 503:
  `Owner authentication is not configured.`

**Remaining:** no application Auth users or enrolled principals exist. Grace's
environment access and first delegated task are unverified. No unattended
trading worker, trading schedule or live orders have been enabled. Both PRs
remain draft. These implementation checks do not prove hosted owner sign-in
or trading readiness.

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

## 1. YOU — open Grace on your phone

Update the ChatGPT mobile app and sign in to the account and workspace that
owns Grace. Open the existing Grace conversation and send a message. Enable
ChatGPT notifications in your phone settings if you want alerts.

**Complete when:** Grace replies in the mobile app. Account-specific availability
still needs checking. Dots support the mobile app; mobile web is unsupported.
See [mobile messaging](https://learn.chatgpt.com/docs/dots/channels).

## 2. YOU + CODEX — publish the engineering environment once

On web or desktop, open **Settings → Codex Cloud → Environments → Create
environment**. Select both `liddar12/SelfLearning` and
`liddar12/liddar-terminal`; connect GitHub if prompted. Name the environment
**Automated Trade Bot**, or retain an existing suitable environment's name.

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
> named Automated Trade Bot. Read SelfLearning's AGENTS.md, CLAUDE.md,
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

## 4. YOU — create the private application login

Open [SelfLearningAI Authentication users](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf/auth/users).
Use the project's normal user-creation flow with your chosen email and a
password entered privately. This application login is separate from Supabase
dashboard access. Do not paste a password or token into chat.

Tell this chat **“Owner login created.”** Agents then verify the actual Auth
UUID, enroll the owner, configure the terminal preview, and check real login,
storage readback and unauthorized-access denial.

**Complete when:** the authenticated owner can use the hosted trading area and
anonymous/wrong-owner requests are denied. Keep project-wide Auth settings and
NFL production unchanged.

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
