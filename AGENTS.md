# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Persistent memory

Before writing to persistent memory (e.g. the Claude Code per-project memory store), consider whether the guidance belongs in this file instead. AGENTS.md is checked in, visible to human contributors, and shared across every agent session; per-agent memory is none of those things. If a rule is repo-wide and durable rather than personal to one user or session, recommend adding it to AGENTS.md and wait for approval before either saving the memory or editing this file.

## Commits

Do not add AI agent attribution to commits (no `Co-Authored-By` lines). The user owns all code in this repository.

Never run `git commit` or `git push` without an explicit instruction in the current message to do so. Completing a task does not constitute permission to commit — each commit requires a fresh explicit request. If changes are ready, say so and stop.

## Commands

All commands use the virtualenv binaries directly (no need to activate the venv first). This is intentional — the direct-path form (`.venv/bin/pytest`) can be whitelisted in Claude Code permission settings, whereas a `source .venv/bin/activate && pytest` pipeline cannot. README.md uses the `source` idiom because it targets human contributors; don't "reconcile" the two.

```bash
# Install with dev dependencies (requires libmilter C library to be installed first)
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Run all tests
.venv/bin/pytest

# Run a single test file
.venv/bin/pytest tests/test_logic.py

# Run a single test by name
.venv/bin/pytest tests/test_milter.py::TestOutgoingWithExistingReferences::test_chgheader_called_with_correct_index

# Run the milter (dry-run mode recommended for first use)
AUTOREF_DRY_RUN=true AUTOREF_LOG_LEVEL=DEBUG .venv/bin/milter-autoref

# Run the property-based integration test with deeper exploration
# (run this locally after significant changes to milter.py or logic.py)
HYPOTHESIS_MAX_EXAMPLES=1000 .venv/bin/pytest tests/test_integration.py::TestPropertyBased::test_random_message_sequences -v
```

`libmilter` is a system package (`libmilter` on Arch, `libmilter-dev` on Debian/Ubuntu, `sendmail-devel` on RHEL) and must be installed before `pymilter` can be built.

## Architecture

The codebase is split so that the core logic has no pymilter dependency:

- **`logic.py`** — Pure functions only, no `pymilter` import. `compute_new_references` and `is_outgoing` are the primary units. Tests here run without `libmilter` installed.
- **`milter.py`** — `AutorefMilter(Milter.Base)`. Buffers `Message-ID` and `References` header values during `header()` callbacks, then calls the logic functions and applies `addheader`/`chgheader` in `eom()`. Header mutation can only happen in `eom()` — this is a pymilter constraint.
- **`config.py`** — `Config` frozen dataclass populated from env vars via `from_env()`. Parsed and validated at startup; invalid values raise immediately.
- **`__main__.py`** — Wires `Config`, logging, signal handlers, and `Milter.runmilter`. Serves as both the console script entry point and `python -m milter_autoref`.

## Key design decisions

**Fail-closed outgoing detection.** By default (`AUTOREF_AUTH_ONLY=true`), a message is only modified if at least one SASL auth macro (`{auth_type}` or `{auth_authen}`) is set in `envfrom()` — authenticated mail is the only signal we trust. When `AUTOREF_AUTH_ONLY=false`, every message is treated as outgoing, and it's the operator's responsibility to scope the milter to outbound-only traffic via `master.cf`. A false positive on incoming mail would wrongly rewrite someone else's `References`, so the default stays fail-closed.

**`Message-ID` may not be visible.** Postfix's `cleanup(8)` adds `Message-ID` to messages that lack one *after* milters run, so we never see it at `eom()`. This is correct — SES can only break threading on messages where the client set a `Message-ID` in the first place.

**Multiple `References` headers.** When more than one `References` header is present, we track the last one by its 1-based index and modify only that, leaving earlier ones untouched.

 **Dockerfile intentional omissions.** The image omits `EXPOSE` and `HEALTHCHECK` deliberately. The image targets container-to-container deployment where `EXPOSE` is metadata-only and doesn't affect   connectivity — any external consumer who needs the port can read the `ENV AUTOREF_SOCKET=` line. A `HEALTHCHECK` baked into the image would be fragile because `AUTOREF_SOCKET` is operator-configurable (unix path vs `inet:port`), so a check hardcoded for the default breaks on override; orchestrators (compose/k8s) should define their own liveness probes. Don't "add the missing EXPOSE/HEALTHCHECK" — they were considered and rejected.                                                                                                                                             

## Versioning and releases

The package version is derived from git tags via `setuptools-scm` — there is no hardcoded version string in the source. `__init__.py` reads the version at runtime from package metadata.

The `.github/workflows/release.yml` workflow triggers on `v*` tag pushes, runs the test suite, and creates a GitHub Release using `--notes-from-tag` (the annotated tag message becomes the release body).

Between releases, `setuptools-scm` generates dev versions like `0.1.1.dev3+g1a2b3c4` from the commit distance and hash. Never add a hardcoded version string to `pyproject.toml` or `__init__.py`.

### Creating a release (agent instructions)

When the user asks to create a release:

1. Determine the new version (ask the user if not specified).
2. Find the previous release tag: `git describe --tags --abbrev=0`.
3. Read the commits since that tag: `git log <prev_tag>..HEAD --oneline`.
4. Write a short summary (a few bullet points) of the most significant changes since that previous tag. Not every commit needs a bullet — group related changes and focus on what matters to someone using the milter. Start with a line like "Changes since v0.1.0:" to make the baseline clear.
5. Create an annotated tag with the summary as the message: `git tag -a v<version> -m "<summary>"`.
6. Push the tag: `git push origin v<version>`.

The workflow handles the rest (tests + GitHub Release).

## Testing the milter class

pymilter creates one `AutorefMilter` instance per SMTP connection and initialises `_protocol` via the C extension. To test callbacks directly without a real event loop, set `m._protocol = 0` after instantiation — this makes `@Milter.noreply`-decorated callbacks behave as transparent pass-throughs. See `tests/test_milter.py::_make_milter` for the pattern.
