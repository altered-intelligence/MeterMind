"""Tests for the parser registry + version resolution."""

from __future__ import annotations

import pytest

from app.parsers import registry as r
from app.parsers.coserv_pdf_parser import PARSER_VERSION, SCHEMA_VERSION


def test_coserv_registered_with_expected_identity():
    p = r.get_latest_parser("coserv")
    assert p.provider_name == "coserv"
    assert p.parser_version == PARSER_VERSION == "coserv-v0.1.0"
    assert p.schema_version == SCHEMA_VERSION == "coserv-bill-schema-v1"


def test_get_parser_exact_and_case_insensitive():
    assert r.get_parser("coserv", PARSER_VERSION).parser_version == PARSER_VERSION
    # Provider lookup is case-insensitive (bills store "CoServ").
    assert r.get_parser("CoServ", PARSER_VERSION).parser_version == PARSER_VERSION


def test_unknown_provider_and_version_raise():
    with pytest.raises(r.UnknownParserError):
        r.get_parser("coserv", "coserv-v9.9.9")
    with pytest.raises(r.UnknownParserError):
        r.get_latest_parser("not-a-provider")


def test_version_key_orders_numerically():
    assert r.version_key("coserv-v0.1.0") < r.version_key("coserv-v0.2.0")
    assert r.version_key("coserv-v0.9.0") < r.version_key("coserv-v0.10.0")
    assert r.version_key("nodigits") == (0,)


def test_get_latest_picks_highest_version():
    reg = r.ParserRegistry()

    class _Dummy(r.ProviderParser):
        schema_version = "x-schema-v1"

        def parse(self, pdf_path):  # pragma: no cover - not exercised
            return None

        def extract_text(self, pdf_path):  # pragma: no cover - not exercised
            return ""

    reg.register("acme", "acme-v0.1.0", _Dummy())
    reg.register("acme", "acme-v0.10.0", _Dummy())
    reg.register("acme", "acme-v0.2.0", _Dummy())
    assert reg.get_latest("acme").parser_version == "acme-v0.10.0"
    assert reg.versions("acme") == ["acme-v0.1.0", "acme-v0.2.0", "acme-v0.10.0"]


def test_register_parser_accepts_class_or_instance():
    reg = r.ParserRegistry()

    class _Dummy(r.ProviderParser):
        def parse(self, pdf_path):  # pragma: no cover
            return None

        def extract_text(self, pdf_path):  # pragma: no cover
            return ""

    by_class = reg.register("a", "a-v1.0.0", _Dummy)
    by_inst = reg.register("b", "b-v1.0.0", _Dummy())
    assert isinstance(by_class, r.ProviderParser)
    assert isinstance(by_inst, r.ProviderParser)
    assert by_class.provider_name == "a" and by_class.parser_version == "a-v1.0.0"


def test_coserv_parser_parses_real_layout(coserv_pdf):
    """The registered CoServ parser actually parses a PDF and extracts text."""
    p = r.get_latest_parser("coserv")
    bill = p.parse(coserv_pdf)
    assert bill.parser_version == PARSER_VERSION
    assert bill.account_summary.total_amount_due is not None
    assert p.extract_text(coserv_pdf).strip() != ""
