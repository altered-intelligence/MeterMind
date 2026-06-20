"""Read/serialize the parser registry for the ``/api/parsers`` metadata endpoints.

This is a thin presentation layer over :mod:`app.parsers.registry`: it turns registered
:class:`ProviderParser` objects into the API's :class:`ParserInfo` / :class:`ProviderParsers`
schemas, marking which version is the latest for each provider.
"""

from __future__ import annotations

from typing import Optional

from ..parsers import registry as parser_registry
from ..schemas import ParserInfo, ProviderParsers


def _provider_payload(provider_name: str) -> ProviderParsers:
    reg = parser_registry.get_registry()
    latest = reg.get_latest(provider_name)
    versions = reg.all_parsers(provider_name)  # oldest -> newest
    infos = [
        ParserInfo(
            provider_name=p.provider_name,
            parser_version=p.parser_version,
            schema_version=p.schema_version,
            description=p.description,
            is_latest=(p.parser_version == latest.parser_version),
        )
        for p in versions
    ]
    return ProviderParsers(
        provider_name=provider_name,
        latest_version=latest.parser_version,
        schema_version=latest.schema_version,
        versions=infos,
    )


def list_providers() -> list[dict]:
    """All providers with their registered versions (newest marked ``is_latest``)."""
    reg = parser_registry.get_registry()
    return [_provider_payload(name).model_dump(mode="json") for name in reg.providers()]


def get_provider(provider_name: str) -> Optional[dict]:
    """One provider's parsers, or ``None`` if the provider is not registered."""
    if not parser_registry.get_registry().has_provider(provider_name):
        return None
    return _provider_payload(provider_name).model_dump(mode="json")


def get_provider_latest(provider_name: str) -> Optional[dict]:
    """The latest parser version for a provider, or ``None`` if not registered."""
    reg = parser_registry.get_registry()
    if not reg.has_provider(provider_name):
        return None
    latest = reg.get_latest(provider_name)
    return ParserInfo(
        provider_name=latest.provider_name,
        parser_version=latest.parser_version,
        schema_version=latest.schema_version,
        description=latest.description,
        is_latest=True,
    ).model_dump(mode="json")
