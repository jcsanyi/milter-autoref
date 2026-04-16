# milter-autoref

A Postfix milter that appends the outgoing `Message-ID` to the `References`
header, fixing email threading broken by mail relays that rewrite the
`Message-ID` header (such as AWS SES).

**au·to** /ˈɔː.toʊ/ *prefix*
1. **self** - referring to oneself
2. **automatic** - happening without any manual intervention

The `autoref` name captures both: this milter *automatically* adds a *self-reference*
to every outgoing message before it leaves Postfix.

## The problem this solves

Some mail relays rewrite the `Message-ID` header on every
message they handle. When a recipient replies, their mail client puts the
relay's rewritten ID in `In-Reply-To` and `References` - but your mail client
only knows about the original ID. It can't match anything in the `References`
header to an existing message, so the first reply appears unthreaded.

Before the message leaves Postfix, this milter will append the current `Message-ID`
to the `References` header. The recipient's reply will then include both the original and
rewritten IDs in `References`, allowing your client to find the original and  thread the
reply correctly.

## Requirements

- Python ≥ 3.10
- `libmilter` (C library, e.g. `libmilter` on Arch, `libmilter-dev` on
  Debian/Ubuntu, or install `sendmail-devel` on RHEL)
- Postfix

## Installation

First, install the `libmilter` system library using your OS package manager
(see Requirements above). This is needed to build `pymilter` and cannot be
handled by pip.

Then install from source using `pipx`, which handles the Python environment
automatically and adds `milter-autoref` to your PATH:

```
git clone https://github.com/jcsanyi/milter-autoref
cd milter-autoref
pipx install .
```

If you don't have `pipx`, install it with your OS package manager
(`python-pipx` on Arch, `pipx` on Debian/Ubuntu) or via `pip install pipx`.

## Running

```
milter-autoref
```

Or:

```
python -m milter_autoref
```

The milter listens on `AUTOREF_SOCKET` (default:
`/tmp/milter-autoref.sock`) and blocks until it receives SIGTERM or
SIGINT.

For a first deployment, start with dry-run mode to verify the intended header
changes without applying them:

```
AUTOREF_DRY_RUN=true AUTOREF_LOG_LEVEL=DEBUG milter-autoref
```

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|---|---|---|
| `AUTOREF_SOCKET` | `/tmp/milter-autoref.sock` | pymilter address string. Unix path, `inet:port@host`, or `inet6:port@host`. |
| `AUTOREF_OUTGOING_DAEMONS` | `ORIGINATING` | Comma-separated `{daemon_name}` values that identify outgoing mail. |
| `AUTOREF_TRUST_AUTH` | `true` | Treat SASL-authenticated connections as outgoing (`{auth_type}` or `{auth_authen}` is set). |
| `AUTOREF_INTERNAL_HOSTS` | *(empty)* | Comma-separated CIDRs. Clients in these ranges are treated as outgoing. |
| `AUTOREF_DRY_RUN` | `false` | Log intended header changes without applying them. |
| `AUTOREF_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `AUTOREF_TIMEOUT` | `600` | Milter timeout in seconds. |

Boolean values accept: `1/true/yes/on` or `0/false/no/off` (case-insensitive).

### Example

```
AUTOREF_SOCKET=/tmp/milter-autoref.sock
AUTOREF_OUTGOING_DAEMONS=ORIGINATING
AUTOREF_INTERNAL_HOSTS=172.16.0.0/12, 127.0.0.1/32
AUTOREF_TRUST_AUTH=true
AUTOREF_LOG_LEVEL=INFO
```

## Postfix configuration

### Outgoing-mail detection

The key to distinguishing outgoing from incoming mail is the `{daemon_name}`
macro, which Postfix sets per-service in `master.cf`.

Add `-o milter_macro_daemon_name=ORIGINATING` to your submission service in
`/etc/postfix/master.cf`:

```
# /etc/postfix/master.cf
submission inet n       -       n       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o milter_macro_daemon_name=ORIGINATING
  -o smtpd_milters=unix:/tmp/milter-autoref.sock
```

If you also want `AUTOREF_INTERNAL_HOSTS` to work (for clients that bypass
SASL auth via IP allowlist), enable the `{client_addr}` macro:

```
# /etc/postfix/main.cf
milter_connect_macros = j {daemon_name} {client_addr}
milter_mail_macros = i {auth_type} {auth_authen} {mail_addr}
```

### Applying to outgoing mail only

Use `smtpd_milters` on the submission service (as above) rather than
`non_smtpd_milters` or a global `smtpd_milters`. This restricts the milter
to authenticated/submission traffic and avoids running it on inbound relayed
mail.

If you want the milter on all SMTP paths and rely solely on macro-based
detection, add it to `main.cf`:

```
smtpd_milters = unix:/tmp/milter-autoref.sock
milter_default_action = accept
```

### Caveats

- **Message-ID must be set by the client.** Postfix's `cleanup(8)` daemon
  adds a `Message-ID` to messages that don't have one, but it does this
  *after* milters run. If your mail client doesn't set a `Message-ID`,
  milter-autoref will log an INFO message and skip the modification - that's
  correct behaviour, since threading is only affected on messages where the
  relay rewrote a `Message-ID` that the client originally set.

- **Fail-closed detection.** If none of the configured outgoing signals match
  (daemon name, auth macros, internal hosts), the milter will not modify the
  message. This is intentional: a false positive on an incoming message would
  wrongly modify someone else's `References` header.

## Planned improvements

These are out of scope for v1 but captured here for future reference.

- **Configurable log destination.** Add an `AUTOREF_LOG_DEST` env var
  accepting `stderr` (default), `stdout`, `syslog`, or a file path. Syslog
  would use the `LOG_MAIL` facility, which is conventional for mail tooling
  and routes to `/var/log/mail.log` on most systems. The current default of
  stderr is correct for Docker and systemd, so this is purely a flexibility
  add for bare-metal deployments.

- **References header length management.** Long email threads can produce a
  `References` header that exceeds RFC 5322's recommended 998-character line
  limit, and some MTAs or clients cap it further. A future
  `AUTOREF_MAX_REFERENCES_BYTES` option would trim the header when it
  exceeds the limit, using a "keep first + last-N tokens" policy to preserve
  the thread root and the most recent ancestors - the two parts MUAs actually
  use for threading.

## Development

```
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Tests in `tests/test_logic.py` cover the pure header-manipulation functions
and require no libmilter. Tests in `tests/test_milter.py` mock the pymilter
API and exercise the full callback chain.
