"""Provider parser registry + versioning.

CoServ tweaks its bill layout over time, so the app must record *which* parser (provider +
version + output schema) processed each bill, and be able to reprocess a stored PDF with the
latest — or a pinned — parser version when the extraction logic improves.

This module is the single source of truth for the available parsers:

* :class:`ProviderParser` — a thin, uniform wrapper around a deterministic parse function,
  carrying its identifying metadata (``provider_name``, ``parser_version``,
  ``schema_version``).
* :class:`ParserRegistry` — maps ``provider -> {version -> ProviderParser}`` and resolves the
  latest version via numeric version-key comparison.
* Module-level helpers :func:`register_parser`, :func:`get_parser`,
  :func:`get_latest_parser` operate on a shared default registry.

The current CoServ parser is registered at import time at the bottom of this file.
"""

from __future__ import annotations

import re
from typing import Optional, Type, Union


class ProviderParser:
    """A registered parser for one provider + version.

    Subclasses set the three class attributes and implement :meth:`parse` /
    :meth:`extract_text`. The registry sets ``provider_name`` / ``parser_version`` from the
    registration call so they always agree with the lookup keys.
    """

    provider_name: str = ""
    parser_version: str = ""
    schema_version: str = ""
    description: str = ""

    def parse(self, pdf_path: str):
        """Parse a PDF at ``pdf_path`` into the provider's parsed-bill object."""
        raise NotImplementedError

    def extract_text(self, pdf_path: str) -> str:
        """Return the extracted (redacted-safe to hash, never logged) text of the PDF."""
        raise NotImplementedError

    @property
    def metadata(self) -> dict:
        return {
            "provider_name": self.provider_name,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "description": self.description,
        }


def version_key(version: str) -> tuple[int, ...]:
    """Sortable key from a version string, e.g. ``"coserv-v0.1.0" -> (0, 1, 0)``.

    Non-numeric decoration is ignored; a version with no digits sorts lowest.
    """
    nums = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in nums) if nums else (0,)


class UnknownParserError(KeyError):
    """Raised when a requested provider or version is not registered."""


class ParserRegistry:
    def __init__(self) -> None:
        self._by_provider: dict[str, dict[str, ProviderParser]] = {}

    @staticmethod
    def _norm(provider_name: str) -> str:
        return (provider_name or "").strip().lower()

    def register(
        self,
        provider_name: str,
        parser_version: str,
        parser: Union[ProviderParser, Type[ProviderParser]],
    ) -> ProviderParser:
        """Register a parser instance (or class) under ``provider_name``/``parser_version``.

        Re-registering the same provider+version replaces it (idempotent on import).
        """
        inst = parser() if isinstance(parser, type) else parser
        # The registration call is authoritative for the identity keys.
        inst.provider_name = provider_name
        inst.parser_version = parser_version
        self._by_provider.setdefault(self._norm(provider_name), {})[parser_version] = inst
        return inst

    def get(self, provider_name: str, version: str) -> ProviderParser:
        prov = self._by_provider.get(self._norm(provider_name))
        if not prov or version not in prov:
            raise UnknownParserError(
                f"No parser registered for provider '{provider_name}' version '{version}'."
            )
        return prov[version]

    def get_latest(self, provider_name: str) -> ProviderParser:
        prov = self._by_provider.get(self._norm(provider_name))
        if not prov:
            raise UnknownParserError(f"No parser registered for provider '{provider_name}'.")
        latest = max(prov.keys(), key=version_key)
        return prov[latest]

    def has_provider(self, provider_name: str) -> bool:
        return self._norm(provider_name) in self._by_provider

    def providers(self) -> list[str]:
        return sorted(self._by_provider.keys())

    def versions(self, provider_name: str) -> list[str]:
        """Registered versions for a provider, oldest first."""
        prov = self._by_provider.get(self._norm(provider_name))
        if not prov:
            raise UnknownParserError(f"No parser registered for provider '{provider_name}'.")
        return sorted(prov.keys(), key=version_key)

    def all_parsers(self, provider_name: str) -> list[ProviderParser]:
        return [self.get(provider_name, v) for v in self.versions(provider_name)]


# Shared default registry + module-level convenience API (matches the task spec signatures).
_registry = ParserRegistry()


def get_registry() -> ParserRegistry:
    return _registry


def register_parser(
    provider_name: str,
    parser_version: str,
    parser_class: Union[ProviderParser, Type[ProviderParser]],
) -> ProviderParser:
    return _registry.register(provider_name, parser_version, parser_class)


def get_parser(provider_name: str, version: str) -> ProviderParser:
    return _registry.get(provider_name, version)


def get_latest_parser(provider_name: str) -> ProviderParser:
    return _registry.get_latest(provider_name)


# --------------------------------------------------------------------------------------
# Current CoServ parser registration
# --------------------------------------------------------------------------------------


class CoservParser(ProviderParser):
    """Wraps the deterministic CoServ PDF parser (pdfplumber-based, no LLM)."""

    schema_version = "coserv-bill-schema-v1"
    description = "Deterministic pdfplumber parser for CoServ combined electric + gas bills."

    def parse(self, pdf_path: str):
        from .coserv_pdf_parser import parse_coserv_bill

        return parse_coserv_bill(pdf_path)

    def extract_text(self, pdf_path: str) -> str:
        from .coserv_pdf_parser import extract_text

        return extract_text(pdf_path)


def _bootstrap() -> None:
    """Register the built-in parsers. Safe to call repeatedly (idempotent)."""
    from .coserv_pdf_parser import PARSER_VERSION, SCHEMA_VERSION

    parser = CoservParser()
    parser.schema_version = SCHEMA_VERSION
    register_parser("coserv", PARSER_VERSION, parser)


_bootstrap()
