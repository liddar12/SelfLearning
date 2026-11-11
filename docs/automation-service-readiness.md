# Automation service readiness

Checked November 10, 2026 against the final Claude handoff and the connected
accounts available to this ChatGPT task. This is a dated observation, not a
live status feed. Recheck access before acting.

Update after the production repair: Vercel access is verified for the intended
team and both projects. SelfLearning's intended NFL deployment is restored;
both production aliases and a hostname-based deployment read confirm it, and
the hosted health endpoint returned HTTP 200. Jimmy confirmed saving the
production branch setting; that setting is owner-reported, not API-read
verified. See the [dated repair record](vercel-production-repair-2026-11-10.md).
Preserve the rollback's pause in automatic production-domain assignment.
Supabase access is now verified. The [access and isolation record](supabase-access-and-isolation-2026-11-10.md)
supersedes earlier inaccessible-project observations. Jimmy subsequently approved sharing this existing project. The
[implementation record](shared-supabase-implementation-2026-11-10.md) supersedes
the separate-project proposal.

## Working arrangement

Grace coordinates the project, keeps the backlog and reports exceptions.
Codex implements and tests changes in these two existing repositories.
A separately deployed worker will run the trading application. GitHub is the
shared record for code, CI results, decisions and operating instructions.

Dots can start Codex tasks and follow up on them when an appropriate cloud
environment is already available. This session has not verified Grace's access
to one. The setup browser is signed out of ChatGPT, and a local CLI
configuration does not install plugins into Grace's account.

## Verified and pending connections

| Component | Verified observation | Remaining action |
| --- | --- | --- |
| GitHub | Both repositories are readable; setup work is published as draft SelfLearning PR #15 and draft terminal PR #13; neither is merged | Review current CI/preview evidence; production repair does not authorize merging or deployment |
| Python development tools | Isolated setup and all three library suites run in this cloud checkout | Recreate using the committed setup command on the actual execution host |
| Infrastructure CLIs | Netlify `27.17.0`, Vercel `65.14.0`, Supabase `3.1.0` installed and version-verified | OAuth/account authorization is separate from installation |
| Vercel | Team consent verified for `liddar-terminal` (`team_iQtnIckXS2Om0FEzZzYUtn9l`); SelfLearning's release deployment, both aliases and hosted health verified; Branch Tracking save owner-confirmed | Preserve the rollback's automatic-assignment pause; database, ingestion and full runtime observability are separate checks |
| Supabase | Shared trading migration `20261110233747` applied to SelfLearningAI `omavgimpnvrpuxqhfxpf`; hosted role/rollback checks passed and original NFL catalog/grants/counts preserved; one confirmed owner enrolled, zero workers; real owner Scores read passed | Establish the separate worker identity/session lifecycle and integrate the runnable application |
| Terminal Preview | Six Auth/storage settings read back only for `codex/terminal-owner-auth` in `liddar-terminal-2tpb`; preview READY at `344921dd0da7a3b31737b40dbfd00cfab2bfa581`; real owner sign-in, Scores/reload/sign-out and signed-out response passed | Keep PR draft for broader review; owner preview acceptance does not authorize production promotion or prove a running bot |
| Netlify | Official CLI and local Codex MCP configuration prepared; no callable installed ChatGPT plugin found in this session | Owner Netlify OAuth and an explicit target site if used; current terminal remains on Vercel |
| Grace and Codex Cloud | Native delegation is documented, but account/environment access is not verified here | Publish or select one environment containing both repositories and demonstrate one delegated task with a returned result |
| Alpaca paper | Equities/ETF adapter exists; paper connection preflight is implemented and offline-tested; credentials absent here | Supply paper-only keys privately, then run the read-only paper preflight |
| Robinhood | Official external-agent MCP endpoint verified; no account connected; prepared definition disabled | Owner onboarding/OAuth and an explicit broker decision; current agent interface does not support multi-leg options |
| Schwab | Adapter remains a stub; no developer OAuth connection verified | Developer app/authorization, adapter implementation and review of API terms for the intended autonomous use |
| Fidelity | No authorized retail execution API/connector was established by this investigation | Do not use credential scraping or browser trading as a substitute |
| Model providers | Judgment adapters are a handoff backlog item; no runtime keys verified here | Implement/validate the provider interface and verify actual model IDs/entitlements before enabling calls |

## Production repair: completed for routing and health

The [November 10 repair record](vercel-production-repair-2026-11-10.md)
documents the restored deployment, displaced main build, both alias mappings,
owner-confirmed branch-setting save and hosted health response. It resolves
the historical collision prerequisite for this observed scope. Both setup PRs
remain draft; no PR merge or terminal production change was performed.

The offline inventory's
`HANDOFF_VERCEL_PRODUCTION_COLLISION_REQUIRES_VERIFIED_RESOLUTION` entry is a
static historical prerequisite, not a fresh observation. The dated repair
record supplies its resolution; the inventory still does not establish overall
production or live-trading eligibility. The original Claude handoff remains
unchanged as historical source material.

An Instant Rollback pauses automatic production-domain assignment. Preserve
that pause; do not promote a deployment as cleanup. A follow-up error/fatal
aggregate log query for the restored production deployment returned no matching
rows in the sampled five-minute window, and the project drain list was empty.
This does not establish that the runtime is error-free outside that sample.
Database, ingestion and operational recovery remain unverified by these checks.

Do not operate on the old March `liddar-terminal` project/domain. The terminal
target in this handoff is `liddar-terminal-2tpb`.

## Shared-project decision and owner setup status

Jimmy approved using existing SelfLearningAI `omavgimpnvrpuxqhfxpf` for both
applications. No separate project, billing change or organization selection is
needed. Follow the [implementation record](shared-supabase-implementation-2026-11-10.md)
for exact migration, code and hosted verification status.

The trading area uses separate schemas and application grants, a closed
principal allowlist, and security-invoker RPCs. Terminal data calls use the
verified owner's bearer token; a separate worker identity handles backend
writes and outcomes. No broad service-key fallback is allowed. Existing NFL
objects and project-wide Auth settings remain outside this additive change.

Jimmy created the private application login. Agents verified the confirmed
Auth identity and enrolled it as an enabled trading owner. Supabase recorded
successful private sign-in. An initial terminal verification failure was
traced to omission of the protected preview cookie; the scoped fix passed CI
and real hosted acceptance. The owner opened private panels and Scores,
retained access after reload, then signed out and remained signed out after
another reload. Direct `/api/session` navigation returned the application's
`Sign in to continue.` response. This records the response body, not a
browser-read HTTP status. Wrong-owner denial was verified in mocked CI only;
no second real account was created. No duplicate account, management-plugin
reconnection or password change is required.

The additive migration and hosted rollback-only permission checks are now
complete. The allowlist now has one enabled owner and zero workers; trading
evidence tables remain empty. Six Auth/storage settings are saved only for
the terminal's Preview branch. See the implementation record for the exact
scope, source/hosted-version mapping and separation between database checks,
mocked tests and live browser acceptance. The
[mobile/Grace checklist](mobile-grace-setup.md) gives the remaining environment
and delegation actions.

## Database and terminal prerequisite

The original terminal baseline lacked caller authentication on its database
and paid model routes. The companion PR now supplies owner sign-in and
server-side authorization, verified on its protected Preview deployment.
Keep service credentials off the old public baseline. The configured preview's
trading storage uses the verified owner's bearer and a publishable key,
without a project service/secret key.

The shared-project terminal uses the same project URL for Auth and fixed
trading RPCs, with authenticated RLS and a principal allowlist. Do not change
project-wide signup settings as a side effect of terminal setup.

The original fresh-project candidate remains guarded and unapplied. The
shared-project migration is separate and additive. The unrelated AYSO project
remains outside this setup.

## Scope of automation

The setup command installs tools. The offline inventory reports observations.
The paper connection check performs a read-only account request only when
explicitly requested. None of them starts the bot or enables live orders.

The six implementation tasks in the source handoff remain the engineering
backlog: reconciliation; signal registry/adoption evaluation; options odds;
judgment providers; shadow runner; and terminal series/ratio data routes.
Hosted storage, outcome resolution, durable order state and supervised runtime
recovery must also be verified as an integrated system.

Scheduled bot runs remain disabled until the hosted store and runnable
application exist. Live risk policy is unset. The historical $1,500 maximum
loss is not an approved policy for the $2,000 account.

Use one concise progress digest when monitoring is configured, with immediate
exceptions for failed reconciliation, unclear order state, stale inputs,
credential expiry or an operating limit. Grace should report actual recorded
state and delegate routine engineering without asking the owner to relay logs.

## Sources

- [Dot tasks and delegation](https://learn.chatgpt.com/docs/dots/tasks-and-memory)
- [Codex Cloud environment setup](https://learn.chatgpt.com/docs/environments/cloud-environments)
- [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)
- [Netlify with ChatGPT](https://docs.netlify.com/build/build-with-ai/agent-setup-guides/use-netlify-with-chatgpt/)
- [Robinhood external-agent onboarding](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
- [Robinhood supported agent orders](https://robinhood.com/us/en/support/articles/trading-with-your-agent/)
- [Schwab Trader API terms](https://contentdelivery.schwab.com/api/content/rtcontent/asset/trader-api-terms-and-conditions)
- Source handoff: `docs/codex-handoff-2026-11-10.md`
