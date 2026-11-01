"""python -m acquisition {run|update|backfill}"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "backfill":
    from .backfill.__main__ import main as backfill_main

    raise SystemExit(backfill_main(sys.argv[2:]))

from .cli import main  # noqa: E402

raise SystemExit(main())
