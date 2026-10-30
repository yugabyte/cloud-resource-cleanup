"""Cleaner registry: `(cloud, kind)` to a factory that builds a `Cleaner` from a `RunContext`.

The module-level `register`, `get`, `kinds_for` and `clouds` use `DEFAULT_REGISTRY`.
Tests build their own `Registry()` so registrations never leak between tests.
"""

from collections.abc import Callable
from typing import TypeVar

from crc.core.cleaner import Cleaner, RunContext

CleanerFactory = Callable[[RunContext], Cleaner]
F = TypeVar("F", bound=CleanerFactory)


class DuplicateCleanerError(ValueError):
    """The same `(cloud, kind)` was registered twice."""


class UnknownCleanerError(LookupError):
    """No cleaner is registered for the requested cloud or kind."""


class Registry:
    """Holds cleaner factories. No import-time cloud calls, no other state."""

    def __init__(self) -> None:
        self._factories: dict[tuple[str, str], CleanerFactory] = {}

    def register(self, cloud: str, kind: str) -> Callable[[F], F]:
        """Decorator that stores the factory under `(cloud, kind)` and returns it unchanged."""
        if not cloud or not kind:
            raise ValueError(f"cloud and kind must be non-empty (got {cloud!r}, {kind!r})")

        def decorator(factory: F) -> F:
            key = (cloud, kind)
            if key in self._factories:
                existing = self._factories[key]
                raise DuplicateCleanerError(
                    f"cleaner already registered for cloud={cloud!r} kind={kind!r}: "
                    f"{existing!r}; refusing to register {factory!r}"
                )
            self._factories[key] = factory
            return factory

        return decorator

    def get(self, cloud: str, kind: str) -> CleanerFactory:
        """Factory registered for `(cloud, kind)`; raises `UnknownCleanerError` otherwise."""
        factory = self._factories.get((cloud, kind))
        if factory is None:
            known = self.kinds_for(cloud) if cloud in self.clouds() else ()
            raise UnknownCleanerError(
                f"no cleaner registered for cloud={cloud!r} kind={kind!r}; "
                f"known clouds: {list(self.clouds())}; known kinds for {cloud!r}: {list(known)}"
            )
        return factory

    def kinds_for(self, cloud: str) -> tuple[str, ...]:
        """Sorted kinds registered for `cloud`; raises `UnknownCleanerError` for unknown clouds."""
        kinds = sorted(kind for registered, kind in self._factories if registered == cloud)
        if not kinds:
            raise UnknownCleanerError(
                f"no cleaners registered for cloud={cloud!r}; known clouds: {list(self.clouds())}"
            )
        return tuple(kinds)

    def clouds(self) -> tuple[str, ...]:
        """Sorted clouds that have at least one cleaner."""
        return tuple(sorted({cloud for cloud, _ in self._factories}))


DEFAULT_REGISTRY = Registry()


def register(cloud: str, kind: str) -> Callable[[F], F]:
    """Register a cleaner factory in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.register(cloud, kind)


def get(cloud: str, kind: str) -> CleanerFactory:
    """Look up a factory in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.get(cloud, kind)


def kinds_for(cloud: str) -> tuple[str, ...]:
    """Kinds registered for `cloud` in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.kinds_for(cloud)


def clouds() -> tuple[str, ...]:
    """Clouds registered in `DEFAULT_REGISTRY`."""
    return DEFAULT_REGISTRY.clouds()
