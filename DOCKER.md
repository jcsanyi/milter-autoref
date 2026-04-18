# milter-autoref

A Postfix milter that appends the current message's `Message-ID` to the
`References` header for outgoing messages — fixing email threading broken
by mail relays like AWS SES that rewrite the `Message-ID`.

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
|---|---|---|
| `AUTOREF_SOCKET` | `inet:8890@0.0.0.0` | Socket to listen on. Override to use a Unix socket or different port. |
| `AUTOREF_AUTH_ONLY` | `true` | Only rewrite messages with SASL authentication. Set to `false` if scoped to outbound-only traffic. |
| `AUTOREF_DRY_RUN` | `false` | Log intended changes without applying them. |
| `AUTOREF_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `AUTOREF_TRIM_REFERENCES` | `true` | Trim `References` to at most `AUTOREF_MAX_REFERENCES` tokens. |
| `AUTOREF_MAX_REFERENCES` | `20` | Maximum tokens to keep in `References` when trimming. |

## Source

[github.com/jcsanyi/milter-autoref](https://github.com/jcsanyi/milter-autoref)
