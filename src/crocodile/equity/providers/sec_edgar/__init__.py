"""SEC EDGAR provider implementation."""

from crocodile.equity.providers.sec_edgar.client import (
    COMPANY_TICKERS_URL,
    SecCompanyTicker,
    SecEdgarClient,
    parse_company_tickers,
)
from crocodile.equity.providers.sec_edgar.form4 import Form4ParseError, parse_form4
from crocodile.equity.providers.sec_edgar.form13f import (
    Form13FCoverPage,
    Form13FParseError,
    parse_13f_information_table,
    parse_13f_primary_document,
)

__all__ = [
    "COMPANY_TICKERS_URL",
    "Form4ParseError",
    "Form13FCoverPage",
    "Form13FParseError",
    "SecCompanyTicker",
    "SecEdgarClient",
    "parse_13f_information_table",
    "parse_13f_primary_document",
    "parse_company_tickers",
    "parse_form4",
]
