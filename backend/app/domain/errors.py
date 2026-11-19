class DomainError(ValueError):
    """A request the domain rejects (invalid input, rule not met). Tools return it to the LLM."""


class NotFoundError(LookupError):
    """A record that does not exist."""
