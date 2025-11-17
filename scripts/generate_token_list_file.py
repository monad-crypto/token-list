#!/usr/bin/env python3
"""Generate tokenlist-mainnet.json from individual token files.

This script aggregates all token definitions from the mainnet/ directory
and generates a consolidated token list file following the token list standard.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json5

DATA_DIR = "mainnet"
OUTPUT_FILE = "tokenlist-mainnet.json"
TOKEN_LIST_NAME = "Monad Mainnet"
LOGO_URI = (
    "https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/assets/monad.svg"
)
KEYWORDS = ["monad mainnet"]
VERSION_MAJOR = 0
VERSION_MINOR = 0
VERSION_PATCH = 1


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


def load_token_data(dir_path: Path) -> dict[str, Any]:
    """Load token data from a directory containing data.json and optional logo file.

    Args:
        dir_path: Path to the token directory.

    Returns:
        dict: Token data as a dictionary. If a logo file (logo.svg or logo.png)
              exists in the directory, a logoURI field is added.

    Raises:
        ValueError: If the file cannot be parsed.
        OSError: If the file cannot be read.
    """
    try:
        filepath = dir_path / "data.json"
        with filepath.open(mode="r", encoding="utf-8") as f:
            token_data = json5.load(f)

            logo_uri = None
            for logo_filename in ["logo.svg", "logo.png"]:
                logo_path = dir_path / logo_filename
                if logo_path.exists():
                    logo_uri = logo_path
                    break

            if logo_uri:
                root_dir = Path(__file__).resolve().parent.parent
                token_data["logoURI"] = (
                    f"https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/{logo_uri.relative_to(root_dir)}"
                )

            return token_data
    except ValueError as e:
        raise ValueError(f"Invalid JSON5 in {filepath}: {e}") from e
    except OSError as e:
        raise OSError(f"Cannot read {filepath}: {e}") from e


def load_all_tokens(token_dirs: list[Path]) -> list[dict[str, Any]]:
    """Load all token data from a list of files.

    Args:
        token_dirs: List of token directory paths.

    Returns:
        list[dict]: List of token data dictionaries.

    Raises:
        ValueError: If any token file cannot be parsed.
        IOError: If any token file cannot be read.
    """
    return [load_token_data(dir_path) for dir_path in token_dirs]


def create_token_list(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    """Create the token list structure.

    Args:
        tokens: List of token data dictionaries.

    Returns:
        dict: Complete token list structure.
    """
    return {
        "name": TOKEN_LIST_NAME,
        "logoURI": LOGO_URI,
        "keywords": KEYWORDS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tokens": tokens,
        "version": {
            "major": VERSION_MAJOR,
            "minor": VERSION_MINOR,
            "patch": VERSION_PATCH,
        },
    }


def write_token_list(token_list: dict[str, Any], output_path: Path) -> None:
    """Write the token list to a JSON5 file.

    Args:
        token_list: The token list data structure.
        output_path: Path where the file should be written.

    Raises:
        IOError: If the file cannot be written.
    """
    try:
        with output_path.open(mode="w", encoding="utf-8") as f:
            json5.dump(
                token_list,
                f,
                indent=4,
                quote_keys=True,
                trailing_commas=False,
            )
    except OSError as e:
        raise OSError(f"Cannot write to {output_path}: {e}") from e


def main() -> int:
    """Main entry point for the token list generator.

    Returns:
        int: Exit code (0 for success, 1 for failure).
    """
    try:
        data_dir = get_data_directory()

        token_dirs = get_token_dirs(data_dir)
        if not token_dirs:
            print(f"No token files found in {DATA_DIR}/")
            return 0

        print(f"Processing {len(token_dirs)} token(s)...")

        tokens = load_all_tokens(token_dirs)
        token_list = create_token_list(tokens)

        output_path = Path(__file__).resolve().parent.parent / OUTPUT_FILE
        write_token_list(token_list, output_path)

        print(f"Successfully created '{OUTPUT_FILE}'")
        print(f"   - {len(tokens)} token(s) included")
        print(f"   - Timestamp: {token_list['timestamp']}")

        return 0
    except FileNotFoundError as e:
        print(f"{e}")
        return 1
    except (OSError, ValueError) as e:
        print(f"{e}")
        return 1
    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
