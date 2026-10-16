"""Types shared by the tokenized equities sync, its issuer sources and its validator."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import requests
from requests.adapters import HTTPAdapter, Retry

from utils.json_values import read_field, read_list
from utils.token_list import CrossChainId, is_cross_chain_id

# Some issuer APIs reject default Python user agents with 403.
SOURCE_HTTP_HEADERS = {"User-Agent": "monad-token-list-sync/1.0"}
SOURCE_HTTP_TIMEOUT_SECONDS = 30
# Issuer APIs return sporadic 5xx mid-pagination (seen on xStocks page 13). Waits of
# 1, 2, 4, 8s ride those out while one failed source still costs well under a minute.
SOURCE_HTTP_RETRY = Retry(
    total=4,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=("GET", "HEAD"),
    respect_retry_after_header=True,
)
EQUITIES_LIST_FILE = "tokenlist-equities.json"
EQUITIES_LIST_PATH = Path(__file__).resolve().parents[2] / EQUITIES_LIST_FILE
# On-chain reads take about 10s per 100 tokens, so CI logs show a line every ~10s.
PROGRESS_INTERVAL_TOKENS = 100


@dataclass(frozen=True)
class Listing:
    """One token as an issuer lists it, before on-chain verification.

    Attributes:
        address: Checksummed Monad address.
        symbol: Symbol the issuer claims; the sync rejects it if the chain disagrees.
        logo_uri: https URL of the token logo.
        cross_chain_addresses: Checksummed addresses on other chains, keyed by chain ID.
    """

    address: str
    symbol: str
    logo_uri: str
    cross_chain_addresses: dict[CrossChainId, str]


@dataclass(frozen=True)
class EquitySource:
    """An issuer whose tokens are synced into the equities list.

    Attributes:
        tag: Tag ID carried by every token from this issuer (token list standard: word
            characters, at most 10).
        name: Display name of the tag.
        description: Description of the tag.
        fetch_listings: Returns the issuer's current Monad tokens; raises on any problem
            with the source so the sync can keep the issuer's previous tokens.
    """

    tag: str
    name: str
    description: str
    fetch_listings: Callable[[], list[Listing]]


def create_source_session() -> requests.Session:
    """Create an HTTP session that retries transient issuer failures.

    Every request carries SOURCE_HTTP_HEADERS. Connection errors, 429 and 5xx
    responses to GET and HEAD are retried with exponential backoff, honouring
    Retry-After. Once retries run out, requests raises
    requests.exceptions.RetryError, a requests.RequestException.

    Returns:
        requests.Session: A session to close after use, e.g. in a with block.
    """
    session = requests.Session()
    session.headers.update(SOURCE_HTTP_HEADERS)
    session.mount("https://", HTTPAdapter(max_retries=SOURCE_HTTP_RETRY))
    return session


def print_onchain_progress(checked: int, total: int) -> None:
    """Print a progress line every PROGRESS_INTERVAL_TOKENS tokens and at the end.

    Args:
        checked: Tokens whose on-chain metadata has been read so far.
        total: Tokens to read in all.
    """
    if checked % PROGRESS_INTERVAL_TOKENS != 0 and checked != total:
        return

    print(f"   - read on-chain metadata for {checked}/{total} token(s)", flush=True)


class CrossChainAddress(TypedDict):
    """A token's address on another chain."""

    address: str


class EquityTokenExtensions(TypedDict, total=False):
    """The token list extensions the equities sync writes."""

    coinGeckoId: str
    crossChainAddresses: dict[CrossChainId, CrossChainAddress]


class EquityTokenCore(TypedDict):
    """The fields every equities token entry starts with, in output order."""

    chainId: int
    address: str
    name: str
    symbol: str
    decimals: int
    tags: list[str]


class _RequiredEquityTokenFields(EquityTokenCore):
    logoURI: str


class EquityToken(_RequiredEquityTokenFields, total=False):
    """An equities token list entry; extensions is written only when non-empty."""

    extensions: EquityTokenExtensions


def build_equity_token(
    core: EquityTokenCore, extensions: EquityTokenExtensions, logo_uri: str
) -> EquityToken:
    """Assemble an entry, omitting empty extensions and keeping the output field order.

    Args:
        core: The entry's leading fields.
        extensions: The entry's extensions; dropped when empty.
        logo_uri: https URL of the token logo, written last.

    Returns:
        EquityToken: The token list entry.
    """
    if not extensions:
        return {**core, "logoURI": logo_uri}

    return {**core, "extensions": extensions, "logoURI": logo_uri}


def parse_cross_chain_addresses(value: object) -> dict[CrossChainId, CrossChainAddress]:
    """Narrow a decoded crossChainAddresses extension.

    Args:
        value: The extension as decoded from JSON.

    Returns:
        dict[CrossChainId, CrossChainAddress]: Addresses keyed by chain ID.

    Raises:
        ValueError: If value is not an object, a key is not a known chain ID, or an
            entry lacks a string address.
    """
    if not isinstance(value, dict):
        raise ValueError("crossChainAddresses must be a JSON object")

    addresses: dict[CrossChainId, CrossChainAddress] = {}

    for chain_id, entry in value.items():
        if not is_cross_chain_id(chain_id):
            raise ValueError(f"crossChainAddresses has unknown chain ID {chain_id!r}")

        addresses[chain_id] = {"address": read_field(entry, "address", str)}

    return addresses


def parse_equity_token_extensions(value: object) -> EquityTokenExtensions:
    """Narrow a decoded extensions object to the extensions the equities list uses.

    Args:
        value: The entry's extensions as decoded from JSON.

    Returns:
        EquityTokenExtensions: The coinGeckoId and crossChainAddresses present.

    Raises:
        ValueError: If value is not an object or a known extension has the wrong shape.
    """
    if not isinstance(value, dict):
        raise ValueError("extensions must be a JSON object")

    extensions: EquityTokenExtensions = {}

    if "coinGeckoId" in value:
        extensions["coinGeckoId"] = read_field(value, "coinGeckoId", str)

    if "crossChainAddresses" in value:
        extensions["crossChainAddresses"] = parse_cross_chain_addresses(
            value["crossChainAddresses"]
        )

    return extensions


def parse_equity_token(entry: object) -> EquityToken:
    """Narrow a decoded token list entry to an EquityToken.

    Only the fields EquityToken declares are kept; the validator reports any others.

    Args:
        entry: One item of the list's tokens array as decoded from JSON.

    Returns:
        EquityToken: The entry, typed.

    Raises:
        ValueError: If a field is missing or has the wrong type.
    """
    if not isinstance(entry, dict):
        raise ValueError("token entry must be a JSON object")

    core: EquityTokenCore = {
        "chainId": read_field(entry, "chainId", int),
        "address": read_field(entry, "address", str),
        "name": read_field(entry, "name", str),
        "symbol": read_field(entry, "symbol", str),
        "decimals": read_field(entry, "decimals", int),
        "tags": read_list(entry, "tags", str),
    }
    extensions = parse_equity_token_extensions(entry.get("extensions", {}))

    return build_equity_token(core, extensions, read_field(entry, "logoURI", str))
