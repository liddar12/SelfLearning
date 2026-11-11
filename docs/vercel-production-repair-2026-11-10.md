# SelfLearning production repair, November 10, 2026

## Result and scope

The intended NFL deployment was restored to SelfLearning's production domains
after Jimmy confirmed saving the intended Production Branch Tracking setting.
Fresh connected-tool reads verified both production alias mappings and the
deployment returned by the production hostname. The hosted health endpoint
returned HTTP 200 with the expected release and mode.

This record resolves the historical production-collision prerequisite for
routing and health. It does not verify database access, ingestion, the complete
NFL application, the trading bot, or operational recovery. Both setup PRs remain
draft; no PR was merged and no terminal production change was performed.

## Exact target and source

| Field | Recorded value |
| --- | --- |
| Vercel team | `liddar-terminal` / `team_iQtnIckXS2Om0FEzZzYUtn9l` |
| Vercel project | `self-learning` / `prj_20rSK2jZv6db1XS45Dgs4S3ZHo8a` |
| Restored deployment | `dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3` |
| Repository | `liddar12/SelfLearning` |
| Restored source branch | `release/optimized-nfl-runtime-20261018` |
| Restored source commit | `aacb19c73122f257449e977df40f2a2733991b2f` |
| Restored deployment state / target | `READY` / `production` |
| Displaced main deployment | `dpl_8gTsvAs91dar9ss9jJX9Ay7y9J8q` |
| Displaced main commit | `46be142de18823ec86be2e33d8a421ce272616a8` |

## Evidence and what it proves

1. **Branch Tracking: owner-reported save.** Jimmy showed the project's
   Production settings page and confirmed saving
   `release/optimized-nfl-runtime-20261018`. The connected project tools do not
   expose this setting, so it was not independently API-read verified. A
   deployment's source branch alone is not proof of current Branch Tracking.
2. **Restoration target: independently read before rollback.** The named
   deployment belonged to the exact project above and was READY/production,
   with the expected repository, release branch and full commit.
3. **Rollback: authorized action completed.** The intended prior production
   deployment was restored through Vercel's rollback control.
4. **Production aliases: fresh post-action reads.** `list_aliases` showed both
   `self-learning-beige.vercel.app` and
   `self-learning-liddar-terminal.vercel.app` mapped to
   `dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3`.
5. **Hostname-based deployment lookup: independently confirmed.**
   `get_deployment("self-learning-beige.vercel.app")` returned that exact
   restored deployment ID, release branch and full commit, with
   READY/production state.
6. **Hosted health: one successful request.**
   `GET https://self-learning-beige.vercel.app/api/health` returned HTTP 200:

   ```json
   {"status":"ok","release":"hosted-foundation-v1","mode":"L0"}
   ```

The health response establishes that this endpoint responded successfully.
It does not establish the database or ingestion path. A follow-up runtime-log
aggregate query filtered to error/fatal levels, production, the restored
deployment and the last five minutes returned a table with no result rows.
This means no matching rows were returned in that sampled window; it does
not prove the absence of runtime errors or establish full observability.
`list_drains` returned `drains: []`; no log drain was configured by this repair.

## Preserve the rollback state

Vercel documents that Instant Rollback turns off automatic assignment of
production domains. Future pushes to the configured production branch do not
replace the restored deployment while that pause is in place. A later
promotion reenables automatic assignment. **Do not promote as cleanup.**

Rollback also restores the target deployment's cron definitions. No new
trading schedules were created or enabled by this work. Do not infer that
existing NFL schedules are absent or disabled; they were not audited here.
SHADOW remains the trading setup mode, with no live orders or autonomy
promotion authorized by this repair.

The historical Claude handoff remains unchanged. The offline readiness script
still emits
`HANDOFF_VERCEL_PRODUCTION_COLLISION_REQUIRES_VERIFIED_RESOLUTION` because it
contains a static handoff prerequisite, not a Vercel status probe. Use this
dated record as its routing/health resolution. Do not change code or enable
production permissions merely to make an offline status green.

## Next owner action and delegated work

Reconnect Supabase using the account/organization that owns the intended
SelfLearning project `omavgimpnvrpuxqhfxpf`. Current connector access exposes
only the unrelated AYSO project, and the intended project is inaccessible.
Complete consent privately; never paste passwords, tokens or database keys
into chat. Agents should then verify the exact project and inspect its schema,
grants, migrations and isolation read-only before selecting a database plan.

No hosted database changes have been performed. The schema in
`tools/automation/supabase` remains an unapplied candidate for an explicitly
selected empty dedicated project, not an upgrade for an uninspected existing
NFL database. Keep new trading schedules disabled until the hosted store and
runner work together. Normal PR review and regression gates remain required.

## References

- [Project production settings](https://vercel.com/liddar-terminal/self-learning/settings/environments/production)
- [Production branch configuration](https://vercel.com/docs/git#customizing-the-production-branch)
- [Instant Rollback, automatic assignment and cron behavior](https://vercel.com/docs/instant-rollback)
- [Original Claude handoff](codex-handoff-2026-11-10.md)
- [Current next steps](codex-grace-next-steps.md)
- [Service readiness](automation-service-readiness.md)
