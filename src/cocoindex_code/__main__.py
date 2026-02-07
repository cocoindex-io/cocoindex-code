"""Entry point for `python -m cocoindex_code`."""

import asyncio

from .server import main

if __name__ == "__main__":
    asyncio.run(main())
