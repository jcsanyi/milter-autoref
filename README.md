# milter-autoref

A Postfix milter that appends the current message's `Message-ID` value to the `References`
header for outgoing messages - fixing email threading that's broken by mail relays that
rewrite the `Message-ID` header.

**au·to** /ˈɔː.toʊ/ *prefix*
1. **automatic** - happening without any manual intervention
2. **self** - referring to oneself

The `autoref` name refers to both meanings: this milter *automatically* adds a reference
to *itself* to every outgoing message.

## Why?

Some mail relays (like AWS SES) rewrite the `Message-ID` header on every
message they handle. When a recipient replies, their mail client puts the
relay's rewritten ID in `In-Reply-To` and `References` - but your mail client
only knows about the original ID. It can't match anything in the `References`
header to an existing message, so the first reply appears unthreaded.

Before the message leaves Postfix, this milter will append the current `Message-ID`
to the `References` header. The recipient's reply will then include both the original and
rewritten IDs in `References`, allowing your client to find the original and thread the
reply correctly.

## Symptoms

Consider this milter if you run a **self-hosted Postfix** instance with outgoing
mail routed through AWS SES (or a similar relay), and you're seeing any
of the following:

- Replies to your sent messages appear as **new threads** or **new
  conversations** instead of continuing the original
- In **Gmail**, the reply shows up on its own rather than joining the
  existing conversation
- In **Thunderbird**, **Outlook**, or **Apple Mail**, the threaded view
  is broken — the first reply looks unthreaded
- In a self-hosted **customer support** or **helpdesk** system, replies
  from customers create **new tickets** instead of being matched back to
  the original
- Only the sender side is affected — your recipients' mail clients
  thread replies fine

The following relays are known or reported to rewrite `Message-ID` on
outgoing mail, and may benefit from this milter:

- **AWS SES** — rewrites by default
- **Postmark** — rewrites by default; preserve with the `X-PM-KeepID: true` SMTP header
- **SendGrid**, **Mailgun**, and **Brevo** (formerly **Sendinblue**) — similar behavior reported

## Requirements

- Python ≥ 3.10
- `libmilter` (C library, e.g. `libmilter` on Arch, `libmilter-dev` on
  Debian/Ubuntu, or install `sendmail-devel` on RHEL)
- Postfix

The milter protocol originated in Sendmail and is supported by several
MTAs, but this milter has only been tested with Postfix. If you've run
it successfully against another MTA, please open an issue or PR so this
note can be updated.

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

## Docker

The image is published at <https://hub.docker.com/r/jcsanyi/milter-autoref>.

Run with Docker:

```
docker run -d --name milter-autoref -p 8890:8890 jcsanyi/milter-autoref:latest
```

Or with docker-compose (see `docker-compose.yml` in the repo):

```
docker compose up -d
```

The container listens on `inet:8890` by default. Configure Postfix to
connect to it:

```
# /etc/postfix/main.cf (or per-service override in master.cf)
smtpd_milters = inet:localhost:8890
```

If Postfix runs in the same compose network, use the service name
instead: `smtpd_milters = inet:milter-autoref:8890`.

All configuration is via environment variables (see Configuration below),
passed with `-e` or via the `environment` key in `docker-compose.yml`.

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
| --- | --- | --- |
| **AUTOREF_SOCKET** | /tmp/milter-autoref.sock | Socket to listen on. Unix path, `inet:port@host`, or `inet6:port@host`. Note that the Docker image defaults to inet:8890 instead of the file-based socket. |
| **AUTOREF_AUTH_ONLY** | true | Only rewrite messages that authenticated via SASL (`{auth_type}` or `{auth_authen}` set). Set to `false` if you've scoped the milter to outbound-only traffic via `master.cf`. |
| **AUTOREF_DRY_RUN** | false | Log intended header changes without applying them. |
| **AUTOREF_LOG_LEVEL** | INFO | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| **AUTOREF_TRIM_REFERENCES** | true | Trim the `References` header to at most `AUTOREF_MAX_REFERENCES` tokens, keeping the thread root and most recent ancestors. |
| **AUTOREF_MAX_REFERENCES** | 20 | Maximum number of Message-ID tokens to keep in the `References` header when trimming is enabled. Must be a positive integer. The newly-appended self-reference is always preserved; trimming drops middle ancestors while keeping the thread root and the most recent N-1 entries. Setting this to `1` discards the thread root (only the self-reference remains), which can break threading in archive tools that anchor on root-ID. |

Boolean values accept: `1/true/yes/on` or `0/false/no/off` (case-insensitive).

### Example

```
AUTOREF_SOCKET=/tmp/milter-autoref.sock
AUTOREF_AUTH_ONLY=true
AUTOREF_LOG_LEVEL=INFO
```

## Postfix configuration

### Recommended: scope the milter to the submission service

The simplest deployment pattern — and the one Postfix's own
`SMTPD_MILTER_README` recommends — is to attach the milter only to the
submission service via a per-service `-o smtpd_milters=` override in
`/etc/postfix/master.cf`:

```
# /etc/postfix/master.cf
submission inet n       -       n       -       -       smtpd
  -o syslog_name=postfix/submission
  -o smtpd_tls_security_level=encrypt
  -o smtpd_sasl_auth_enable=yes
  -o smtpd_milters=unix:/tmp/milter-autoref.sock
```

Ensure the SASL auth macros are exported to milters so the default
`AUTOREF_AUTH_ONLY=true` can see them. `milter_mail_macros` must include
*at least* `{auth_type}` and `{auth_authen}`; any additional macros you
already export for other milters are fine to leave in place.

```
# /etc/postfix/main.cf
milter_mail_macros = i {auth_type} {auth_authen}
```

### Alternative: wire globally with the default auth-only gate

If you prefer to add the milter once in `main.cf` and let it see all SMTP
paths, the `AUTOREF_AUTH_ONLY=true` default keeps you safe: unauthenticated
inbound MX traffic passes through untouched because `{auth_type}` and
`{auth_authen}` are only set after a successful SASL AUTH.

```
# /etc/postfix/main.cf
smtpd_milters = unix:/tmp/milter-autoref.sock
milter_default_action = accept
milter_mail_macros = i {auth_type} {auth_authen}
```

### When to set AUTOREF_AUTH_ONLY=false

Disable the auth gate only when you've already restricted the milter to
outbound-only traffic at the MTA layer — for example, when your submission
service relies on IP allowlisting (`permit_mynetworks`) rather than SASL,
and some legitimate outgoing messages arrive without auth macros set. In
that case, scope the milter per-service in `master.cf` and set
`AUTOREF_AUTH_ONLY=false`.

Note that local pickup also does not authenticate via SASL. Messages
submitted via `sendmail -t` from cron jobs, monitoring scripts, `mail(1)`,
and similar sources will be skipped by default. If you want those messages
to be rewritten too, scope the milter appropriately and disable the auth
gate.

## DKIM and milter ordering

If you use SES Easy DKIM (the default — no local DKIM milter), SES signs
the message after milter-autoref has already run, so no special handling
is needed.

If your MTA signs with a local DKIM milter (e.g. OpenDKIM) before
forwarding to SES, milter-autoref must appear before it in `smtpd_milters`.
OpenDKIM signs `References` by default, and if milter-autoref modifies it
afterwards the signature will fail at the recipient. In Postfix,
`smtpd_milters` runs left-to-right, so list milter-autoref first:

```
# /etc/postfix/main.cf
smtpd_milters = unix:/tmp/milter-autoref.sock, inet:localhost:8891
```

## Caveats

* **Message-ID must be set by the client.** Postfix's `cleanup(8)` daemon
  adds a `Message-ID` to messages that don't have one, but it does this
  *after* milters run. If your mail client doesn't set a `Message-ID`,
  milter-autoref will log an INFO message and skip the modification —
  that's correct behaviour. A client that didn't set a Message-ID in the
  first place has no ID in its Sent folder to match replies against, so
  threading would already be broken regardless of what any relay does
  downstream.

## Development

```
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

The test suite has three layers:

- **`tests/test_logic.py`** — pure header-manipulation functions; no `libmilter` required.
- **`tests/test_milter.py`** — full callback chain with the pymilter API mocked out.
- **`tests/test_integration.py`** — live milter over a real Unix socket using [`miltertest`](https://pypi.org/project/miltertest/). Includes a [Hypothesis](https://hypothesis.readthedocs.io/) property-based test that generates random multi-message sequences on a single connection to catch unanticipated state-leakage bugs.

For deeper exploration of the property-based test (e.g. before shipping significant changes to `milter.py` or `logic.py`):

```
HYPOTHESIS_MAX_EXAMPLES=1000 pytest tests/test_integration.py::TestPropertyBased::test_random_message_sequences -v
```
