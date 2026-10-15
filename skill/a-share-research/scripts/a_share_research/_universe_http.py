"""One permission classification for universe-owned HTTP acquisitions."""

from urllib.error import HTTPError

from .identity_sources import TransportError


def is_permission_denied(error: TransportError) -> bool:
    """Inspect typed status/code only; never return provider exception text."""
    return error.code == "source_access_blocked" or (
        isinstance(error.__cause__, HTTPError) and error.__cause__.code in {401, 403}
    )
