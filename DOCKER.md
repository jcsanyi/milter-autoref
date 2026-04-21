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

## Quick start

```
docker run -d --name milter-autoref -p 8890:8890 jcsanyi/milter-autoref:latest
```

Then configure Postfix to use the milter:

```
# /etc/postfix/main.cf
smtpd_milters = inet:localhost:8890
milter_mail_macros = i {auth_type} {auth_authen} {mail_addr}
```

## Docker Compose

```yaml
services:
  milter-autoref:
    image: jcsanyi/milter-autoref:latest
    restart: unless-stopped
    environment:
      - AUTOREF_AUTH_ONLY=true
      - AUTOREF_LOG_LEVEL=INFO
```

If Postfix runs in the same compose network, use the service name:
`smtpd_milters = inet:milter-autoref:8890`

If Postfix runs on the host, add `ports: ["8890:8890"]` and use:
`smtpd_milters = inet:localhost:8890`

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
| --- | --- | --- |
| **AUTOREF_SOCKET** | inet:8890@0.0.0.0 | Socket to listen on. Override to use a Unix socket or different port. |
| **AUTOREF_AUTH_ONLY** | true | Only rewrite messages with SASL authentication. Set to `false` if scoped to outbound-only traffic. |
| **AUTOREF_DRY_RUN** | false | Log intended changes without applying them. |
| **AUTOREF_LOG_LEVEL** | INFO | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| **AUTOREF_TRIM_REFERENCES** | true | Trim `References` to at most `AUTOREF_MAX_REFERENCES` tokens. |
| **AUTOREF_MAX_REFERENCES** | 20 | Maximum tokens to keep in `References` when trimming. The self-reference is always preserved; trimming keeps the thread root and the most recent N-1 entries. Setting to `1` discards the thread root. |

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

## Source

[https://github.com/jcsanyi/milter-autoref](https://github.com/jcsanyi/milter-autoref)
