#!/usr/bin/env python3
"""Validate token JSON files in the mainnet/ directory.

This script validates token definitions to ensure they conform to
the required schema and contain valid data for all required fields.
It also validates that the token metadata matches on-chain data.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import json5
from PIL import Image
from utils.web3 import (
    CHAIN_NAMES,
    CHAIN_RPC_URLS,
    DEFAULT_RPC_URL,
    fetch_token_decimals_with_retry,
    fetch_token_name_with_retry,
    fetch_token_symbol_with_retry,
    get_web3_connection,
    get_web3_connection_for_chain,
)
from web3 import Web3

DATA_DIR = "mainnet"
REQUIRED_FIELDS = ["chainId", "address", "name", "symbol", "decimals"]
ALLOWED_TOP_LEVEL_FIELDS = set(REQUIRED_FIELDS) | {"extensions"}
ALLOWED_EXTENSIONS = {
    "coinGeckoId": str,
    "bridgeInfo": dict,
    "crossChainAddresses": dict,
}
# Known chain IDs for cross-chain address validation
KNOWN_CHAIN_IDS = {
    "1",  # Ethereum Mainnet
    "10",  # Optimism
    "56",  # BNB Chain
    "137",  # Polygon
    "999",  # HyperEVM
    "8453",  # Base
    "9745",  # Plasma
    "42161",  # Arbitrum One
    "42220",  # Celo
    "43114",  # Avalanche C-Chain
}
VALID_BRIDGE_PROTOCOLS = {
    "Chainlink CCIP",
    "Circle CCTP",
    "Hyperlane Warp Route",
    "LayerZero OFT",
    "Wormhole",
    "Wormhole NTT",
}
EXPECTED_CHAIN_ID = 143
MIN_DECIMALS = 0
MAX_DECIMALS = 36
MIN_LOGO_SIZE = 200
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def get_data_directory() -> Path:
    """Get the path to the data directory.

    Returns:
        Path: Absolute path to the data directory.

    Raises:
        FileNotFoundError: If the data directory does not exist.
    """
    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir.parent / DATA_DIR

    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    return data_dir


def get_token_dirs(data_dir: Path) -> list[Path]:
    """Get all token directories from the specified directory.

    Args:
        data_dir: Path to the directory containing token directories.

    Returns:
        list[Path]: Sorted list of token directory paths.
    """
    return [f for f in sorted(data_dir.iterdir()) if f.is_dir()]


def is_valid_address(address: str) -> bool:
    """Check if an address is a valid Ethereum address.

    Args:
        address: The address string to validate.

    Returns:
        bool: True if the address is valid, False otherwise.
    """
    return bool(re.match(r"^0x[0-9A-Fa-f]{40}$", address))


def validate_bridge_info(bridge_info: dict[str, Any]) -> list[str]:
    """Validate the bridgeInfo extension.

    Args:
        bridge_info: The bridgeInfo dictionary to validate.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []
    allowed_fields = {"protocol", "bridgeAddress"}
    actual_fields = set(bridge_info.keys())

    unknown_fields = actual_fields - allowed_fields
    if unknown_fields:
        errors.append(f"Unknown fields in bridgeInfo: {', '.join(unknown_fields)}")

    if "protocol" not in bridge_info:
        errors.append("Missing required field 'protocol' in bridgeInfo")
    else:
        protocol = bridge_info["protocol"]
        if not isinstance(protocol, str):
            errors.append(
                f"Invalid type for bridgeInfo.protocol: expected str, got {type(protocol).__name__}"
            )
        elif protocol not in VALID_BRIDGE_PROTOCOLS:
            valid_protocols = ", ".join(sorted(VALID_BRIDGE_PROTOCOLS))
            errors.append(
                f"Invalid bridgeInfo.protocol: '{protocol}'. Must be one of: {valid_protocols}"
            )

    if "bridgeAddress" not in bridge_info:
        errors.append("Missing required field 'bridgeAddress' in bridgeInfo")
    else:
        bridge_address = bridge_info["bridgeAddress"]
        if not isinstance(bridge_address, str):
            errors.append(
                "Invalid type for bridgeInfo.bridgeAddress: expected str, "
                f"got {type(bridge_address).__name__}"
            )
        elif not is_valid_address(bridge_address):
            errors.append(f"Invalid bridgeInfo.bridgeAddress address: {bridge_address}")

    return errors


def validate_cross_chain_addresses(cross_chain: dict[str, Any]) -> list[str]:
    """Validate the crossChainAddresses extension.

    Args:
        cross_chain: The crossChainAddresses dictionary to validate.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []
    allowed_fields = {"address", "symbol", "decimals"}

    for chain_id, chain_data in cross_chain.items():
        # Validate chain ID format
        if chain_id not in KNOWN_CHAIN_IDS:
            errors.append(f"Invalid chain ID '{chain_id}' in crossChainAddresses. ")
            continue

        if not isinstance(chain_data, dict):
            errors.append(
                f"Invalid type for crossChainAddresses[{chain_id}]: "
                f"expected dict, got {type(chain_data).__name__}"
            )
            continue

        # Check for required address field
        if "address" not in chain_data:
            errors.append(f"Missing required field 'address' in crossChainAddresses[{chain_id}]")
        else:
            address = chain_data["address"]
            # For EVM chains, validate address format
            if not is_valid_address(address):
                errors.append(f"Invalid address in crossChainAddresses[{chain_id}]: {address}")

        # Validate optional symbol field type
        if "symbol" in chain_data:
            symbol = chain_data["symbol"]
            if not isinstance(symbol, str) or not symbol.strip():
                errors.append(
                    f"Invalid symbol in crossChainAddresses[{chain_id}]: must be a non-empty string"
                )

        # Validate optional decimals field type
        if "decimals" in chain_data:
            decimals = chain_data["decimals"]
            if not isinstance(decimals, int) or not (MIN_DECIMALS <= decimals <= MAX_DECIMALS):
                errors.append(
                    f"Invalid decimals in crossChainAddresses[{chain_id}]: "
                    f"must be an integer between {MIN_DECIMALS} and {MAX_DECIMALS}"
                )

        # Check for unknown fields
        actual_fields = set(chain_data.keys())
        unknown_fields = actual_fields - allowed_fields
        if unknown_fields:
            errors.append(
                f"Unknown fields in crossChainAddresses[{chain_id}]: {', '.join(unknown_fields)}"
            )

    return errors


def validate_single_cross_chain_address(
    chain_id: str,
    address: str,
    expected_symbol: str,
    expected_decimals: int,
) -> tuple[list[str], list[str]]:
    """Validate a single cross-chain address against expected metadata.

    Args:
        chain_id: The chain ID as a string.
        address: The token address on the remote chain.
        expected_symbol: Expected token symbol.
        expected_decimals: Expected decimals.

    Returns:
        tuple[list[str], list[str]]: (errors, warnings)
    """
    errors = []
    warnings = []
    chain_name = CHAIN_NAMES.get(chain_id, f"Chain {chain_id}")

    web3 = get_web3_connection_for_chain(chain_id)
    if web3 is None:
        warnings.append(f"Could not connect to {chain_name} RPC")
        return errors, warnings

    # Fetch and validate symbol
    try:
        actual_symbol = fetch_token_symbol_with_retry(web3, address)
        if actual_symbol != expected_symbol:
            errors.append(
                f"Cross-chain symbol mismatch on {chain_name}: "
                f"expected '{expected_symbol}', got '{actual_symbol}'"
            )
    except Exception as e:
        warnings.append(f"Failed to fetch symbol from {chain_name}: {e}")
        return errors, warnings

    # Fetch and validate decimals
    try:
        actual_decimals = fetch_token_decimals_with_retry(web3, address)
        if actual_decimals != expected_decimals:
            errors.append(
                f"Cross-chain decimals mismatch on {chain_name}: "
                f"expected {expected_decimals}, got {actual_decimals}"
            )
    except Exception as e:
        warnings.append(f"Failed to fetch decimals from {chain_name}: {e}")

    return errors, warnings


def validate_cross_chain_metadata(
    data: dict[str, Any],
    max_workers: int = 4,
) -> tuple[list[str], list[str]]:
    """Validate cross-chain addresses have matching metadata.

    Args:
        data: The token data dictionary.
        max_workers: Maximum number of parallel workers for RPC calls.

    Returns:
        tuple[list[str], list[str]]: (errors, warnings)
    """
    errors = []
    warnings = []

    extensions = data.get("extensions", {})
    cross_chain = extensions.get("crossChainAddresses", {})

    if not cross_chain:
        return errors, warnings

    monad_symbol = data.get("symbol")
    monad_decimals = data.get("decimals")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for chain_id, chain_data in cross_chain.items():
            if chain_id not in CHAIN_RPC_URLS:
                continue
            address = chain_data.get("address")
            if not address:
                continue

            # Use override symbol/decimals if specified, otherwise use Monad token's values
            expected_symbol = chain_data.get("symbol", monad_symbol)
            expected_decimals = chain_data.get("decimals", monad_decimals)

            future = executor.submit(
                validate_single_cross_chain_address,
                chain_id,
                address,
                expected_symbol,
                expected_decimals,
            )
            futures[future] = chain_id

        for future in as_completed(futures):
            chain_errors, chain_warnings = future.result()
            errors.extend(chain_errors)
            warnings.extend(chain_warnings)

    return errors, warnings


def get_svg_dimensions(svg_path: Path) -> tuple[Optional[int], Optional[int]]:
    """Extract width and height from an SVG file.

    Args:
        svg_path: Path to the SVG file.

    Returns:
        tuple[Optional[int], Optional[int]]: (width, height) in pixels, or (None, None) if not
        found.
    """
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()

        width_str = root.get("width")
        height_str = root.get("height")

        if width_str and height_str:
            width = int(re.sub(r"[^0-9.]", "", width_str).split(".")[0])
            height = int(re.sub(r"[^0-9.]", "", height_str).split(".")[0])
            return width, height

        return None, None
    except Exception:
        return None, None


def validate_logo_dimensions(token_dir_path: Path) -> list[str]:
    """Validate logo file dimensions.

    Args:
        token_dir_path: Path to the token directory.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []
    svg_logo_path = token_dir_path / "logo.svg"
    png_logo_path = token_dir_path / "logo.png"

    logo_path = None
    if svg_logo_path.exists():
        logo_path = svg_logo_path
    elif png_logo_path.exists():
        logo_path = png_logo_path
    else:
        return ["Logo file not found"]

    try:
        if logo_path.suffix == ".svg":
            width, height = get_svg_dimensions(logo_path)
            if width is None or height is None:
                errors.append(
                    "Could not extract dimensions from SVG. "
                    "Ensure the SVG has width/height attributes or a viewBox."
                )
                return errors
        elif logo_path.suffix == ".png":
            with Image.open(logo_path) as img:
                width, height = img.size
        else:
            errors.append(f"Unsupported logo format: {logo_path.suffix}")
            return errors

        # Check if square
        if width != height:
            errors.append(f"Logo must be square: current dimensions are {width}x{height}px")
        # Check minimum size
        elif width < MIN_LOGO_SIZE:
            errors.append(
                f"Logo dimensions must be at least {MIN_LOGO_SIZE}x{MIN_LOGO_SIZE}px: "
                f"current dimensions are {width}x{height}px"
            )
    except Exception as e:
        errors.append(f"Failed to validate logo dimensions: {e}")

    return errors


def validate_extensions(extensions: Any) -> list[str]:
    """Validate the extensions field of token data.

    Args:
        extensions: The extensions value to validate.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []

    if not isinstance(extensions, dict):
        return ["Invalid extensions: must be a dictionary"]

    allowed_tags = ", ".join(ALLOWED_EXTENSIONS.keys())
    for tag, value in extensions.items():
        if tag in ALLOWED_EXTENSIONS:
            expected_type = ALLOWED_EXTENSIONS[tag]
            if not isinstance(value, expected_type):
                type_name = expected_type.__name__
                errors.append(
                    f"Invalid type for extension '{tag}': expected {type_name}, "
                    f"got {type(value).__name__}"
                )
            elif tag == "bridgeInfo":
                bridge_errors = validate_bridge_info(value)
                errors.extend(bridge_errors)
            elif tag == "crossChainAddresses":
                cross_chain_errors = validate_cross_chain_addresses(value)
                errors.extend(cross_chain_errors)
        else:
            errors.append(f"Invalid extension tag: {tag}. Allowed tags are: {allowed_tags}")

    return errors


def validate_token_data(
    data: dict[str, Any],
    token_dir_path: Path,
    web3: Web3,
    validate_cross_chain: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate token data against required schema and on-chain metadata.

    Args:
        data: The token data dictionary to validate.
        token_dir_path: Path to the token directory.
        web3: Web3 instance for on-chain validation.
        validate_cross_chain: If True, validate cross-chain addresses against
            on-chain metadata on other chains.

    Returns:
        tuple[list[str], list[str]]: (errors, warnings)
    """
    errors = []
    warnings = []

    # Check for unknown top-level fields
    unknown_fields = set(data.keys()) - ALLOWED_TOP_LEVEL_FIELDS
    if unknown_fields:
        allowed = ", ".join(sorted(ALLOWED_TOP_LEVEL_FIELDS))
        errors.append(
            f"Unknown top-level field(s): {', '.join(sorted(unknown_fields))}. "
            f"Allowed fields are: {allowed}"
        )

    # Check for required fields
    missing_fields = [field for field in REQUIRED_FIELDS if field not in data]
    if missing_fields:
        errors.append(f"Missing required fields: {', '.join(missing_fields)}")
        return errors, warnings

    # Validate chainId
    chain_id = data.get("chainId")
    if not isinstance(chain_id, int) or chain_id != EXPECTED_CHAIN_ID:
        errors.append(f"Invalid chainId: expected {EXPECTED_CHAIN_ID}, got {chain_id}")

    # Validate address
    address = data.get("address")
    if not is_valid_address(address):
        errors.append(f"Invalid address: {address}")

    # Validate name
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("Invalid name: must be a non-empty string")

    # Validate symbol
    symbol = data.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        errors.append("Invalid symbol: must be a non-empty string")
    elif symbol != token_dir_path.name:
        errors.append(
            f"Symbol mismatch: folder name is '{token_dir_path.name}' but symbol is '{symbol}'"
        )

    # Validate decimals
    decimals = data.get("decimals")
    if not isinstance(decimals, int) or not (MIN_DECIMALS <= decimals <= MAX_DECIMALS):
        errors.append(
            f"Invalid decimals: must be an integer between {MIN_DECIMALS} and {MAX_DECIMALS}"
        )

    # Validate logo dimensions
    logo_errors = validate_logo_dimensions(token_dir_path)
    errors.extend(logo_errors)

    # Validate extensions (optional)
    if "extensions" in data:
        extension_errors = validate_extensions(data.get("extensions"))
        errors.extend(extension_errors)

    # Validate on-chain data
    onchain_errors = validate_onchain_metadata(data, web3)
    errors.extend(onchain_errors)

    # Cross-chain metadata validation (optional)
    if validate_cross_chain and "extensions" in data:
        extensions = data.get("extensions", {})
        if "crossChainAddresses" in extensions:
            cc_errors, cc_warnings = validate_cross_chain_metadata(data)
            errors.extend(cc_errors)
            warnings.extend(cc_warnings)

    return errors, warnings


def validate_onchain_metadata(data: dict[str, Any], web3: Web3) -> list[str]:
    """Validate token metadata against on-chain data.

    Each field is fetched separately so that we don't retry calls that succeeded.

    Args:
        data: The token data dictionary to validate.
        web3: Web3 instance connected to the chain.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []
    address = data.get("address")

    if not address:
        return ["Cannot validate on-chain: address is missing"]

    if address == ZERO_ADDRESS:
        return []

    # Fetch and validate name
    try:
        onchain_name = fetch_token_name_with_retry(web3, address)
        if data.get("name") != onchain_name:
            errors.append(f"Name mismatch: expected '{onchain_name}', got '{data.get('name')}'")
    except Exception as e:
        errors.append(f"Failed to fetch on-chain name: {e}")

    # Fetch and validate symbol
    try:
        onchain_symbol = fetch_token_symbol_with_retry(web3, address)
        if data.get("symbol") != onchain_symbol:
            errors.append(
                f"Symbol mismatch: expected '{onchain_symbol}', got '{data.get('symbol')}'"
            )
    except Exception as e:
        errors.append(f"Failed to fetch on-chain symbol: {e}")

    # Fetch and validate decimals
    try:
        onchain_decimals = fetch_token_decimals_with_retry(web3, address)
        if data.get("decimals") != onchain_decimals:
            errors.append(
                f"Decimals mismatch: expected {onchain_decimals}, got {data.get('decimals')}"
            )
    except Exception as e:
        errors.append(f"Failed to fetch on-chain decimals: {e}")

    return errors


def validate_token_directory(
    dir_path: Path,
    web3: Web3,
    validate_cross_chain: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Validate a token directory and its data.json file.

    Args:
        dir_path: Path to the token directory.
        web3: Web3 instance for on-chain validation.
        validate_cross_chain: If True, validate cross-chain addresses against
            on-chain metadata on other chains.

    Returns:
        tuple[bool, list[str], list[str]]: (is_valid, errors, warnings)
    """
    data_file = dir_path / "data.json"

    if not data_file.exists():
        return False, [f"data.json not found in {dir_path.name}/ directory"], []

    try:
        with data_file.open(mode="r", encoding="utf-8") as f:
            data = json5.load(f)
    except ValueError as e:
        return False, [f"Invalid JSON5 in data.json: {e}"], []
    except OSError as e:
        return False, [f"Cannot read data.json: {e}"], []

    errors, warnings = validate_token_data(data, dir_path, web3, validate_cross_chain)
    return len(errors) == 0, errors, warnings


def main() -> int:
    """Main entry point for the token validator.

    Returns:
        int: Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(
        description="Validate token JSON files and on-chain metadata in the mainnet/ directory"
    )
    parser.add_argument(
        "--rpc-url",
        type=str,
        help=f"Custom RPC URL (defaults to MONAD_RPC_URL env var or {DEFAULT_RPC_URL})",
    )
    parser.add_argument(
        "--validate-cross-chain",
        action="store_true",
        help="Enable cross-chain address validation (slower, requires external RPC access)",
    )

    args = parser.parse_args()

    try:
        data_dir = get_data_directory()

        token_dirs = get_token_dirs(data_dir)
        if not token_dirs:
            print(f"No token directories found in {DATA_DIR}/")
            return 0

        try:
            web3 = get_web3_connection(args.rpc_url)
        except ConnectionError as e:
            print(f"Error: {e}")
            print("Cannot proceed without RPC connection")
            return 1

        print(f"Validating {len(token_dirs)} token(s)...")
        if args.validate_cross_chain:
            print("Cross-chain validation enabled\n")
        else:
            print()

        all_valid = True
        for dir_path in token_dirs:
            token_name = dir_path.name
            is_valid, errors, warnings = validate_token_directory(
                dir_path, web3, args.validate_cross_chain
            )

            if is_valid and not warnings:
                print(f"{token_name} is valid")
            elif is_valid and warnings:
                print(f"{token_name} is valid (with warnings):")
                for warning in warnings:
                    print(f"   [WARN] {warning}")
            else:
                print(f"{token_name} is invalid:")
                for error in errors:
                    print(f"   - {error}")
                for warning in warnings:
                    print(f"   [WARN] {warning}")
                all_valid = False

        if all_valid:
            print(f"\nAll {len(token_dirs)} token(s) are valid")
            return 0

        print("\nValidation failed for one or more tokens")
        return 1
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
