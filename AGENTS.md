# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Commits

Do not add AI agent attribution to commits (no `Co-Authored-By` lines). The user owns all code in this repository.

Never run `git commit` or `git push` without an explicit instruction in the current message to do so. Completing a task does not constitute permission to commit — each commit requires a fresh explicit request. If changes are ready, say so and stop.

## Commands

```bash
# Install with dev dependencies (requires libmilter C library to be installed first)
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

# Run all tests
pytest

# Run a single test file
pytest tests/test_logic.py

# Run a single test by name
pytest tests/test_milter.py::TestOutgoingWithExistingReferences::test_chgheader_called_with_correct_index

# Run the milter (dry-run mode recommended for first use)
AUTOREF_DRY_RUN=true AUTOREF_LOG_LEVEL=DEBUG milter-autoref

# Run the property-based integration test with deeper exploration
# (run this locally after significant changes to milter.py or logic.py)
HYPOTHESIS_MAX_EXAMPLES=1000 pytest tests/test_integration.py::TestPropertyBased::test_random_message_sequences -v
```

`libmilter` is a system package (`libmilter` on Arch, `libmilter-dev` on Debian/Ubuntu, `sendmail-devel` on RHEL) and must be installed before `pymilter` can be built.

## Architecture

The codebase is split so that the core logic has no pymilter dependency:

- **`logic.py`** — Pure functions only, no `pymilter` import. `compute_new_references` and `is_outgoing` are the primary units. Tests here run without `libmilter` installed.
- **`milter.py`** — `AutorefMilter(Milter.Base)`. Buffers `Message-ID` and `References` header values during `header()` callbacks, then calls the logic functions and applies `addheader`/`chgheader` in `eom()`. Header mutation can only happen in `eom()` — this is a pymilter constraint.
- **`config.py`** — `Config` frozen dataclass populated from env vars via `from_env()`. Parsed and validated at startup; invalid values raise immediately.
- **`__main__.py`** — Wires `Config`, logging, signal handlers, and `Milter.runmilter`. Serves as both the console script entry point and `python -m milter_autoref`.

## Key design decisions

**Fail-closed outgoing detection.** A message is only modified if at least one signal identifies it as outgoing: `{daemon_name}` macro in the configured set, SASL auth macros present (if `AUTOREF_TRUST_AUTH=true`), or `{client_addr}` within a configured CIDR. All macros are read in `envfrom()`. If nothing matches, the message is passed through untouched — a false positive on incoming mail would wrongly modify someone else's `References`.

**`Message-ID` may not be visible.** Postfix's `cleanup(8)` adds `Message-ID` to messages that lack one *after* milters run, so we never see it at `eom()`. This is correct — SES can only break threading on messages where the client set a `Message-ID` in the first place.

**Multiple `References` headers.** When more than one `References` header is present, we track the last one by its 1-based index and modify only that, leaving earlier ones untouched.

## Testing the milter class

pymilter creates one `AutorefMilter` instance per SMTP connection and initialises `_protocol` via the C extension. To test callbacks directly without a real event loop, set `m._protocol = 0` after instantiation — this makes `@Milter.noreply`-decorated callbacks behave as transparent pass-throughs. See `tests/test_milter.py::_make_milter` for the pattern.
