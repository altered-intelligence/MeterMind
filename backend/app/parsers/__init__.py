"""PDF / document parsers.

``parse_coserv_bill`` is exposed lazily (PEP 562) so that running the parser module as a
script (``python -m app.parsers.coserv_pdf_parser``) does not trigger a re-import warning.
"""

__all__ = ["parse_coserv_bill"]


def __getattr__(name):
    if name == "parse_coserv_bill":
        from .coserv_pdf_parser import parse_coserv_bill

        return parse_coserv_bill
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
