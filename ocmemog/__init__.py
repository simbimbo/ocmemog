"""ocmemog sidecar package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

try:
    __version__ = _package_version("ocmemog-sidecar")
except PackageNotFoundError:  # pragma: no cover - package metadata may be unavailable in source layouts.
    __version__ = "0.1.16"

__all__ = ["__version__"]
