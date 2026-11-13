"""Allow python -m rpa as an alias for python -m rpa.formfill."""

from .formfill import main

if __name__ == "__main__":
    raise SystemExit(main())

