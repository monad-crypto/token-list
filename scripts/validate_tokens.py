#!/usr/bin/env python3
"""
validate_tokens.py

Validate token JSON files in the `mainnet/` folder.

Checks for required fields: `chainId`, `address`, `name`, `symbol`, `decimals`, and `logoURI`.
"""

import os
import sys

import json5
import requests

REQUIRED_FIELDS = ["chainId", "address", "name", "symbol", "decimals", "logoURI"]


def is_valid_address(address: str) -> bool:
    """Check if an address is a valid Ethereum address."""
    return (
        address.startswith("0x")
        and len(address) == 42
        and all(c in "0123456789ABCDEFabcdef" for c in address[2:])
    )


def is_url_image(image_url: str):
    image_formats = ("image/png", "image/jpeg", "image/jpg", "image/svg+xml")
    r = requests.head(image_url)
    return r.headers["content-type"] in image_formats


def is_valid_file(filepath: str) -> bool:
    """Validate one JSON metadata file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json5.load(f)
    except Exception as e:
        print(f"❌ Error reading {filepath}: {e}")
        return False

    filename = os.path.split(filepath)[-1]

    # all required fields must be present
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        print(f"❌ {filename} missing: {', '.join(missing)}")
        return False

    if data.get("chainId", int) != 143:
        print("❌ Invalid chainId.")
        return False

    address = data.get("address", str)
    if not is_valid_address(address):
        print(f"❌ {address} is invalid address")
        return False

    logo_uri = data.get("logoURI", str)
    if not is_url_image(logo_uri):
        print(f"❌ {logo_uri} is not an image.")
        return False

    decimals = data.get("decimals", int)
    if decimals < 6 or decimals > 36:
        print("❌ Invalid decimals.")
        return False

    return True


def main():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "mainnet")
    if not os.path.isdir(base_dir):
        print("❌ mainnet directory not found.")
        sys.exit(1)

    json_files = [
        f for f in sorted(os.listdir(base_dir)) if f.endswith(".json") or f.endswith(".jsonc")
    ]
    if not json_files:
        print("⚠️ No JSON files found in mainnet/")
        sys.exit(0)

    print(f"Validating {len(json_files)} files...\n")
    for filename in json_files:
        filepath = os.path.join(base_dir, filename)
        is_valid = is_valid_file(filepath)
        if is_valid:
            print(f"✅ {filename} is valid.")
        else:
            print(f"❌ {filename} is invalid.")


if __name__ == "__main__":
    main()
