#!/usr/bin/env python3
"""Detect a Monad token's native bridge protocol on-chain.

Reconstructed for the add-token skill. Orchestrates the protocol primitives in
scripts/utils/web3.py over every protocol the validator accepts, and reports
exactly one detected mint-class bridge (tier A), or surfaces ambiguity / a
shared-manager case for a human to confirm.

Output JSON: {token, detected, ambiguous[], needsHumanConfirmation[], report[]}.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from registries import layerzero_oapp_for, load_env  # noqa: E402

load_env()

from utils.web3 import (  # noqa: E402
    CCTP_TOKEN_MINTER_V2_ADDRESS,
    fetch_ccip_token_config_with_retry,
    fetch_cctp_burn_limits_per_message_with_retry,
    fetch_hyperlane_wrapped_token_with_retry,
    fetch_m0_m_token_with_retry,
    fetch_oft_bridge_token_with_retry,
    fetch_token_data_with_retry,
    fetch_wormhole_chain_id_with_retry,
    fetch_wormhole_ntt_token_with_retry,
    get_web3_connection,
    validate_address,
)

# Fixed bridge addresses the validator expects (see scripts/validate_tokens.py).
EXPECTED_BRIDGE_ADDRESSES = {
    "Chainlink CCIP": "0x33566fE5976AAa420F3d5C64996641Fc3858CaDB",
    "Circle CCTP": "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d",
    "M0 Portal": "0xD925C84b55E4e44a53749fF5F2a5A13F63D128fd",
    "Wormhole": "0x0B2719cdA2F10595369e6673ceA3Ee2EDFa13BA7",
}
WORMHOLE_MULTI_TOKEN_NTT_MANAGER = "0x36878C6FCa7e0E8a88F90dc410CfBBcA5B695C95"
LAYERZERO_ENDPOINT_V2_MONAD = "0x6f475642a6e85809b1c36fa62763669b1b48dd5b"

MINTER_ABI = [
    {
        "inputs": [],
        "name": "minter",
        "outputs": [{"name": "", "type": "address"}],
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


def is_nonzero_address(address: str | None) -> bool:
    return bool(address) and int(address, 16) != 0


def call_view(web3, address, abi, function_name):
    contract = web3.eth.contract(address=web3.to_checksum_address(address), abi=abi)
    return getattr(contract.functions, function_name)().call()


def build_detection(protocol, bridge_address, evidence):
    return {
        "protocol": protocol,
        "bridgeAddress": bridge_address,
        "class": "mint",
        "evidence": evidence,
    }


def probe_cctp(web3, token):
    limit = fetch_cctp_burn_limits_per_message_with_retry(web3, CCTP_TOKEN_MINTER_V2_ADDRESS, token)
    if limit and limit > 0:
        evidence = [f"burnLimitsPerMessage == {limit}"]
        return "HIT", build_detection(
            "Circle CCTP", EXPECTED_BRIDGE_ADDRESSES["Circle CCTP"], evidence
        )

    return "negative", None


def probe_ccip(web3, token):
    _admin, _pending_admin, pool = fetch_ccip_token_config_with_retry(web3, token)
    if is_nonzero_address(pool):
        evidence = [f"CCIP token pool {pool}"]
        return "HIT", build_detection(
            "Chainlink CCIP", EXPECTED_BRIDGE_ADDRESSES["Chainlink CCIP"], evidence
        )

    return "negative", None


def probe_m0(web3, token):
    m_token = fetch_m0_m_token_with_retry(web3, token)
    if is_nonzero_address(m_token):
        evidence = [f"mToken() == {m_token}"]
        return "HIT", build_detection("M0 Portal", EXPECTED_BRIDGE_ADDRESSES["M0 Portal"], evidence)

    return "negative", None


def probe_wormhole(web3, token):
    wormhole_chain_id = fetch_wormhole_chain_id_with_retry(web3, token)
    if wormhole_chain_id is not None:
        evidence = [f"wrapped-asset chainId() == {wormhole_chain_id}"]
        return "HIT", build_detection("Wormhole", EXPECTED_BRIDGE_ADDRESSES["Wormhole"], evidence)

    return "negative", None


def is_on_layerzero_endpoint(web3, address) -> bool:
    try:
        endpoint = call_view(web3, address, ENDPOINT_ABI, "endpoint")
    except Exception:
        return False

    return bool(endpoint) and endpoint.lower() == LAYERZERO_ENDPOINT_V2_MONAD


def probe_layerzero(web3, token):
    # Self-OFT: the token is its own OApp on the LayerZero endpoint.
    try:
        oft_token = fetch_oft_bridge_token_with_retry(web3, token)
    except Exception:
        oft_token = None

    if oft_token and oft_token.lower() == token.lower():
        evidence = ["token is its own OFT", "bridge.token() == token"]
        if is_on_layerzero_endpoint(web3, token):
            evidence.append(
                f"bridge.endpoint() == Monad LayerZero EndpointV2 ({LAYERZERO_ENDPOINT_V2_MONAD})"
            )

        return "HIT", build_detection("LayerZero OFT", token, evidence)

    # Adapter: LayerZero's Monad registry names a separate OFT adapter for this
    # token. Trust the registry only to locate it, then prove it on-chain.
    registry_info = layerzero_oapp_for(token)
    if not registry_info or not registry_info["oapp"]:
        return "negative", None

    adapter = web3.to_checksum_address(registry_info["oapp"])
    if not is_on_layerzero_endpoint(web3, adapter):
        return "negative", None

    try:
        wrapped_token = fetch_oft_bridge_token_with_retry(web3, adapter)
    except Exception:
        wrapped_token = None

    if wrapped_token and wrapped_token.lower() == token.lower():
        evidence = [
            f"LayerZero registry adapter {adapter}",
            "adapter.endpoint() == Monad EndpointV2",
            "adapter.token() == token",
        ]
        return "HIT", build_detection("LayerZero OFT", adapter, evidence)

    return "negative", None


def probe_hyperlane(web3, token):
    wrapped_token = fetch_hyperlane_wrapped_token_with_retry(web3, token)
    if is_nonzero_address(wrapped_token):
        evidence = [f"wrappedToken() == {wrapped_token}"]
        return "HIT", build_detection("Hyperlane Warp Route", token, evidence)

    return "negative", None


def probe_ntt(web3, token):
    """Wormhole NTT via minter(); the shared multi-token manager needs a human."""

    try:
        manager = call_view(web3, token, MINTER_ABI, "minter")
    except Exception:
        return "negative", None

    if manager and manager.lower() == WORMHOLE_MULTI_TOKEN_NTT_MANAGER.lower():
        evidence = [
            "minter() is the shared multi-token NTT manager; token() reverts, so a "
            "same-receipt event may belong to any token it carries"
        ]
        return "needs-confirm", build_detection("Wormhole NTT", manager, evidence)

    if is_nonzero_address(manager):
        try:
            managed_token = fetch_wormhole_ntt_token_with_retry(
                web3, web3.to_checksum_address(manager)
            )
        except Exception:
            managed_token = None

        if managed_token and managed_token.lower() == token.lower():
            evidence = [f"minter() {manager} .token() == token"]
            return "HIT", build_detection("Wormhole NTT", manager, evidence)

    return "negative", None


MINT_PROBES = [
    ("Probe Circle CCTP", probe_cctp),
    ("Probe Chainlink CCIP", probe_ccip),
    ("Probe M0 Portal", probe_m0),
    ("Probe Wormhole wrapped", probe_wormhole),
    ("Probe LayerZero OFT self", probe_layerzero),
    ("Probe Hyperlane Warp Route", probe_hyperlane),
    ("Probe Wormhole NTT via minter()", probe_ntt),
]


def probe(token: str) -> dict:
    web3 = get_web3_connection()
    token = validate_address(token)
    token_data = fetch_token_data_with_retry(web3, token)

    report = [
        f"Token {token_data['name']} ({token_data['symbol']}), "
        f"decimals {token_data['decimals']}, at {token}"
    ]
    detections: list[dict] = []
    needs_confirmation: list[dict] = []

    for label, probe_function in MINT_PROBES:
        try:
            status, detection = probe_function(web3, token)
        except Exception:
            status, detection = "negative", None

        report.append(f"{label}: {status}")
        if status == "needs-confirm" and detection:
            needs_confirmation.append(detection)
        elif detection:
            detections.append(detection)

    detected = None
    ambiguous: list[dict] = []

    if len(detections) == 1:
        detected = {**detections[0], "source": "on-chain self probe", "tier": "A"}
        report.append(
            f"Detected native bridge: {detected['protocol']} at {detected['bridgeAddress']}"
        )
    elif len(detections) > 1:
        ambiguous = detections
        report.append("Ambiguous: " + ", ".join(detection["protocol"] for detection in detections))
    else:
        report.append("No mint-class bridge verified on-chain")

    return {
        "token": token_data,
        "detected": detected,
        "ambiguous": ambiguous,
        "needsHumanConfirmation": needs_confirmation,
        "report": report,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: probe_bridges.py <address>"}))
        return 1

    print(json.dumps(probe(sys.argv[1]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
