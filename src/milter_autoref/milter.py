"""AutorefMilter — pymilter Milter.Base subclass."""

import logging

import Milter

from .config import Config
from .logic import compute_new_references, extract_message_id_token, is_outgoing


class AutorefMilter(Milter.Base):
    """Postfix milter that appends the outgoing Message-ID to the References header.

    One instance is created per SMTP connection by pymilter's threading model.
    All mutable state is per-instance; no locking is required.
    """

    def __init__(self, config: Config, log: logging.Logger) -> None:
        super().__init__()
        self._cfg = config
        self._log = log
        self._reset()

    # ------------------------------------------------------------------
    # Per-message state helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._outgoing: bool = False
        self._message_id_value: str | None = None
        self._references_count: int = 0
        self._references_last_index: int = 0
        self._references_last_value: str | None = None

    # ------------------------------------------------------------------
    # Milter callbacks
    # ------------------------------------------------------------------

    @Milter.noreply
    def connect(self, hostname, family, hostaddr):
        self._reset()
        return Milter.CONTINUE

    @Milter.noreply
    def envfrom(self, mailfrom, *esmtp):
        self._outgoing = is_outgoing(
            self.getsymval("{daemon_name}"),
            self.getsymval("{auth_type}"),
            self.getsymval("{auth_authen}"),
            self.getsymval("{client_addr}"),
            outgoing_daemons=self._cfg.outgoing_daemons,
            trust_auth=self._cfg.trust_auth,
            internal_hosts=self._cfg.internal_hosts,
        )
        self._log.debug(
            "envfrom: from=%s outgoing=%s daemon_name=%r auth_type=%r client_addr=%r",
            mailfrom,
            self._outgoing,
            self.getsymval("{daemon_name}"),
            self.getsymval("{auth_type}"),
            self.getsymval("{client_addr}"),
        )
        return Milter.CONTINUE

    @Milter.noreply
    def header(self, name, value):
        if not self._outgoing:
            return Milter.CONTINUE
        lname = name.lower()
        if lname == "message-id":
            self._message_id_value = value  # last one wins
        elif lname == "references":
            self._references_count += 1
            self._references_last_index = self._references_count
            self._references_last_value = value
        return Milter.CONTINUE

    @Milter.noreply
    def eoh(self):
        return Milter.CONTINUE

    @Milter.noreply
    def body(self, chunk):
        return Milter.CONTINUE

    def eom(self):
        if not self._outgoing:
            return Milter.CONTINUE

        mid = extract_message_id_token(self._message_id_value or "")
        if mid is None:
            self._log.info(
                "no Message-ID token visible at eom; skipping "
                "(Postfix cleanup(8) may add one after milters run)"
            )
            return Milter.CONTINUE

        new_refs = compute_new_references(mid, self._references_last_value)
        if new_refs is None:
            self._log.debug("References already contains %s; no change needed", mid)
            return Milter.CONTINUE

        if self._cfg.dry_run:
            self._log.info(
                "DRY-RUN: would set References=%r (references_count=%d)",
                new_refs,
                self._references_count,
            )
            return Milter.CONTINUE

        try:
            if self._references_count == 0:
                self._log.debug("addheader References=%r", new_refs)
                self.addheader("References", new_refs)
            else:
                # chgheader replaces the Nth occurrence of the named header (1-based).
                # When there are multiple References headers we modify the last one
                # and leave any earlier ones untouched.
                self._log.debug(
                    "chgheader References[%d]=%r", self._references_last_index, new_refs
                )
                self.chgheader("References", self._references_last_index, new_refs)
        except Exception as exc:
            self._log.warning("header modification failed: %s", exc)

        return Milter.CONTINUE

    def abort(self):
        self._reset()
        return Milter.CONTINUE

    def close(self):
        return Milter.CONTINUE
