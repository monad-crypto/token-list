#!/usr/bin/env python3
"""Enumerate a bridged token's cross-chain counterparts and verify each on-chain.

Reconstructed for the add-token skill. Every counterpart comes from an
authoritative source that ties the address to this token: the native bridge's
own peer registry (LayerZero `peers`, Wormhole NTT `getPeer`, CCIP pool remote
tokens / directory) or the Hyperlane warp registry. It never guesses a
counterpart from a shared address, because an identical address on another chain
can be an unrelated or malicious deployment. Each candidate is then verified on
its own chain and reported with per-chain symbol/decimals overrides.

Two things keep a counterpart off the wrong chain. The eid -> chain mapping comes
from the LayerZero metadata API rather than a hand-written table, and every
candidate must sit on an endpoint whose own eid() matches the eid that enrolled
it. Candidates that fail that check are reported under `rejected`, never written.

Output JSON: {token, monadSymbol, monadDecimals, verified{}, conflicts{}, rejected[]}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registries import (  # noqa: E402
    ccip_addresses_for,
    hyperlane_warp_addresses,
    layerzero_chain_index,
    layerzero_oapp_for,
    load_env,
)

load_env()

from utils.web3 import (  # noqa: E402
    CHAIN_NAMES,
    fetch_ccip_token_config_with_retry,
    fetch_oft_bridge_token_with_retry,
    fetch_token_decimals_with_retry,
    fetch_token_symbol_with_retry,
    get_web3_connection,
    get_web3_connection_for_chain,
    validate_address,
)
from web3 import Web3  # noqa: E402

SUPPORTED_CHAINS = {"1", "10", "56", "137", "999", "8453", "9745", "42161", "42220", "43114"}

# LayerZero registry chainName -> EVM chain id, for the supported chains.
LAYERZERO_CHAINNAME_TO_ID = {
    "ethereum": "1",
    "optimism": "10",
    "bsc": "56",
    "binance": "56",
    "polygon": "137",
    "hyperliquid": "999",
    "base": "8453",
    "plasma": "9745",
    "arbitrum": "42161",
    "celo": "42220",
    "avalanche": "43114",
}

# Fallback eid -> EVM chain id, used only when the LayerZero metadata API is
# unreachable. `endpoint_id_to_chain()` prefers the live metadata, and every
# candidate is confirmed against the remote endpoint's own eid() regardless.
ENDPOINT_ID_TO_CHAIN_FALLBACK = {
    30101: "1",
    30111: "10",
    30102: "56",
    30109: "137",
    30367: "999",
    30184: "8453",
    30110: "42161",
    30125: "42220",
    30106: "43114",
}

# Chainlink CCIP chain selector -> EVM chain id, for the supported chains only
# (from smartcontractkit's chain-selectors registry). Used to read a token pool's
# remote-token table.
CCIP_SELECTOR_TO_CHAIN = {
    5009297550715157269: "1",
    3734403246176062136: "10",
    11344663589394136015: "56",
    4051577828743386545: "137",
    2442541497099098535: "999",
    15971525489660198786: "8453",
    9335212494177455608: "9745",
    4949039107694359620: "42161",
    1346049177634351622: "42220",
    6433500567565415381: "43114",
}

# Wormhole chain id -> EVM chain id, for the supported chains only (from the
# Wormhole SDK constants). Used to read an NTT manager's getPeer table.
WORMHOLE_ID_TO_CHAIN = {
    2: "1",
    24: "10",
    4: "56",
    5: "137",
    47: "999",
    30: "8453",
    58: "9745",
    23: "42161",
    14: "42220",
    6: "43114",
}

PEERS_ABI = [
    {
        "inputs": [{"name": "_eid", "type": "uint32"}],
        "name": "peers",
        "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    }
]
MINTER_ABI = [
    {
        "inputs": [],
        "name": "minter",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
CCIP_POOL_ABI = [
    {
        "inputs": [],
        "name": "getSupportedChains",
        "outputs": [{"name": "", "type": "uint64[]"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"name": "remoteChainSelector", "type": "uint64"}],
        "name": "getRemoteToken",
        "outputs": [{"name": "", "type": "bytes"}],
        "stateMutability": "view",
        "type": "function",
    },
]
NTT_MANAGER_ABI = [
    {
        "inputs": [{"name": "chainId_", "type": "uint16"}],
        "name": "getPeer",
        "outputs": [
            {
                "components": [
                    {"name": "peerAddress", "type": "bytes32"},
                    {"name": "tokenDecimals", "type": "uint8"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]
ENDPOINT_ABI = [
    {
        "inputs": [],
        "name": "endpoint",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
EID_ABI = [
    {
        "inputs": [],
        "name": "eid",
        "outputs": [{"name": "", "type": "uint32"}],
        "stateMutability": "view",
        "type": "function",
    }
]
_CONNECTION_CACHE: dict = {}


def log(message: str) -> None:
    print(message, file=sys.stderr)


def endpoint_id_to_chain() -> dict[int, str]:
    """eid -> supported chain id, from the LayerZero metadata API."""

    index = layerzero_chain_index()
    if not index:
        log("  LayerZero metadata unreachable; falling back to the static eid table")
        return dict(ENDPOINT_ID_TO_CHAIN_FALLBACK)

    return {
        endpoint_id: info["chainId"]
        for endpoint_id, info in index.items()
        if info["chainId"] in SUPPORTED_CHAINS
    }


def confirm_endpoint_eid(web3, oapp: str, expected_eid: int) -> bool:
    """Does the OApp on the remote chain sit on the endpoint whose eid enrolled it?

    This is what stops a counterpart being written to the wrong chain. A peer at
    eid N is only meaningful if the contract we verify actually lives on the chain
    whose endpoint reports eid N. Same-address deployments on unrelated chains fail
    here, which is the point.
    """

    try:
        endpoint = call_view(web3, oapp, ENDPOINT_ABI, "endpoint")
        return int(call_view(web3, endpoint, EID_ABI, "eid")) == expected_eid
    except Exception:
        return False


def get_remote_web3(chain_id: str):
    """Cache one web3 per chain; each `get_web3_connection_for_chain` does a
    network `is_connected()` handshake, and a chain is verified several times.

    Only a successful connection is cached, so a transient RPC failure retries on
    the next lookup instead of poisoning the chain for the whole run."""

    if chain_id in _CONNECTION_CACHE:
        return _CONNECTION_CACHE[chain_id]

    connection = None
    for _ in range(2):
        try:
            connection = get_web3_connection_for_chain(chain_id)
        except Exception:
            connection = None

        if connection is not None:
            break

    if connection is not None:
        _CONNECTION_CACHE[chain_id] = connection

    return connection


def decode_evm_address(web3, raw: bytes) -> str | None:
    """An EVM address ABI-encoded as `bytes` (CCIP remote token/pool): the last
    20 bytes, dropped when zero."""

    if not raw or len(raw) < 20:
        return None

    address = "0x" + raw[-20:].hex()
    if int(address, 16) == 0:
        return None

    return web3.to_checksum_address(address)


def decode_peer_address(web3, peer_bytes: bytes) -> str | None:
    if not peer_bytes or int.from_bytes(peer_bytes, "big") == 0:
        return None

    return web3.to_checksum_address("0x" + peer_bytes.hex()[-40:])


def resolve_remote_token(chain_id: str, peer_address: str) -> str:
    """A peer is the remote OFT/adapter; its token() is the canonical token (or
    the peer itself for a self-OFT). This is how the registry reaches a
    counterpart deployed at a different address than the OFT."""

    remote = get_remote_web3(chain_id)
    if remote is None:
        return peer_address

    try:
        token = fetch_oft_bridge_token_with_retry(remote, remote.to_checksum_address(peer_address))
        if token and int(token, 16) != 0:
            return remote.to_checksum_address(token)
    except Exception:
        pass

    return peer_address


def verify_on_chain(
    chain_id: str, address: str, monad_symbol: str, monad_decimals: int
) -> dict | None:
    remote = get_remote_web3(chain_id)
    if remote is None:
        return None

    try:
        address = remote.to_checksum_address(address)
        symbol = fetch_token_symbol_with_retry(remote, address)
        decimals = fetch_token_decimals_with_retry(remote, address)
    except Exception:
        return None

    entry: dict = {"address": address}
    if symbol != monad_symbol:
        entry["symbol"] = symbol
    if decimals != monad_decimals:
        entry["decimals"] = decimals
    entry["_symbol"] = symbol

    return entry


def resolve_bridge(token: str, bridge_address: str | None, registry_info: dict | None) -> str:
    """Where the peer table lives: an explicit --bridge-address, else the
    LayerZero registry's adapter (a plain ERC20 has no peers()), else the token
    itself (a NativeOFT is its own OApp)."""

    if bridge_address:
        return validate_address(bridge_address)
    if registry_info and registry_info["oapp"]:
        return validate_address(registry_info["oapp"])

    return token


def enumerate_ccip(web3, token, symbol, record) -> None:
    """Chainlink CCIP counterparts.

    The offchain CCIP directory lists every chain the token exists on with its
    address there; the Monad pool's on-chain remote table only holds the lanes
    that one pool enrolled. So the directory is the primary source, and the
    on-chain table is the fallback when the directory is unreachable.
    """

    try:
        _admin, _pending_admin, pool = fetch_ccip_token_config_with_retry(web3, token)
    except Exception:
        pool = None

    if not pool or int(pool, 16) == 0:
        return  # not a CCIP token on Monad

    directory = ccip_addresses_for(symbol, token)
    if directory:
        log(f"Chainlink CCIP: {len(directory)} chains from the CCIP directory")
        for chain_id, address in directory.items():
            record(chain_id, address, "CCIP directory")

        return

    log(f"Chainlink CCIP: reading remote tokens on pool {pool}")
    contract = web3.eth.contract(address=web3.to_checksum_address(pool), abi=CCIP_POOL_ABI)

    try:
        selectors = contract.functions.getSupportedChains().call()
    except Exception:
        return

    for selector in selectors:
        chain_id = CCIP_SELECTOR_TO_CHAIN.get(selector)
        if not chain_id:
            continue

        try:
            raw = contract.functions.getRemoteToken(selector).call()
        except Exception:
            continue

        remote = decode_evm_address(web3, raw)
        if remote:
            record(chain_id, remote, f"CCIP remote token (selector {selector})")


def enumerate_ntt(web3, token, record) -> None:
    """Wormhole NTT: the token's per-token manager exposes getPeer(whChainId).

    Only a manager whose `token()` is this token is enumerated; the shared
    multi-token manager reverts `token()` and its peers are other shared
    managers, so their counterpart token cannot be resolved."""

    try:
        manager = call_view(web3, token, MINTER_ABI, "minter")
    except Exception:
        return

    if not manager or int(manager, 16) == 0:
        return

    manager = web3.to_checksum_address(manager)
    try:
        managed_token = fetch_oft_bridge_token_with_retry(web3, manager)
    except Exception:
        managed_token = None

    if not managed_token or managed_token.lower() != token.lower():
        return

    log(f"Wormhole NTT: reading getPeer on manager {manager}")
    contract = web3.eth.contract(address=manager, abi=NTT_MANAGER_ABI)

    for wormhole_id, chain_id in WORMHOLE_ID_TO_CHAIN.items():
        try:
            peer = contract.functions.getPeer(wormhole_id).call()
        except Exception:
            continue

        peer_address = decode_peer_address(web3, peer[0])
        if peer_address:
            canonical = resolve_remote_token(chain_id, peer_address)
            record(chain_id, canonical, f"Wormhole NTT peer (whId {wormhole_id})")


def enumerate_hyperlane(token, symbol, record) -> None:
    """Hyperlane Warp Route: the hyperlane-registry warp config lists the token's
    underlying address per chain, matched to this token by its Monad address.

    On-chain discovery from the token does not work for XERC20 routes (the token
    is separate from the router), so the registry is the source here.
    """

    addresses = hyperlane_warp_addresses(symbol, token)
    if not addresses:
        return

    log(f"Hyperlane Warp Route: {len(addresses)} chains from the hyperlane-registry")
    for chain_id, address in addresses.items():
        record(chain_id, address, "Hyperlane warp registry")


def call_view(web3, address, abi, function_name):
    contract = web3.eth.contract(address=web3.to_checksum_address(address), abi=abi)
    return getattr(contract.functions, function_name)().call()


def web3_for_rpcs(rpcs: list[str]):
    """A connection to a chain outside the supported set, for a hub hop."""

    for url in rpcs:
        try:
            connection = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 20}))
            if connection.is_connected():
                return connection
        except Exception:
            continue

    return None


def read_oft_peers(web3, oapp: str, endpoint_ids) -> dict[int, str]:
    """The OApp's peer table, as eid -> peer address, skipping empty slots."""

    contract = web3.eth.contract(address=web3.to_checksum_address(oapp), abi=PEERS_ABI)
    peers = {}
    for endpoint_id in endpoint_ids:
        try:
            peer = contract.functions.peers(endpoint_id).call()
        except Exception:
            continue

        peer_address = decode_peer_address(web3, peer)
        if peer_address:
            peers[endpoint_id] = peer_address

    return peers


def enumerate_layerzero(web3, bridge: str, record) -> list[dict]:
    """LayerZero peers on the bridge, then one hop through any hub they name.

    A hub-and-spoke token enrolls only its hub, so reading just the token's own peer
    table reports zero counterparts for a token that has many. Returns the candidates
    the eid check rejected.
    """

    eid_to_chain = endpoint_id_to_chain()
    lz_index = layerzero_chain_index()
    rejected: list[dict] = []

    def record_peers(peers: dict[int, str], origin: str) -> None:
        for endpoint_id, peer_address in peers.items():
            chain_id = eid_to_chain.get(endpoint_id)
            if chain_id is None:
                continue

            remote = get_remote_web3(chain_id)
            if remote is None:
                log(f"  chain {chain_id:>6} RPC unreachable; skipped (eid {endpoint_id})")
                continue

            if not confirm_endpoint_eid(remote, peer_address, endpoint_id):
                rejected.append(
                    {
                        "chainId": chain_id,
                        "address": peer_address,
                        "endpointId": endpoint_id,
                        "reason": "not on the endpoint reporting this eid",
                        "source": origin,
                    }
                )
                log(
                    f"  chain {chain_id:>6} {peer_address} [rejected] eid {endpoint_id} "
                    f"not confirmed by the remote endpoint"
                )
                continue

            canonical = resolve_remote_token(chain_id, peer_address)
            record(chain_id, canonical, f"{origin} (eid {endpoint_id})")

    log(f"LayerZero OFT: reading peers() on {bridge}")

    all_eids = sorted(lz_index) if lz_index else sorted(eid_to_chain)
    token_peers = read_oft_peers(web3, bridge, all_eids)
    record_peers(token_peers, "LayerZero peer")

    for endpoint_id, hub_address in token_peers.items():
        if endpoint_id in eid_to_chain:
            continue  # a supported chain, already recorded above

        hub_info = lz_index.get(endpoint_id)
        if hub_info is None:
            log(f"  unmapped eid {endpoint_id} peer {hub_address}; resolve by hand")
            continue

        hub_web3 = web3_for_rpcs(hub_info["rpcs"])
        if hub_web3 is None:
            log(f"  hub {hub_info['chainKey']} (eid {endpoint_id}) RPC unreachable; skipped")
            continue

        log(f"  hub hop: reading peers() on {hub_address} at {hub_info['chainKey']}")
        record_peers(
            read_oft_peers(hub_web3, hub_address, all_eids),
            f"LayerZero peer via {hub_info['chainKey']} hub",
        )

    return rejected


def find(token: str, bridge_address: str | None) -> dict:
    web3 = get_web3_connection()
    token = validate_address(token)
    symbol = fetch_token_symbol_with_retry(web3, token)
    decimals = fetch_token_decimals_with_retry(web3, token)
    log(f"Monad token {token}: symbol='{symbol}' decimals={decimals}")

    registry_info = layerzero_oapp_for(token)
    bridge = resolve_bridge(token, bridge_address, registry_info)

    verified: dict[str, dict] = {}
    conflicts: dict[str, list] = {}
    rejected: list[dict] = []

    def record(chain_id: str, address: str, source: str) -> None:
        if chain_id not in SUPPORTED_CHAINS:
            return

        existing = verified.get(chain_id)
        if existing and existing["address"].lower() == address.lower():
            if source not in existing["sources"]:
                existing["sources"].append(source)
            return  # already verified at this address; skip the RPC re-read

        entry = verify_on_chain(chain_id, address, symbol, decimals)
        name = CHAIN_NAMES.get(chain_id, chain_id)
        if entry is None:
            log(f"  chain {chain_id:>6} {name:<12} {address} [unverified] via {source}")
            return

        remote_symbol = entry.pop("_symbol")
        if existing:  # a different address already verified on this chain
            conflicts.setdefault(chain_id, [existing]).append({**entry, "source": source})
            return

        verified[chain_id] = {**entry, "sources": [source]}
        log(
            f"  chain {chain_id:>6} {name:<12} {address} [ok] via {source} (symbol {remote_symbol})"
        )

    # 1. LayerZero OFT peers, plus one hop through any hub they point at.
    rejected.extend(enumerate_layerzero(web3, bridge, record))

    # 2. LayerZero registry peggedTo: the canonical counterpart it pegs to.
    pegged = (registry_info or {}).get("entry", {}).get("peggedTo") or {}
    pegged_chain = LAYERZERO_CHAINNAME_TO_ID.get((pegged.get("chainName") or "").lower())
    if pegged.get("address") and pegged_chain:
        record(
            pegged_chain,
            web3.to_checksum_address(pegged["address"]),
            "LayerZero registry (peggedTo)",
        )

    # 3. Non-LayerZero peer tables. Each self-detects (a CCIP pool, an NTT
    # manager, a Hyperlane router) and no-ops for a plain OFT, so all three are
    # safe to try. A shared address alone is never a source: a counterpart is
    # written only when one of these authoritative registries names it.
    enumerate_ccip(web3, token, symbol, record)
    enumerate_ntt(web3, token, record)
    enumerate_hyperlane(token, symbol, record)

    return {
        "token": token,
        "monadSymbol": symbol,
        "monadDecimals": decimals,
        "verified": verified,
        "conflicts": conflicts,
        "rejected": rejected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enumerate a token's cross-chain counterparts.")
    parser.add_argument("token", help="Monad (chain 143) token address.")
    parser.add_argument(
        "--bridge-address",
        default=None,
        help="Contract holding the peer table, if not the token (an OFT adapter / NTT manager).",
    )
    args = parser.parse_args()

    log("Verifying candidates on their chains:")
    print(json.dumps(find(args.token, args.bridge_address), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
