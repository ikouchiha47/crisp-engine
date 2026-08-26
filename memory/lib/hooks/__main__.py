"""Enables `python -m lib.hooks <event>` — same entry point as the
`crisp-hook` console script (lib.hooks:main)."""
from . import main

if __name__ == "__main__":
    main()
