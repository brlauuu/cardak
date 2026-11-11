"""The installed cardak's version, read from the package metadata once (#121)."""

from functools import cache


@cache
def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("cardak")
    except PackageNotFoundError:  # a source tree that was never installed
        return "dev"
