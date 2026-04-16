"""Entry point for milter-autoref.

Runs via:
  milter-autoref          (console script)
  python -m milter_autoref
"""

import logging
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

    log.info("starting milter-autoref on %s (dry_run=%s)", cfg.socket, cfg.dry_run)
    Milter.runmilter("milter-autoref", cfg.socket, timeout=cfg.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
