"""HELEN OS - Multi-Model AI Companion"""

__version__ = "1.0.0"
__author__ = "HELEN OS Team"


def create_app():
    """Lazy import to avoid pulling Flask into every helen_os import."""
    from .api_server import create_app as _create
    return _create()


__all__ = ["create_app"]
