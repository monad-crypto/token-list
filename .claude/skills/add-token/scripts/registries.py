#!/usr/bin/env python3
"""Public protocol registries used to locate a token's bridge contracts.

These only point at *where* to look (which adapter, which counterpart); the
callers still verify every address on-chain, so a stale or wrong registry entry
cannot produce an unverified result.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

REPO_ROOT = Path(__file__).resolve().parents[4]
LZ_METADATA_URL = "https://metadata.layerzero-api.com/v1/metadata"
CCIP_TOKENS_URL = "https://docs.chain.link/api/ccip/v1/tokens?environment=mainnet"
HYPERLANE_TREE_URL = (
    "https://api.github.com/repos/hyperlane-xyz/hyperlane-registry/git/trees/main?recursive=1"
)
HYPERLANE_RAW_BASE = "https://raw.githubusercontent.com/hyperlane-xyz/hyperlane-registry/main/"

# Hyperlane warp-config chainName -> EVM chain id, for the supported chains.
HYPERLANE_CHAINNAME_TO_ID = {
    "ethereum": "1",
    "optimism": "10",
    "bsc": "56",
    "polygon": "137",
    "hyperevm": "999",
    "base": "8453",
    "plasma": "9745",
    "arbitrum": "42161",
    "celo": "42220",
    "avalanche": "43114",
}

_CACHE: dict = {}


def load_env() -> None:
    """Self-load the repo .env; the repo's own scripts read env at import time."""

    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()

        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _lz_monad_tokens() -> dict:
    if "lz" not in _CACHE:
        tokens = {}
        if requests is not None:
            try:
                data = requests.get(LZ_METADATA_URL, timeout=30).json()
                tokens = data.get("monad", {}).get("tokens", {})
            except Exception:
                tokens = {}

        _CACHE["lz"] = tokens

    return _CACHE["lz"]


def layerzero_chain_index() -> dict:
    """LayerZero V2 mainnet endpoint id -> {chainId, rpcs, chainKey}, from the
    metadata API. Empty when the API is unreachable.

    A hand-written eid table is how a counterpart lands on the wrong chain:
    eid 30370 is Plume, not Plasma, and Plasma is 30383.
    """

    if "lz_chains" not in _CACHE:
        index: dict = {}
        try:
            data = requests.get(LZ_METADATA_URL, timeout=30).json() if requests else {}
        except Exception:
            data = {}

        for chain_key, entry in (data or {}).items():
            details = entry.get("chainDetails") or {}
            native_chain_id = details.get("nativeChainId")
            if details.get("chainType") != "evm" or native_chain_id is None:
                continue

            rpcs = [rpc.get("url") for rpc in (entry.get("rpcs") or []) if rpc.get("url")]
            for deployment in entry.get("deployments") or []:
                if deployment.get("version") == 2 and deployment.get("stage") == "mainnet":
                    endpoint_id = str(deployment.get("eid") or "")
                    if endpoint_id.isdigit():
                        index[int(endpoint_id)] = {
                            "chainId": str(native_chain_id),
                            "rpcs": rpcs,
                            "chainKey": chain_key,
                        }

        _CACHE["lz_chains"] = index

    return _CACHE["lz_chains"]


def layerzero_oapp_for(address: str) -> dict | None:
    """LayerZero OFT registry entry for a Monad token, or None.

    Returns {oapp, is_self, entry}. `oapp` is the OApp that carries the peer
    table: the token itself for a NativeOFT, or the separately-deployed adapter
    (`proxyAddresses[0]`) for a plain ERC20 bridged by an OFT adapter.
    """

    entry = _lz_monad_tokens().get(address.lower())
    if not entry:
        return None
    if entry.get("type") == "NativeOFT":
        return {"oapp": address, "is_self": True, "entry": entry}

    proxies = entry.get("proxyAddresses") or []
    if proxies:
        return {"oapp": proxies[0], "is_self": False, "entry": entry}

    return {"oapp": None, "is_self": False, "entry": entry}


def _ccip_directory() -> dict:
    """The whole CCIP mainnet token directory, `{symbol: {chainId: entry}}`.

    Chainlink's live directory lists every chain a CCIP token exists on with its
    address there, which the Monad pool's on-chain remote table does not (it only
    holds the lanes that one pool enrolled).
    """

    if "ccip" not in _CACHE:
        tokens = {}
        if requests is not None:
            try:
                tokens = requests.get(CCIP_TOKENS_URL, timeout=30).json().get("data", {})
            except Exception:
                tokens = {}

        _CACHE["ccip"] = tokens

    return _CACHE["ccip"]


def ccip_addresses_for(symbol: str, monad_address: str) -> dict:
    """`{EVM chain id: token address}` for a CCIP token, from the directory.

    Keyed by symbol, then confirmed by matching the Monad (143) entry to this
    token's address, so a symbol collision cannot pull in another project.
    """

    by_chain = _ccip_directory().get(symbol) or {}
    monad = by_chain.get("143") or {}
    if (monad.get("tokenAddress") or "").lower() != monad_address.lower():
        return {}

    return {
        chain_id: entry["tokenAddress"]
        for chain_id, entry in by_chain.items()
        if chain_id.isdigit() and entry.get("tokenAddress")
    }


def _hyperlane_config_paths() -> dict:
    """Cached map of warp-route symbol dir -> raw config URLs, from one tree read."""

    if "hyperlane" not in _CACHE:
        paths: dict = {}
        if requests is not None:
            try:
                tree = requests.get(HYPERLANE_TREE_URL, timeout=30).json().get("tree", [])
                for node in tree:
                    match = re.match(
                        r"deployments/warp_routes/([^/]+)/.*-config\.yaml$", node.get("path", "")
                    )
                    if match:
                        url = HYPERLANE_RAW_BASE + node["path"]
                        paths.setdefault(match.group(1), []).append(url)
            except Exception:
                paths = {}

        _CACHE["hyperlane"] = paths

    return _CACHE["hyperlane"]


def _parse_warp_tokens(text: str) -> list[dict]:
    """Parse the `tokens:` list of a hyperlane-registry warp config (no yaml dep).

    Each list item is a per-chain deployment; fields sit at 4-space indent and the
    nested `connections:` items at 6 spaces are ignored.
    """

    entries: list[dict] = []
    current: dict | None = None

    for line in text.splitlines():
        start = re.match(r'^  - (\w+):\s*"?([^"\n]*)"?\s*$', line)
        if start:
            if current is not None:
                entries.append(current)

            current = {start.group(1): start.group(2).strip()}
            continue

        field = re.match(r'^    (\w+):\s*"?([^"\n]*)"?\s*$', line)
        if field and current is not None and field.group(2).strip():
            current[field.group(1)] = field.group(2).strip()

    if current is not None:
        entries.append(current)

    return entries


def hyperlane_warp_addresses(symbol: str, monad_address: str) -> dict:
    """`{EVM chain id: token address}` for a token's Hyperlane warp route.

    Found by matching the token's Monad address in a warp config under its symbol,
    then reading each chain's underlying token (`collateralAddressOrDenom`, or the
    router itself for a synthetic route).
    """

    def token_address(entry: dict) -> str:
        return entry.get("collateralAddressOrDenom") or entry.get("addressOrDenom") or ""

    for url in _hyperlane_config_paths().get(symbol.upper(), []):
        if requests is None:
            return {}

        try:
            entries = _parse_warp_tokens(requests.get(url, timeout=30).text)
        except Exception:
            continue

        monad = next(
            (
                entry
                for entry in entries
                if entry.get("chainName") == "monad"
                and token_address(entry).lower() == monad_address.lower()
            ),
            None,
        )
        if monad is None:
            continue

        addresses = {}
        for entry in entries:
            chain_id = HYPERLANE_CHAINNAME_TO_ID.get(entry.get("chainName", ""))
            if chain_id and token_address(entry):
                addresses[chain_id] = token_address(entry)

        return addresses

    return {}
