#!/usr/bin/env python3
"""Resolve a token's CoinGecko ID with address-level corroboration.

Reconstructed for the add-token skill: given the Monad address (and any
independently-established cross-chain addresses), find the CoinGecko coin and
report how strongly it is tied to *this* token, never to a bare name match.

Confidence:
- `address-verified`: one of the coin's `platforms` addresses equals an address
  we already established (the Monad address or a `--known-address`).
- `weak`: no address match, but either the homepage matches the official domain
  with an exact name/symbol match, or the symbol/name lands exactly on a canonical
  coin whose platforms omit the Monad address (a wrapped/bridged token). Confirm
  before use.
- `none`: nothing corroborates; omit the coinGeckoId.

Output JSON: {coinGeckoId, confidence, evidence[], coin{...}, report[]}.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registries import load_env  # noqa: E402

load_env()

from utils.coingecko import CoinGeckoValidator  # noqa: E402

COIN_DETAIL_PARAMS = {
    "localization": "false",
    "tickers": "false",
    "market_data": "false",
    "community_data": "false",
    "developer_data": "false",
    "sparkline": "false",
}


def fetch_json(session, url, params=None):
    try:
        response = session.get(url, params=params or {}, timeout=30)
    except Exception:
        return None

    return response.json() if response.status_code == 200 else None


def platform_addresses(coin: dict) -> set[str]:
    return {value.lower() for value in (coin.get("platforms") or {}).values() if value}


def mentions_bridge_or_wrapper(coin: dict) -> bool:
    """A canonical coin should win over its bridged/wrapped representation."""

    text = f"{coin.get('id', '')} {coin.get('name', '')}".lower()
    return any(token in text for token in ("bridged", "wrapped"))


def homepage_url(coin: dict) -> str | None:
    for link in (coin.get("links") or {}).get("homepage", []):
        if link and link.startswith("http"):
            return link

    return None


def image_url(coin: dict) -> str | None:
    image = coin.get("image") or {}
    return image.get("large") or image.get("small") or image.get("thumb")


def summarize_coin(coin: dict) -> dict:
    platforms = {
        chain: address for chain, address in (coin.get("platforms") or {}).items() if address
    }
    return {
        "id": coin.get("id"),
        "name": coin.get("name"),
        "symbol": coin.get("symbol"),
        "platforms": platforms,
        "homepage": homepage_url(coin),
        "image": image_url(coin),
    }


def search_candidate_ids(session, base, symbol, name) -> list[str]:
    search = fetch_json(session, f"{base}/search", {"query": symbol or name or ""}) or {}

    candidate_ids = []
    for hit in search.get("coins", []):
        matches_symbol = symbol and hit.get("symbol", "").lower() == symbol.lower()
        matches_name = name and hit.get("name", "").lower() == name.lower()
        if matches_symbol or matches_name:
            candidate_ids.append(hit["id"])

    return list(dict.fromkeys(candidate_ids))[:8]


def search_and_verify(session, base, args, known_addresses, result) -> None:
    """Corroborated search by symbol/name, then verify each candidate by address."""

    report = result["report"]
    candidate_ids = search_candidate_ids(session, base, args.symbol, args.name)
    report.append(f"Search candidates: {candidate_ids}")

    # A wrapped/bridged token carries a modified name ("Wrapped SOL" vs the
    # canonical "Solana"), so its canonical coin never matches on name. For those
    # we accept a symbol-only exact match and lean on the non-bridged preference
    # below to pick the canonical coin over another wrapper.
    local_is_wrapped_variant = bool(args.name) and any(
        marker in args.name.lower() for marker in ("wrapped", "bridged", "wormhole")
    )

    weak_candidate = None
    canonical_candidate = None

    for coin_id in candidate_ids:
        url = f"{base}/coins/{urllib.parse.quote(coin_id, safe='')}"
        coin = fetch_json(session, url, COIN_DETAIL_PARAMS)
        if not isinstance(coin, dict):
            continue

        matched = known_addresses & platform_addresses(coin)
        if matched:
            address = next(iter(matched))
            report.append(f"Candidate '{coin_id}': confidence address-verified")
            result.update(
                coinGeckoId=coin_id,
                confidence="address-verified",
                evidence=[
                    f"coin platform address matches independently-established address {address}"
                ],
                coin=summarize_coin(coin),
            )
            return

        homepage = homepage_url(coin) or ""
        matches_domain = args.official_domain and args.official_domain.lower() in homepage.lower()
        matches_exact = (args.symbol and coin.get("symbol", "").lower() == args.symbol.lower()) or (
            args.name and coin.get("name", "").lower() == args.name.lower()
        )
        if matches_domain and matches_exact and weak_candidate is None:
            weak_candidate = coin

        matches_symbol_exact = args.symbol and coin.get("symbol", "").lower() == args.symbol.lower()
        matches_name_exact = (
            not args.name
            or coin.get("name", "").lower() == args.name.lower()
            or local_is_wrapped_variant
        )
        matches_canonical = matches_symbol_exact and matches_name_exact
        prefers_over_current = canonical_candidate is None or (
            mentions_bridge_or_wrapper(canonical_candidate) and not mentions_bridge_or_wrapper(coin)
        )

        if matches_canonical and prefers_over_current:
            canonical_candidate = coin

        report.append(
            f"Candidate '{coin_id}': {'weak' if matches_domain and matches_exact else 'none'}"
        )

    if weak_candidate is not None:
        result.update(
            coinGeckoId=weak_candidate["id"],
            confidence="weak",
            evidence=["homepage matches official domain and name/symbol match; no address match"],
            coin=summarize_coin(weak_candidate),
        )
        return

    # No address match and no official-domain corroboration, but the symbol and
    # name land exactly on a canonical coin whose platforms omit the Monad
    # address. data.json still wants that canonical id, so surface it for review.
    if canonical_candidate is not None:
        result.update(
            coinGeckoId=canonical_candidate["id"],
            confidence="weak",
            evidence=[
                "exact symbol/name match to a canonical coin; Monad address not listed on "
                "CoinGecko (likely a wrapped/bridged representation) - confirm before use"
            ],
            coin=summarize_coin(canonical_candidate),
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve a token's CoinGecko ID (address-verified)."
    )
    parser.add_argument("address", help="Monad (chain 143) token address.")
    parser.add_argument("--symbol", default=None, help="Token symbol.")
    parser.add_argument("--name", default=None, help="Token name.")
    parser.add_argument(
        "--known-address",
        action="append",
        default=[],
        metavar="CHAINID=0x...",
        help="An independently-established cross-chain address; repeatable.",
    )
    parser.add_argument("--official-domain", default=None, help="Project's official domain.")
    return parser.parse_args()


def collect_known_addresses(monad_address: str, known_flags: list[str]) -> set[str]:
    known_addresses = {monad_address.lower()}
    for item in known_flags:
        _chain, _, address = item.partition("=")
        if address:
            known_addresses.add(address.strip().lower())

    return known_addresses


def main() -> int:
    args = parse_args()

    validator = CoinGeckoValidator()
    session = requests.Session()
    session.headers.update(validator._build_headers())
    base = validator.base_url

    known_addresses = collect_known_addresses(args.address, args.known_address)
    access = "Pro API key (no throttling)" if validator.is_pro else "no/limited key (throttled)"
    result = {
        "coinGeckoId": None,
        "confidence": "none",
        "evidence": [],
        "coin": None,
        "report": [f"CoinGecko access: {access}"],
    }

    # Direct Monad-contract lookup: a coin listed by this very address is
    # address-verified outright.
    encoded = urllib.parse.quote(args.address, safe="")
    coin = fetch_json(session, f"{base}/coins/monad/contract/{encoded}")
    if isinstance(coin, dict) and coin.get("id"):
        result["report"].append("Monad contract lookup: found")
        result.update(
            coinGeckoId=coin["id"],
            confidence="address-verified",
            evidence=[f"coin listed on CoinGecko at the Monad contract address {args.address}"],
            coin=summarize_coin(coin),
        )

        print(json.dumps(result, indent=2))
        return 0

    result["report"].append(
        "Monad contract lookup found nothing (token may be too new); "
        "falling back to corroborated search"
    )
    search_and_verify(session, base, args, known_addresses, result)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
