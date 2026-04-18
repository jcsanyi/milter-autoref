"""Entry point for milter-autoref.

Runs via:
  milter-autoref          (console script)
  python -m milter_autoref
"""

import logging
import os
import signal

import Milter

from .config import Config
from .milter import AutorefMilter


def main() -> int:
    cfg = Config.from_env()

    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("milter_autoref")

    Milter.factory = lambda: AutorefMilter(cfg, log)
    Milter.set_flags(Milter.CHGHDRS | Milter.ADDHDRS)

    def _handle_stop(signum, frame):
        log.info("received signal %d, stopping", signum)
        Milter.stop()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if not cfg.socket.startswith(("inet:", "inet6:")):
        socket_dir = os.path.dirname(cfg.socket)
        if socket_dir:
            try:
                existed = os.path.isdir(socket_dir)
                os.makedirs(socket_dir, exist_ok=True)
                if not existed:
                    log.debug("created socket directory %s", socket_dir)
            except OSError as exc:
                log.error("cannot create socket directory %s: %s", socket_dir, exc)
                return 1

    log.info(
        "starting milter-autoref on %s (dry_run=%s, auth_only=%s, "
        "trim_references=%s, max_references=%d)",
        cfg.socket,
        cfg.dry_run,
        cfg.auth_only,
        cfg.trim_references,
        cfg.max_references,
    )
    Milter.runmilter("milter-autoref", cfg.socket)
    log.info("milter stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
