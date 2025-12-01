#!/usr/bin/env python3
"""Validate token JSON files in the mainnet/ directory.

This script validates token definitions to ensure they conform to
the required schema and contain valid data for all required fields.
It also validates that the token metadata matches on-chain data.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import json5
from utils.web3 import (
    DEFAULT_RPC_URL,
    fetch_token_decimals_with_retry,
    fetch_token_name_with_retry,
    fetch_token_symbol_with_retry,
    get_web3_connection,
)
from web3 import Web3

DATA_DIR = "mainnet"
REQUIRED_FIELDS = ["chainId", "address", "name", "symbol", "decimals"]
ALLOWED_EXTENSIONS = {
    "coinGeckoId": str,
}
EXPECTED_CHAIN_ID = 143
MIN_DECIMALS = 0
MAX_DECIMALS = 36
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


def validate_token_data(
    data: dict[str, Any],
    token_dir_path: Path,
    web3: Web3,
) -> list[str]:
    """Validate token data against required schema and on-chain metadata.

    Args:
        data: The token data dictionary to validate.
        token_dir_path: Path to the token directory.
        web3: Web3 instance for on-chain validation.

    Returns:
        list[str]: List of error messages. Empty list if validation passes.
    """
    errors = []

    # Check for required fields
    missing_fields = [field for field in REQUIRED_FIELDS if field not in data]
    if missing_fields:
        errors.append(f"Missing required fields: {', '.join(missing_fields)}")
        return errors

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

    # Validate logo
    svg_logo_path = token_dir_path / "logo.svg"
    png_logo_path = token_dir_path / "logo.png"
    if not svg_logo_path.exists() and not png_logo_path.exists():
        errors.append("Logo file not found")

    # Validate extensions (optional)
    if "extensions" in data:
        extensions = data.get("extensions")
        if not isinstance(extensions, dict):
            errors.append("Invalid extensions: must be a dictionary")
        else:
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
                else:
                    errors.append(f"Invalid extension tag: {tag}. Allowed tags are: {allowed_tags}")

    # Validate on-chain data
    onchain_errors = validate_onchain_metadata(data, web3)
    errors.extend(onchain_errors)

    return errors


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
) -> tuple[bool, list[str]]:
    """Validate a token directory and its data.json file.

    Args:
        dir_path: Path to the token directory.
        web3: Web3 instance for on-chain validation.

    Returns:
        tuple[bool, list[str]]: (is_valid, error_messages)
    """
    data_file = dir_path / "data.json"

    if not data_file.exists():
        return False, [f"data.json not found in {dir_path.name}/ directory"]

    try:
        with data_file.open(mode="r", encoding="utf-8") as f:
            data = json5.load(f)
    except ValueError as e:
        return False, [f"Invalid JSON5 in data.json: {e}"]
    except OSError as e:
        return False, [f"Cannot read data.json: {e}"]

    errors = validate_token_data(data, dir_path, web3)
    return len(errors) == 0, errors


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

        print(f"Validating {len(token_dirs)} token(s)...\n")

        all_valid = True
        for dir_path in token_dirs:
            token_name = dir_path.name
            is_valid, errors = validate_token_directory(dir_path, web3)

            if is_valid:
                print(f"{token_name} is valid")
            else:
                print(f"{token_name} is invalid:")
                for error in errors:
                    print(f"   - {error}")
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
