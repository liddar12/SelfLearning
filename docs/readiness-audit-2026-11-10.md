# Automated Trade Bot readiness audit

Checked November 10, 2026, America/Chicago; checks ran approximately
2026-11-11 00:22–00:29 UTC. This record answers the owner's request to test
the current system and identify the next step. It supersedes earlier statements
that implied the only remaining work was publishing a Codex environment.

## Result

The development foundation, shared database isolation and private terminal
preview are verified. The trading application is still incomplete. A separate
production release gap also remains: the owner-protected terminal is deployed
to Preview, while the production address serves older code with an
unauthenticated paid-model proxy.

Grace's published environment and actual Codex delegation are **unknown**, not
known to be absent. Check for an existing published environment before creating
anything. Owner account creation, shared-project selection and migration do not
need to be repeated.

## Source and fresh tests

| Component | Source inspected |
| --- | --- |
| SelfLearning PR 15 | `codex/automation-tooling-shadow`, `b2b1b0c12696f838a11ea55e5e267dd28adeb78f` |
| Backend source tree | `e30aa1f3243c0a5986e3e4eb3d5fe8b9694ac014` |
| Terminal PR 13 | `codex/terminal-owner-auth`, `344921dd0da7a3b31737b40dbfd00cfab2bfa581` |
| Terminal source tree | `0b1fc83b7a0627d7bd5d72bb81c398480095c317` |

Both PRs were open drafts, unmerged and mergeable. Local commit metadata differs
from the published commits, but both complete source trees match; both working
trees were clean before this documentation update. This receipt itself may be
published at a later documentation-only commit.

| Fresh check | Result | Scope |
| --- | --- | --- |
| Self-learning core | 271 passed | Local fixtures/unit tests |
| Execution core | 60 passed | Local risk/control/broker fixtures |
| Signals | 5 passed | Local indicators |
| Automation tooling | 36 passed | Local inventory/preflight tests |
| Walk-forward smoke | Passed | Synthetic data |
| Shared database suite | 17 passed | Disposable local database, no hosted writes |
| Older database candidate | 7 passed, plus 3 checker tests | Unused candidate; not applied to the shared project |
| Terminal | 37 passed, build passed, 1 browser test passed | Fresh GitHub rerun, Node 22.23.4, mocked Auth/RPC responses |
| Infrastructure CLIs | Version checks passed | This checkout only: Netlify 27.17.0, Supabase 3.1.0, Vercel 65.14.0 |

The backend regression command strips service credentials from child processes.
The terminal rerun is job `130229125837` in
[run 42929541393](https://github.com/liddar12/liddar-terminal/actions/runs/42929541393).
All six CI jobs also passed for the inspected backend head in
[run 42930357960](https://github.com/liddar12/SelfLearning/actions/runs/42930357960).
These checks establish implemented behavior under fixtures; they do not prove
broker connectivity, a deployed trading worker or options execution.

## Hosted verification

| Layer | Observed state |
| --- | --- |
| Shared Supabase | SelfLearningAI `omavgimpnvrpuxqhfxpf` is ACTIVE_HEALTHY; the selected migration is applied |
| Principals and trading evidence | One enabled owner, zero workers; all four trading evidence tables empty |
| Current owner database access | Read-only authenticated-role RPC checks using the enrolled owner identity passed; this is not a new browser login |
| Real owner preview flow | Earlier actual private sign-in, Scores read, reload, sign-out and signed-out reload passed on the same terminal commit; see the [acceptance record](shared-supabase-implementation-2026-11-10.md) |
| Fresh anonymous preview check | With authorized preview access, the entry page showed owner login and direct `/api/session` navigation returned the application's sign-in-required JSON; owner credentials were not requested again |
| Preview configuration | Six Auth/storage entries are scoped only to Preview branch `codex/terminal-owner-auth` |
| Preview deployment | READY `dpl_G1Axg1jq3WnBSmuwjYgGnPvdrYas`, commit `344921dd0da7a3b31737b40dbfd00cfab2bfa581` |
| Existing NFL production | Restored release `dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3`; fresh health HTTP 200, `ok`, `hosted-foundation-v1`, `L0` |
| Supabase advisor | 19 INFO findings plus a warning that leaked-password protection is disabled; no setting was changed by this audit |

The advisor warning is a shared-project Auth-hardening item, separate from the
working owner session and trading RLS. Do not silently change project-wide Auth
settings or describe the warning as a failed owner login.

### Production terminal release gap

`liddar-terminal-2tpb.vercel.app` and
`liddar-terminal-2tpb-liddar-terminal.vercel.app` currently resolve to READY production
deployment `dpl_AxGPqX6J9CaTBFb3JeeZestHeTtn`, `main` commit
`6f4af5112c4a43247428f1ad64cd9ebdb5fa9602`.

Ordinary anonymous GET requests, with no Authorization header, cookies or
Vercel share link, reached application handlers:

- `/api/claude`: HTTP 405, JSON `{"error":"POST only"}`.
- `/api/predictions`: HTTP 503 with an application error response.

A separate anonymous POST to `/api/claude` with the empty body `{}` returned
HTTP 400, `{"error":"messages[] required"}`. The inspected production source
validates missing/empty messages before its upstream fetch, so this check did
not request a provider call or charge.

The production version of
[`api/claude.js`](https://github.com/liddar12/liddar-terminal/blob/6f4af5112c4a43247428f1ad64cd9ebdb5fa9602/api/claude.js)
has no caller authentication, accepts a caller-selected model and token limit,
and forwards nonempty messages to Anthropic. Vercel metadata shows an existing
`ANTHROPIC_API_KEY` entry targeting Production and Preview. Its value and
validity were not inspected. No paid-model request was made. Public handler reachability
and the source establish a real protection gap; this audit does not claim a
successful paid-model request or evidence of misuse.

## What is not implemented yet

| Component | Direct source evidence and implication |
| --- | --- |
| Trading application/runner | `apps/liddar-execution` contains a README, Dockerfile and universe config. Its actual Docker command only prints that the engine is a scaffold. There is no running execution loop. |
| Schwab and Robinhood adapters | Operational methods in `packages/execution-core/execution_core/adapters/{schwab,robinhood}.py` raise `NotImplementedError`. Capability flags do not prove an integration. |
| Alpaca options | The adapter implements equities/ETFs and rejects option intents. Its platform capability flag must not be presented as options-adapter support. |
| Paper broker connection | The offline preflight reports missing paper keys in this runtime, no network check and zero orders. This does not establish whether keys exist in another environment. |
| Durable orders and reconciliation | `execution_core/oms.py` keeps orders in a process-local dictionary. The expected reconciliation module is absent; restart recovery is not established. |
| Operational stop control | `ControlState` has tested pause/autonomy logic, but a deployed worker/control API/mobile stop path has not been built. |
| Worker authentication | `TradingSupabaseStore` requires an external current-token provider. It does not sign in, persist/refresh credentials or provision a worker; current usage is in tests. |
| Remaining pipeline modules | Expected signal registry, never-regress gate, options odds, judgment-provider and shadow-runner implementations are absent. The market-data provider is an abstract interface. |
| Learning beyond proposals | Task-separated scoring and L0 monitoring exist; L1 can propose calibration with verified evidence. L2/L3/L4 policies still raise `NotImplementedError`. No trading outcomes exist to establish performance improvement. |

The offline inventory also contains historical production-collision and
hosted-store prerequisite strings. They are not current remote probes. Read
them alongside the completed migration, owner-acceptance and NFL repair
receipts. Likewise, a provider CLI absent from global PATH can still be installed
in the pinned project-local tool directory; that verifier passed. Installation
never establishes OAuth or Grace access.

## Next actions, with ownership

### 1. Review a controlled terminal Production release

The next release action is to bring the tested owner protection to
`liddar-terminal-2tpb` Production. Agents prepare the change; Jimmy supplies the
deployment-gate decision required by `CLAUDE.md`. This audit authorizes no
rollout and does not merge either PR.

The concrete release plan is:

1. Read back the terminal's current Production settings and preserve existing
   unrelated configuration.
2. Configure the same verified project, publishable key and existing owner for
   Production using `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`,
   `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `TERMINAL_OWNER_USER_ID`, and
   `TERMINAL_STORAGE_BACKEND=shared-trading-v1`. No new owner, password, project,
   broad service key or migration is needed.
3. After the owner deployment checkpoint and required review checks, merge the
   tested terminal PR 13 through GitHub to `main`, then monitor the Production
   build and verify its exact merge SHA and both Production aliases. READY
   alone does not establish that the new build serves those addresses. This keeps subsequent main
   deployments on the protected source. Do not merge SelfLearning PR 15 as
   part of this terminal release. Retain the existing provider key.
4. Use the Production build/configuration path, not a quick Preview promotion
   that assumes Preview values carry over. Vercel promotion rebuilds a Preview
   deployment with Production environment variables; it does not simply reuse
   the Preview configuration. See
   [Vercel's promotion documentation](https://vercel.com/docs/deployments/promoting-a-deployment).
5. Verify anonymous private API requests return 401, including an empty POST
   to `/api/claude`, before any provider or storage work. Verify the existing
   owner's read-only Scores/sign-out flow through private sign-in as needed.
   Do not make a paid-model request merely to test denial.
6. Record the release and its fail-closed recovery procedure. The older
   unauthenticated paid proxy is not an acceptable unattended fallback.

This scope is the terminal only. Preserve the restored NFL deployment and its
automatic production-domain assignment pause. The release does not create a
trading worker, broker connection, schedule or live-trading authority.

### 2. Verify Grace's existing environment before creating one

In the account/workspace that owns Grace, first look for a published environment
with both repositories. Reuse it if present. If it exists as a draft, finish its
review/publication. Create an environment only if no suitable one exists.

The current session did not obtain an environment publication receipt or an
actual Grace-to-Codex task result; its ChatGPT browser was signed out. That is
an observability limit, not proof that Jimmy has not already completed setup.
Ask Grace to identify the existing environment and return one actual delegated
verification task, its branches/commits and checked results. See the updated
[mobile checklist](mobile-grace-setup.md).

### 3. Build the first complete shadow execution slice

Agents next implement a manually invoked, zero-order path: scoped worker
authentication, timestamped input, prediction and risk verdict, durable write,
outcome resolution, task scoring, and authenticated terminal display. Verify
missing-data/auth/storage failures explicitly. Durable order state,
reconciliation, restart recovery and a working stop control precede scheduled
or broker execution. Broker consent and the $2,000 live operating policy remain
separate later decisions.

No account credentials were read or printed, no broker order or paid-model
request was made, and no application configuration or hosted trading data was
changed by this audit. The terminal CI job was rerun and authorized temporary
preview access was renewed for the browser check. The Production release plan
remains pending the owner deployment checkpoint; no release action was taken.
