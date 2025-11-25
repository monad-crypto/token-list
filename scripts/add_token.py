#!/usr/bin/env python3
"""Add a new token to the mainnet directory by fetching data from the blockchain.

This script fetches token information (name, symbol, decimals) from the blockchain
based on the provided contract address and creates the appropriate directory
structure and data.json file.
"""

import argparse
import json
import sys
from pathlib import Path

from utils.web3 import (
    fetch_token_data_with_retry,
    get_web3_connection,
    validate_address,
)


def get_mainnet_directory() -> Path:
    """Get the path to the mainnet directory.

    Returns:
        Path: Absolute path to the mainnet directory.

    Raises:
        FileNotFoundError: If the mainnet directory does not exist.
    """
    script_dir = Path(__file__).resolve().parent
    mainnet_dir = script_dir.parent / "mainnet"

    if not mainnet_dir.is_dir():
        raise FileNotFoundError(f"Mainnet directory not found: {mainnet_dir}")

    return mainnet_dir


def create_token_directory(mainnet_dir: Path, token_data: dict) -> Path:
    """Create token directory and data.json file.

    Args:
        mainnet_dir: Path to the mainnet directory.
        token_data: Token data dictionary.

    Returns:
        Path: Path to the created token directory.

    Raises:
        FileExistsError: If the token directory already exists.
    """
    symbol = token_data["symbol"]
    token_dir = mainnet_dir / symbol

    if token_dir.exists():
        raise FileExistsError(f"Token directory already exists: {token_dir}")

    token_dir.mkdir(parents=True, exist_ok=False)

    data_file = token_dir / "data.json"
    with data_file.open("w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return token_dir


def main() -> int:
    """Main entry point for the add_token script.

    Returns:
        int: Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(
        description="Add a new token by fetching data from the blockchain"
    )
    parser.add_argument(
        "address",
        nargs="?",
        help="Token contract address (if not provided, will prompt for input)",
    )

    args = parser.parse_args()

    address = args.address
    if not address:
        address = input("Enter token contract address: ").strip()

    if not address:
        print("Error: No address provided")
        return 1

    try:
        print(f"Validating address: {address}")
        address = validate_address(address)
        print(f"Checksummed address: {address}")

        web3 = get_web3_connection()
        print("Connected successfully")

        print(f"\nFetching token data from {address}...")
        token_data = fetch_token_data_with_retry(web3, address)

        print("\nToken found:")
        print(f"  Name: {token_data['name']}")
        print(f"  Symbol: {token_data['symbol']}")
        print(f"  Decimals: {token_data['decimals']}")

        mainnet_dir = get_mainnet_directory()

        print("\nCreating token directory...")
        token_dir = create_token_directory(mainnet_dir, token_data)

        print("\n✓ Token successfully added!")
        print(f"  Directory: {token_dir}")
        print(f"  Data file: {token_dir / 'data.json'}")
        print(
            f"\n  Note: Don't forget to add a logo file (logo.svg or logo.png) "
            f"to the {token_data['symbol']}/ directory!"
        )

        return 0
    except (ValueError, ConnectionError, FileNotFoundError, FileExistsError) as e:
        print(f"Error: {e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
