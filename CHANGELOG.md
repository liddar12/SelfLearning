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
  `tools/fit_nfl_corrections.py`) using per-market shrunk bias corrections
  stored as existing journal `source_snapshot` objects (no migration)
- Contract documentation in `docs/nfl-corrections-v1.md`
