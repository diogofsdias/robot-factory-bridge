class AdapterError(Exception):
    """Base class for all adapter errors."""


class ConfigError(AdapterError):
    """The factory config is missing, malformed or incomplete."""


class TagError(AdapterError):
    """Unknown canonical tag, or a write to a read-only tag."""


class SafetyError(AdapterError):
    """A command was refused by a safety rule."""
