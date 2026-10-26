# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Images: `ghcr.io/arrakis-it/planificateur-gardes:<version>`.

## [0.13.1] - 2026-10-26

### Fixed
- Generating over active generations without choosing an overlap mode answered 500
  instead of the 409 confirmation dialog for a superadmin acting as a doctor: the refused
  run is now undone with a savepoint, not a full rollback that expired the logged-in user.
  The dialog shows the period of each overlapped generation.
- Pages rendered after a session rollback no longer crash on expired user / clinic objects
  (MissingGreenlet); unhandled exceptions render the 500 error page.

## [0.13.0] - 2026-10-26

### Changed
- **Several active generations per clinic.** A generation is active while `archived_at` is
  NULL (`is_active` is deprecated: no longer read, still written on archive; migration 0014
  drops the one-active-per-clinic unique index). Two active generations never hold the
  same day.
- Generating a period that meets active generations asks first (confirmation dialog, live
  notice on the Générer page, `GET /generer/chevauchement`): days in locked months stay
  where they are; the other days are either **transferred** as they are (manual edits,
  desiderata flags, draft cells) or **recalculated** (the old assignments go to an archived
  generation, compared in the changelog email). Overlapped generations are fitted to their
  remaining days, or archived when none is left.
- Calendars and the iCal feed show the union of the active generations, and the latest
  archived assignment (read-only) for the days none of them holds.
- Results, statistics and PDF are per generation (`?generation=`, default: the one covering
  today); statistics count only the generation's own days. Saving the shared draft emails
  one PDF per changed generation.
- Month locks stay clinic-wide: lockable while an active generation covers the month,
  released when none does any more.

### Added
- Expired generations (last day past) are archived at startup, every night and before each
  new generation; their days remain visible as history.
- Admin: active generations with the locked months they hold; "Archiver" asks for
  confirmation (no unarchiving).

### Security
- Docker image: pip 26.2.2 (its vendored certifi 2026.10.19 drops the compromised emSign
  ECC Root CA - G3 chain) in the build stages; the production image ships no pip.

## [0.12.1] - 2026-10-22

### Security
- certifi 2026.10.19 (was 2026.7.22): emSign CA removed after 8 compromised intermediate
  certificates.
- Pillow 13.0.0 (was 12.3.0, used by WeasyPrint): fixes 29 CVEs, the worst a heap buffer
  overflow leading to remote code execution.

## [0.12.0] - 2026-10-17

### Added
- Desiderata: days a doctor would rather not be on duty (`/desiderata`, own days only,
  audited). Soft constraint: the generator leaves out doctors with a desiderata whenever
  someone else can take the day (even if that doctor was on duty the day before / after);
  when everyone has one, the doctor with the fewest broken desiderata in the period gets
  it. Without desiderata the generator output is unchanged.
- Days given despite a desiderata are stored (`assignment.desiderata_conflict`: generated
  or manual), shown with amber stripes and a tooltip in the calendar, and listed under
  "Conflits avec desiderata" in the results.
- The cell modal flags doctors with a desiderata like doctors on vacation.
- "Desiderata" column in the statistics (results, PDF, Générer).

### Changed
- Privacy policy: desiderata added to the processed data.

## [0.11.4] - 2026-10-16

### Fixed
- CodeQL `py/uninitialized-local-variable`: `changed` initialized before the palette
  branches of the settings page.
- CodeQL `py/url-redirection` (login `next`, admin redirect helper): `safe_next_url` now
  uses the guard CodeQL recognises as a sanitizer (same rules, no behaviour change).

### Changed
- CodeQL workflow: `security-extended` query suite instead of `security-and-quality`, and
  `alembic/versions/**` ignored (Alembic globals were reported as unused).

## [0.11.3] - 2026-10-16

### Security
- `safe_next_url` for every redirect built from request data (login, editing return
  targets, admin redirect helpers): no scheme, host, `//`, `://`, backslash or control
  character.
- Template guard against inline JavaScript rewritten with an HTML parser.

## [0.11.2] - 2026-10-16

### Fixed
- `ruff format` of `app/dependencies.py` (CI); pre-commit hooks run once per commit.

## [0.11.1] - 2026-10-16

### Fixed
- Redirect loop when the terms acceptance and a forced password change are both pending.

## [0.11.0] - 2026-10-15

### Added
- Doctor color palettes per clinic (Par défaut, Nuances de gris, Bleus, Rose et violet),
  WCAG AA with white text; manual color per doctor within the active palette.

## [0.10.1] - 2026-10-15

### Fixed
- iCal feed: "Garde sur place" label, taken from the same day views as the PDF.

## [0.10.0] - 2026-10-15

### Added
- Personal iCal subscription feed of each doctor's duties (`/ical/{token}`).

## [0.9.2] - 2026-10-15

### Added
- `/health` liveness endpoint for external monitoring (not written to the access log).

## [0.9.1] - 2026-10-15

### Fixed
- A failed HTMX request (e.g. a CDN challenge) falls back to the full page instead of
  doing nothing.

## [0.9.0] - 2026-10-15

### Fixed
- Tapping a day of a locked month explains why it cannot be edited (touch screens).

## [0.8.0] - 2026-10-14

### Added
- Retroactive acceptance of the CGU / privacy policy for existing users.
- Opt-out of notification emails (settings toggle, one-click unsubscribe link).
- RCS and VAT numbers in the mentions légales.

### Fixed
- Versioned static URLs, so deployments are not hidden by CDN / browser caches.

## [0.7.0] - 2026-10-14

### Added
- Toasts for flash messages; lock buttons without page reload.

### Changed
- Only months of the active generation can be locked; regeneration releases locks
  outside its period.

## [0.6.0] - 2026-10-14

### Added
- Mentions légales, politique de confidentialité and CGU pages; mandatory acceptance on
  the invitation form.

## [0.5.0] - 2026-10-14

### Added
- Group admin role and monthly locks.

### Changed
- Schedule email subject "Gardes {groupe} du {début} au {fin}"; on-site rows always red
  in the changelog.

## [0.4.0] - 2026-10-14

### Added
- Changelog of the changes against the previous generation in the regeneration email.

### Changed
- Calendar navigation by 8-month blocks; Générer defaults to today → end of month +6.

## [0.3.0] - 2026-10-14

### Added
- Invitation emails for new users; name editing (self-service and admin).

### Changed
- Greeting under the header title; 8-month window from the current month on Congés /
  Sur place.

## [0.2.0] - 2026-10-14

### Changed
- "Cabinet" renamed "Groupe" in the French UI; greeting with the user's name.

## [0.1.0] - 2026-10-12

### Added
- First release.

[0.13.1]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.11.4...v0.12.0
[0.11.4]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.11.3...v0.11.4
[0.11.3]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.11.2...v0.11.3
[0.11.2]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.10.1...v0.11.0
[0.10.1]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.9.2...v0.10.0
[0.9.2]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Arrakis-IT/planificateur-gardes/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Arrakis-IT/planificateur-gardes/releases/tag/v0.1.0
