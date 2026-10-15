#!/usr/bin/env python3
"""Generate tokenlist-equities.json from tokenized equity issuers.

Each issuer in equity_sources/ supplies addresses, logos and cross-chain
deployments; name, symbol and decimals are read on-chain. Issuers fail
independently: if one source breaks, its tokens from the previous list are
kept and the script exits non-zero after writing the others' updates.
Versioning follows the same rules as the mainnet list (see utils/token_list.py).
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import TypeAlias

import requests
from equity_sources import SOURCES
from utils.coingecko import (
    CROSS_CHAIN_COINGECKO_PLATFORMS,
    MONAD_COINGECKO_PLATFORM,
    CoinGeckoCoin,
    CoinGeckoValidator,
)
from utils.equities import (
    EQUITIES_LIST_FILE,
    EQUITIES_LIST_PATH,
    EquitySource,
    EquityToken,
    EquityTokenExtensions,
    Listing,
    build_equity_token,
    parse_equity_token,
    print_onchain_progress,
)
from utils.json_values import read_object_list
from utils.token_list import (
    create_token_list,
    find_duplicate_errors,
    format_version,
    load_existing_token_list,
    resolve_next_version,
    write_token_list,
)
from utils.web3 import (
    CHAIN_ID,
    TokenMetadata,
    add_rpc_url_argument,
    fetch_token_metadata_in_batches,
    get_web3_connection,
)
from web3 import Web3

TOKEN_LIST_NAME = "Tokenized Equities on Monad"
KEYWORDS = ["monad mainnet", "tokenized equities", "stocks", "etfs"]
DEFAULT_MAX_REMOVALS = 10
# CoinGecko coin IDs keyed by (CoinGecko platform ID, lowercase contract address)
CoinGeckoIndex: TypeAlias = dict[tuple[str, str], set[str]]


def build_token(
    source: EquitySource,
    listing: Listing,
    onchain: TokenMetadata,
    coin_gecko_id: str | None,
) -> EquityToken:
    """Build a token list entry from an issuer listing and its on-chain metadata.

    Args:
        source: The issuer, whose tag the entry carries.
        listing: The issuer's listing for the token.
        onchain: Name, symbol and decimals read from the token contract.
        coin_gecko_id: The token's CoinGecko ID, or None when it has none.

    Returns:
        EquityToken: A token list entry ready to write.
    """
    extensions: EquityTokenExtensions = {}

    if coin_gecko_id is not None:
        extensions["coinGeckoId"] = coin_gecko_id

    if listing.cross_chain_addresses:
        extensions["crossChainAddresses"] = {
            chain_id: {"address": address}
            for chain_id, address in listing.cross_chain_addresses.items()
        }

    return build_equity_token(
        {
            "chainId": CHAIN_ID,
            "address": listing.address,
            "name": onchain["name"],
            "symbol": onchain["symbol"],
            "decimals": onchain["decimals"],
            "tags": [source.tag],
        },
        extensions,
        listing.logo_uri,
    )


def index_coin_ids_by_address(coins: list[CoinGeckoCoin]) -> CoinGeckoIndex:
    """Index CoinGecko coin IDs by (platform, lowercase contract address).

    Args:
        coins: Coins as returned by CoinGeckoValidator.fetch_coins_with_platforms.

    Returns:
        CoinGeckoIndex: Every coin ID claiming each address.
    """
    index: CoinGeckoIndex = {}

    for coin in coins:
        for platform, address in coin.platforms.items():
            index.setdefault((platform, address.lower()), set()).add(coin.id)

    return index


def index_previous_coin_gecko_ids(tokens: list[EquityToken]) -> CoinGeckoIndex:
    """Index the coinGeckoIds already in the list by Monad address.

    Args:
        tokens: Token entries from the previous list.

    Returns:
        CoinGeckoIndex: Coin IDs keyed by (platform, lowercase address).
    """
    return {
        (MONAD_COINGECKO_PLATFORM, token["address"].lower()): {coin_gecko_id}
        for token in tokens
        if (coin_gecko_id := token.get("extensions", {}).get("coinGeckoId")) is not None
    }


def load_coin_gecko_index(previous_tokens: list[EquityToken]) -> CoinGeckoIndex:
    """Index CoinGecko IDs by address, falling back to the previous list's IDs.

    A CoinGecko outage keeps the IDs already published rather than dropping
    them, which would bump the list version for no real change.

    Args:
        previous_tokens: Token entries from the previous list.

    Returns:
        CoinGeckoIndex: Coin IDs keyed by (platform, lowercase address).
    """
    try:
        return index_coin_ids_by_address(CoinGeckoValidator().fetch_coins_with_platforms())
    except (RuntimeError, ValueError, requests.RequestException) as e:
        print(f"   - CoinGecko unavailable, keeping previous coinGeckoIds: {e}")
        return index_previous_coin_gecko_ids(previous_tokens)


def find_coin_gecko_id(listing: Listing, coin_gecko_index: CoinGeckoIndex) -> str | None:
    """Return the one CoinGecko ID claiming any of the listing's addresses.

    Matching is by address only: symbols such as ASx or GHx collide with
    unrelated coins on CoinGecko.

    Args:
        listing: The issuer's listing, with its Monad and cross-chain addresses.
        coin_gecko_index: Coin IDs keyed by (platform, lowercase address).

    Returns:
        str | None: The coin ID, or None when no coin or more than one coin matches.
    """
    platform_addresses = [(MONAD_COINGECKO_PLATFORM, listing.address)] + [
        (CROSS_CHAIN_COINGECKO_PLATFORMS[chain_id], address)
        for chain_id, address in listing.cross_chain_addresses.items()
    ]
    candidate_ids: set[str] = set().union(
        *(
            coin_gecko_index.get((platform, address.lower()), set())
            for platform, address in platform_addresses
        )
    )

    if len(candidate_ids) != 1:
        return None

    return candidate_ids.pop()


def read_listing_metadata(web3: Web3, listings: list[Listing]) -> dict[str, TokenMetadata]:
    """Read every listing's name, symbol and decimals from its contract.

    Args:
        web3: Web3 instance connected to Monad.
        listings: The issuer's listings.

    Returns:
        dict[str, TokenMetadata]: On-chain metadata keyed by listing address.

    Raises:
        RuntimeError: If any token's metadata cannot be read.
    """
    metadata, errors = fetch_token_metadata_in_batches(
        web3, [listing.address for listing in listings], print_onchain_progress
    )

    if errors:
        details = "\n".join(f"   - {address}: {error}" for address, error in errors.items())
        raise RuntimeError(f"cannot read on-chain metadata for {len(errors)} token(s):\n{details}")

    return metadata


def ensure_symbols_match(listings: list[Listing], metadata: dict[str, TokenMetadata]) -> None:
    """Fail when an issuer claims a symbol the token contract does not report.

    Args:
        listings: The issuer's listings.
        metadata: On-chain metadata keyed by listing address.

    Raises:
        RuntimeError: If the issuer and the chain disagree on any symbol.
    """
    symbol_mismatches = [
        f"   - {listing.address}: issuer says {listing.symbol!r}, "
        f"chain says {metadata[listing.address]['symbol']!r}"
        for listing in listings
        if listing.symbol != metadata[listing.address]["symbol"]
    ]

    if symbol_mismatches:
        raise RuntimeError("issuer and on-chain symbols disagree:\n" + "\n".join(symbol_mismatches))


def ensure_within_removal_limit(
    previous_tokens: list[EquityToken], tokens: list[EquityToken], max_removals: int
) -> None:
    """Fail when more tokens disappeared than a real round of delistings explains.

    Args:
        previous_tokens: The issuer's tokens in the current list.
        tokens: The issuer's freshly built tokens.
        max_removals: Most tokens allowed to disappear in one sync.

    Raises:
        RuntimeError: If more than max_removals symbols disappeared.
    """
    removed_symbols = {token["symbol"] for token in previous_tokens} - {
        token["symbol"] for token in tokens
    }

    if len(removed_symbols) > max_removals:
        raise RuntimeError(
            f"refusing to remove {len(removed_symbols)} token(s) (limit {max_removals}): "
            f"{', '.join(sorted(removed_symbols))}. Re-run with a higher --max-removals "
            "if these delistings are real."
        )


def build_source_tokens(
    source: EquitySource,
    web3: Web3,
    previous_tokens: list[EquityToken],
    max_removals: int,
    coin_gecko_index: CoinGeckoIndex,
) -> list[EquityToken]:
    """Build one issuer's verified token entries from its current listings.

    Args:
        source: The issuer to sync.
        web3: Web3 instance connected to Monad.
        previous_tokens: This issuer's tokens in the current list.
        max_removals: Most tokens allowed to disappear in one sync.
        coin_gecko_index: CoinGecko coin IDs keyed by (platform, lowercase address).

    Returns:
        list[EquityToken]: The issuer's token entries.

    Raises:
        RuntimeError: If metadata cannot be read, the issuer and the chain disagree
            on a symbol, or too many tokens disappeared.
        ValueError: If the issuer's source returns data of an unexpected shape.
        requests.RequestException: If the issuer's source cannot be reached.
    """
    listings = source.fetch_listings()
    metadata = read_listing_metadata(web3, listings)
    ensure_symbols_match(listings, metadata)
    tokens = [
        build_token(
            source,
            listing,
            metadata[listing.address],
            find_coin_gecko_id(listing, coin_gecko_index),
        )
        for listing in listings
    ]
    ensure_within_removal_limit(previous_tokens, tokens, max_removals)

    return tokens


def main() -> int:
    """Main entry point for the tokenized equities sync.

    Returns:
        int: Exit code (0 for success, 1 if any issuer failed or nothing could be written).
    """
    parser = argparse.ArgumentParser(description="Sync tokenlist-equities.json from issuers")
    add_rpc_url_argument(parser)
    parser.add_argument(
        "--max-removals",
        type=int,
        default=DEFAULT_MAX_REMOVALS,
        help=(
            "Per issuer: fail the issuer if more tokens than this disappear in one sync, "
            f"which usually means a source problem rather than delistings "
            f"(default {DEFAULT_MAX_REMOVALS})"
        ),
    )
    args = parser.parse_args()

    try:
        web3 = get_web3_connection(args.rpc_url)
        existing_token_list = load_existing_token_list(EQUITIES_LIST_PATH)
        existing_tokens = (
            [parse_equity_token(entry) for entry in read_object_list(existing_token_list, "tokens")]
            if existing_token_list
            else []
        )

        print("Fetching CoinGecko coin list...")
        coin_gecko_index = load_coin_gecko_index(existing_tokens)

        new_tokens: list[EquityToken] = []
        failed_sources: list[str] = []

        for source in SOURCES:
            previous_tokens = [token for token in existing_tokens if source.tag in token["tags"]]
            print(f"Syncing {source.name}...")

            try:
                source_tokens = build_source_tokens(
                    source, web3, previous_tokens, args.max_removals, coin_gecko_index
                )
                print(f"   - {len(source_tokens)} token(s) on Monad")
            except (RuntimeError, ValueError, requests.RequestException) as e:
                print(f"   - FAILED, keeping {len(previous_tokens)} previous token(s): {e}")
                failed_sources.append(source.name)
                source_tokens = previous_tokens

            new_tokens.extend(source_tokens)

        matched_count = sum("coinGeckoId" in token.get("extensions", {}) for token in new_tokens)
        print(f"   - {matched_count} of {len(new_tokens)} token(s) have a CoinGecko ID")

        duplicate_errors = find_duplicate_errors(new_tokens)

        if duplicate_errors:
            print("Refusing to write a list with duplicates across issuers:")
            print("\n".join(f"   - {error}" for error in duplicate_errors))
            return 1

        new_tokens.sort(key=lambda token: token["symbol"])
        new_version, version_report = resolve_next_version(existing_token_list, new_tokens)
        print("\n".join(version_report))

        if new_version is not None:
            timestamp = datetime.now(timezone.utc).isoformat()
            tags = {
                source.tag: {"name": source.name, "description": source.description}
                for source in SOURCES
            }
            token_list = create_token_list(
                TOKEN_LIST_NAME, KEYWORDS, new_tokens, new_version, timestamp, tags
            )
            write_token_list(token_list, EQUITIES_LIST_PATH)
            print(f"Successfully wrote '{EQUITIES_LIST_FILE}'")
            print(f"   - {len(new_tokens)} token(s) included")
            print(f"   - Version: {format_version(new_version)}")

        if failed_sources:
            print(f"\nSync failed for: {', '.join(failed_sources)}")
            return 1

        return 0
    except (ConnectionError, OSError, ValueError) as e:
        print(e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
