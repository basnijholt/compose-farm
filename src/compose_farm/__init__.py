"""Compose Farm - run docker compose commands across multiple hosts."""

try:
    from compose_farm._version import __version__, __version_tuple__
except ImportError:
    __version__ = "0.0.0"
    __version_tuple__ = (0, 0, 0)

__all__ = ["__version__", "__version_tuple__"]
