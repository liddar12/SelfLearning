# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Common Changelog](https://common-changelog.org/).
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- NFL corrections planner v1: `POST /api/v1/nfl/corrections` with
  `SELFLEARN_CORRECTIONS_MODE=off|shadow|on` (default off) and distinct
  `SELFLEARN_NFL_CORRECTIONS_TOKEN` auth
- Offline artifact fitter (`fit_corrections_artifact` /
  `tools/fit_nfl_corrections.py`) using per-segment shrunk bias corrections
  stored as existing journal `source_snapshot` objects (no migration)
- Contract documentation in `docs/nfl-corrections-v1.md`
- Adversarial hold regressions for Beta findings (thin segments, fake
  eligibility rules, clock order, casefold push/void, selection isolation,
  public provenance)

### Changed

- Corrections segments keyed by `market|selection` (no cross-selection apply)
- Validate canon `eligibility_rules` equality, segment floor flag consistency,
  `n_graded` == sum of segment `n_eligible`, and
  `trained_through <= fitted_at <= expires_at`
- Expand public artifact provenance (segments, floors, caps, fit hash, rules)
- Default fit `source` is `graded_caller_list_untrusted` (digests ≠ signatures;
  no journal grade-revision/as-of trust claimed)
- Document that rapid-disable is unavailable (Vercel redeploy + Netlify
  rebuild/SWR) and activation remains blocked
