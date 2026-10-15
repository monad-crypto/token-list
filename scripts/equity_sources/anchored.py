"""Anchored listings from the Anchored token documentation.

Anchored's symbols API needs a partner key and an IP allowlist, which CI runners
cannot satisfy, so the source is the Markdown version of their docs page. Parsing
is strict: any change to the page's shape fails the source instead of guessing.
"""

import re

import requests
from utils.equities import (
    SOURCE_HTTP_TIMEOUT_SECONDS,
    EquitySource,
    Listing,
    create_source_session,
)
from utils.token_list import CrossChainId
from web3 import Web3

ANCHORED_TOKENS_URL = "https://docs.anchored.finance/getting-started/anchored-tokens.md"
ANCHORED_LOGO_BASE_URL = "https://img.anchored.finance/logos"
STOCKS_SECTION_PATTERN = re.compile(r"### Tokenized stocks(.*?)### Tokenized funds", re.DOTALL)
STOCK_ROW_PATTERN = re.compile(r"<tr><td>(a[A-Za-z0-9.]+)</td><td>(0x[0-9a-fA-F]{40})</td></tr>")
TABLE_ROW_MARKER = "<tr><td>"
NETWORKS_STATEMENT = "Ethereum, Monad, Base, and Arbitrum"
CROSS_CHAIN_IDS: tuple[CrossChainId, ...] = ("1", "8453", "42161")


def parse_stock_rows(markdown: str) -> list[tuple[str, str]]:
    """Extract (symbol, address) pairs from the tokenized stocks table.

    Args:
        markdown: The Markdown version of the Anchored tokens docs page.

    Returns:
        list[tuple[str, str]]: Symbol and address for every stock row.

    Raises:
        RuntimeError: If the section is missing, no longer names Monad, or has rows
            that do not parse.
    """
    section_match = STOCKS_SECTION_PATTERN.search(markdown)

    if section_match is None:
        raise RuntimeError("tokenized stocks section not found in Anchored docs")

    section = section_match.group(1)

    if NETWORKS_STATEMENT not in section:
        raise RuntimeError(f"Anchored docs no longer say stocks are on {NETWORKS_STATEMENT}")

    rows = STOCK_ROW_PATTERN.findall(section)
    row_count = section.count(TABLE_ROW_MARKER)

    if not rows or len(rows) != row_count:
        raise RuntimeError(f"parsed {len(rows)} of {row_count} rows in the Anchored stock table")

    return rows


def build_logo_uri(symbol: str) -> str:
    """Return the logo URL for an Anchored symbol.

    Args:
        symbol: An Anchored token symbol such as aAAPL.

    Returns:
        str: The logo URL, keyed by the underlying ticker.
    """
    return f"{ANCHORED_LOGO_BASE_URL}/{symbol.removeprefix('a')}.svg"


def build_listing(symbol: str, address: str) -> Listing:
    """Build the listing for one row of the Anchored stock table.

    Anchored deploys each token at the same address on every chain it supports.

    Args:
        symbol: The Anchored token symbol, e.g. aAAPL.
        address: The token's address as the docs give it.

    Returns:
        Listing: The token's listing, with its cross-chain deployments.
    """
    checksummed_address = Web3.to_checksum_address(address)

    return Listing(
        address=checksummed_address,
        symbol=symbol,
        logo_uri=build_logo_uri(symbol),
        cross_chain_addresses=dict.fromkeys(CROSS_CHAIN_IDS, checksummed_address),
    )


def find_missing_logos(session: requests.Session, listings: list[Listing]) -> list[str]:
    """Return the symbols whose logo URL does not resolve.

    Sends one HEAD request per listing to its logo URL.

    Args:
        session: Session to send the requests on.
        listings: Listings whose logo_uri to check.

    Returns:
        list[str]: Symbols whose logo URL answered with a status other than 200.

    Raises:
        requests.RequestException: If a logo URL cannot be reached.
    """
    missing_symbols = []

    for listing in listings:
        response = session.head(listing.logo_uri, timeout=SOURCE_HTTP_TIMEOUT_SECONDS)

        if response.status_code != 200:
            missing_symbols.append(listing.symbol)

    return missing_symbols


def fetch_listings() -> list[Listing]:
    """Return every Anchored tokenized stock deployed on Monad.

    Returns:
        list[Listing]: One listing per stock in the docs table, each with a logo
        that resolves.

    Raises:
        requests.RequestException: If the docs or logos cannot be reached.
        RuntimeError: If the docs page does not parse or a logo is missing.
    """
    # One session reuses connections for the docs page and every logo check.
    with create_source_session() as session:
        response = session.get(ANCHORED_TOKENS_URL, timeout=SOURCE_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()
        listings = [
            build_listing(symbol, address) for symbol, address in parse_stock_rows(response.text)
        ]
        missing_logos = find_missing_logos(session, listings)

    if missing_logos:
        raise RuntimeError(f"Anchored logos missing for: {', '.join(missing_logos)}")

    return listings


SOURCE = EquitySource(
    tag="anchored",
    name="Anchored",
    description="Tokenized stocks and ETFs issued by Anchored",
    fetch_listings=fetch_listings,
)
