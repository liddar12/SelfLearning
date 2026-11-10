# Automation service readiness

Checked November 10, 2026 against the final Claude handoff and the connected
accounts available to this ChatGPT task. This is a dated observation, not a
live status feed. Recheck access before acting.

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
| GitHub | Both repositories are readable; current baseline is SelfLearning `46be142de18823ec86be2e33d8a421ce272616a8`, terminal `6f4af51` | Publish/review the setup feature branches; respect the main-merge restrictions |
| Python development tools | Isolated setup and all three library suites run in this cloud checkout | Recreate using the committed setup command on the actual execution host |
| Infrastructure CLIs | Netlify `27.17.0`, Vercel `65.14.0`, Supabase `3.1.0` installed and version-verified | OAuth/account authorization is separate from installation |
| Vercel | Plugin installed; intended team requests return 403 | Owner team consent for `liddar-terminal` (`team_iQtnIckXS2Om0FEzZzYUtn9l`), then inspect and repair the production collision |
| Supabase | Connected account exposes only an unrelated AYSO organization/project; intended learning project access is denied | Connect the account/organization owning `omavgimpnvrpuxqhfxpf`; inspect schema before choosing a fresh project or migration |
| Netlify | Official CLI and local Codex MCP configuration prepared; no callable installed ChatGPT plugin found in this session | Owner Netlify OAuth and an explicit target site if used; current terminal remains on Vercel |
| Grace and Codex Cloud | Native delegation is documented, but account/environment access is not verified here | Publish or select one environment containing both repositories and demonstrate one delegated task with a returned result |
| Alpaca paper | Equities/ETF adapter exists; paper connection preflight is implemented and offline-tested; credentials absent here | Supply paper-only keys privately, then run the read-only paper preflight |
| Robinhood | Official external-agent MCP endpoint verified; no account connected; prepared definition disabled | Owner onboarding/OAuth and an explicit broker decision; current agent interface does not support multi-leg options |
| Schwab | Adapter remains a stub; no developer OAuth connection verified | Developer app/authorization, adapter implementation and review of API terms for the intended autonomous use |
| Fidelity | No authorized retail execution API/connector was established by this investigation | Do not use credential scraping or browser trading as a substitute |
| Model providers | Judgment adapters are a handoff backlog item; no runtime keys verified here | Implement/validate the provider interface and verify actual model IDs/entitlements before enabling calls |

## Priority: repair deployment routing before main merges

The handoff reports that SelfLearning PR #14 caused Vercel `self-learning` to
publish main in place of the NFL runtime. This session could not inspect the
private deployment because the target team grant is missing.

After the owner grants team access:

1. Inspect project `self-learning`, its current production deployment and its
   production Git branch. Verify the project's repository and root directory.
2. Inspect reported prior deployment `dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3` and
   verify that it belongs to this project and is the intended working NFL build.
3. Restore that confirmed deployment and configure the intended production
   branch, reported as `release/optimized-nfl-runtime-20261018`, using the
   provider's supported controls. Verify the resulting alias and Git settings.
4. Re-evaluate the merge restriction only after observing the actual result.

Do not operate on the old March `liddar-terminal` project/domain. The terminal
target in this handoff is `liddar-terminal-2tpb`.

## Database and terminal prerequisite

The reviewed terminal baseline has no caller authentication on its database
and paid model routes. Do not enable service credentials on that public
baseline. The companion terminal change must provide an owner sign-in flow,
server-side owner authorization and modern Supabase secret-key compatibility
before hosted data is enabled. Configure the owner identity in server settings;
only a publishable/legacy anon key can reach the browser.

Any fresh-project schema candidate is for an empty, explicitly selected
database. It is not a migration for an uninspected existing production database.
The AYSO project is outside this trading setup and must not be repurposed.

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
