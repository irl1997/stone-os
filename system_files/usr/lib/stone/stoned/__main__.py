"""Entry point.

  stoned               run the service (stoned.service)
  stoned --recover     boot-time recovery, never touches the hardware (stoned-recover.service)
  stoned --mark-clean  orderly shutdown marker (ExecStop of stoned-recover.service)
"""
import json
import sys

from . import recover, state


def main(argv):
    if "--recover" in argv:
        print(json.dumps(recover.recover(state.Store())), flush=True)
        return 0
    if "--mark-clean" in argv:
        recover.mark_clean_shutdown(state.Store())
        return 0
    from . import service
    service.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
