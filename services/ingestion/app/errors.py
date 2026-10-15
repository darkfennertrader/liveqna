class RegistryError(RuntimeError):
    """Base class for document registry errors."""


class RegistryNotFoundError(RegistryError):
    """Requested registry entity does not exist."""


class RegistryConflictError(RegistryError):
    """Requested operation conflicts with existing registry state."""


class InvalidLifecycleStateError(RegistryConflictError):
    """Requested lifecycle transition is not allowed."""
