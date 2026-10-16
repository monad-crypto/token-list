"""Anchored listings from the Anchored token documentation.

Anchored's symbols API needs a partner key and an IP allowlist, which CI runners
cannot satisfy, so the source is the Markdown version of their docs page. Parsing
is strict: any change to the page's shape fails the source instead of guessing.
"""

import re
from dataclasses import replace

import requests
from utils.equities import (
    SOURCE_HTTP_TIMEOUT_SECONDS,
    EquitySource,
    Listing,
    create_source_session,
    print_onchain_progress,
)
from utils.token_list import CrossChainId
from utils.web3 import (
    CHAIN_NAMES,
    fetch_code_with_retry,
    fetch_token_metadata_in_batches,
    get_web3_connection_for_chain,
)
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

    Anchored deploys each token at the same address on every chain it supports;
    keep_verified_cross_chain_addresses checks that claim on-chain.

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
        response = session.head(
            listing.logo_uri, timeout=SOURCE_HTTP_TIMEOUT_SECONDS, allow_redirects=True
        )

        if response.status_code != 200:
            missing_symbols.append(listing.symbol)

    return missing_symbols


def is_confirmed_absent(web3: Web3, address: str) -> bool:
    """Return whether eth_getCode confirms there is no contract at an address.

    Args:
        web3: Web3 instance connected to the chain.
        address: Checksummed address to look up.

    Returns:
        bool: True when the call succeeds with empty code; False when there is code
        or the call fails, so an RPC failure never counts as proof of absence.
    """
    try:
        return len(fetch_code_with_retry(web3, address)) == 0
    except Exception:
        return False


def find_deployed_addresses(chain_id: CrossChainId, listings: list[Listing]) -> set[str]:
    """Return the listing addresses that hold a token with the listed symbol on a chain.

    Comparing symbol() rather than only checking for code also catches an
    unrelated contract at the same address. An address counts as not deployed only
    when its symbol is read and differs, or its metadata read fails and the
    address has no code; anything else is unknown.

    Args:
        chain_id: The chain to check.
        listings: Listings whose address to look up on that chain.

    Returns:
        set[str]: Checksummed addresses whose contract reports the listing's symbol.

    Raises:
        RuntimeError: If the chain's RPC cannot be reached, or any address can be
            neither confirmed nor ruled out, so the sync keeps the previous tokens.
    """
    web3 = get_web3_connection_for_chain(chain_id)

    if web3 is None:
        raise RuntimeError(f"cannot connect to the {CHAIN_NAMES[chain_id]} RPC")

    print(f"   - checking deployments on {CHAIN_NAMES[chain_id]}", flush=True)
    metadata, errors = fetch_token_metadata_in_batches(
        web3, [listing.address for listing in listings], print_onchain_progress
    )
    unresolved_errors = {
        address: error
        for address, error in errors.items()
        if not is_confirmed_absent(web3, address)
    }

    if unresolved_errors:
        details = "\n".join(
            f"   - {address}: {error}" for address, error in unresolved_errors.items()
        )
        raise RuntimeError(
            f"cannot verify {len(unresolved_errors)} address(es) on "
            f"{CHAIN_NAMES[chain_id]}:\n{details}"
        )

    return {
        listing.address
        for listing in listings
        if listing.address in metadata and metadata[listing.address]["symbol"] == listing.symbol
    }


def keep_verified_cross_chain_addresses(listings: list[Listing]) -> list[Listing]:
    """Drop each cross-chain address that does not hold the listed token.

    The only evidence for the cross-chain addresses is one sentence in the docs, and
    they feed the CoinGecko match, so each one is checked on its chain.

    Args:
        listings: Listings carrying the addresses the docs claim.

    Returns:
        list[Listing]: The listings with only verified cross-chain addresses.

    Raises:
        RuntimeError: If a chain's RPC cannot be reached or an address cannot be verified.
    """
    deployed_by_chain = {
        chain_id: find_deployed_addresses(chain_id, listings) for chain_id in CROSS_CHAIN_IDS
    }
    verified_listings = []

    for listing in listings:
        verified_addresses = {
            chain_id: address
            for chain_id, address in listing.cross_chain_addresses.items()
            if address in deployed_by_chain[chain_id]
        }
        missing_chains = [
            chain_id
            for chain_id in listing.cross_chain_addresses
            if chain_id not in verified_addresses
        ]

        if missing_chains:
            chain_names = ", ".join(CHAIN_NAMES[chain_id] for chain_id in missing_chains)
            print(f"   - {listing.symbol}: not deployed on {chain_names}, dropping those addresses")

        verified_listings.append(replace(listing, cross_chain_addresses=verified_addresses))

    return verified_listings


def fetch_listings() -> list[Listing]:
    """Return every Anchored tokenized stock deployed on Monad.

    Returns:
        list[Listing]: One listing per stock in the docs table, each with a logo
        that resolves and only the cross-chain addresses verified on-chain.

    Raises:
        requests.RequestException: If the docs or logos cannot be reached.
        RuntimeError: If the docs page does not parse, a logo is missing, or a
            cross-chain address cannot be verified.
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

    return keep_verified_cross_chain_addresses(listings)


SOURCE = EquitySource(
    tag="anchored",
    name="Anchored",
    description="Tokenized stocks and ETFs issued by Anchored",
    fetch_listings=fetch_listings,
)
