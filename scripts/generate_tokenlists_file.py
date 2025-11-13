#!/usr/bin/env python3
"""
generate_tokenlists_file.py

Generates mainnet.json file by adding all tokens from `mainnet/` folder.
"""

import os
import sys
from datetime import datetime, timezone

import json5


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

    print(f"Adding {len(json_files)} tokens...\n")
    tokens = []
    for filename in json_files:
        filepath = os.path.join(base_dir, filename)
        try:
            with open(filepath, encoding="utf-8") as f:
                token_data = json5.load(f)
        except Exception as e:
            print(f"❌ Error reading {filepath}: {e}")
            return False
        tokens.append(token_data)

    data = {
        "name": "Monad Mainnet",
        "logoURI": "https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/assets/monad.svg",
        "keywords": ["monad mainnet"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tokens": tokens,
        "version": {"major": 0, "minor": 0, "patch": 1},
    }

    file_out = "tokenlist-mainnet.json"

    try:
        with open(file_out, "w", encoding="utf-8") as f:
            json5.dump(data, f, indent=4, quote_keys=True, trailing_commas=False)
            print(f"JSON5 file '{file_out}' created successfully.")
    except OSError as e:
        print(f"Error writing to file: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    main()
