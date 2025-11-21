#!/usr/bin/env python3
"""Generate tokenlist-mainnet.json from individual token files.

This script aggregates all token definitions from the mainnet/ directory
and generates a consolidated token list file following the token list standard.
Version numbers are automatically incremented based on changes:
- Major: tokens removed or addresses changed
- Minor: tokens added
- Patch: any other changes
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import json5

DATA_DIR = "mainnet"
OUTPUT_FILE = "tokenlist-mainnet.json"
TOKEN_LIST_NAME = "Monad Mainnet"
LOGO_URI = (
    "https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/assets/monad.svg"
)
KEYWORDS = ["monad mainnet"]
DEFAULT_VERSION_MAJOR = 1
DEFAULT_VERSION_MINOR = 0
DEFAULT_VERSION_PATCH = 0


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


def load_existing_token_list(output_path: Path) -> Optional[dict[str, Any]]:
    """Load existing token list if it exists.

    Args:
        output_path: Path to the existing token list file.

    Returns:
        Optional[dict]: Existing token list or None if file doesn't exist.
    """
    if not output_path.exists():
        return None

    try:
        with output_path.open(mode="r", encoding="utf-8") as f:
            return json5.load(f)
    except (OSError, ValueError):
        return None


def compare_tokens(
    old_tokens: list[dict[str, Any]], new_tokens: list[dict[str, Any]]
) -> tuple[Optional[str], Optional[str]]:
    """Compare old and new token lists to determine change type.

    Args:
        old_tokens: List of tokens from existing token list.
        new_tokens: List of tokens from newly generated token list.

    Returns:
        tuple[str, str]: (change_type, description) where change_type is one of:
            - None: No changes
            - "major": Tokens removed or addresses changed
            - "minor": Tokens added
            - "patch": Other changes (metadata, logoURI, etc.)
    """
    old_map = {t["symbol"]: t for t in old_tokens}
    new_map = {t["symbol"]: t for t in new_tokens}

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # Check for removed tokens
    removed = old_keys - new_keys
    if removed:
        return ("major", f"Tokens removed: {', '.join(removed)}")

    # Check for address changes in existing tokens
    for key in old_keys & new_keys:
        if old_map[key]["address"] != new_map[key]["address"]:
            return ("major", f"Token address changed: {key}")

    # Check for added tokens
    added = new_keys - old_keys
    if added:
        return ("minor", f"Tokens added: {', '.join(added)}")

    # Check for any other changes in existing tokens
    for key in old_keys & new_keys:
        old_token = old_map[key]
        new_token = new_map[key]
        # Compare all fields except address (already checked)
        if old_token != new_token:
            return ("patch", "Token metadata updated")

    return (None, None)


def increment_version(current_version: dict[str, int], change_type: str) -> dict[str, int]:
    """Increment version based on change type.

    Args:
        current_version: Current version dict with major, minor, patch.
        change_type: Type of change ("major", "minor", "patch", or None).

    Returns:
        dict: New version with appropriate increment.
    """
    major = current_version["major"]
    minor = current_version["minor"]
    patch = current_version["patch"]

    if change_type == "major":
        return {"major": major + 1, "minor": 0, "patch": 0}
    if change_type == "minor":
        return {"major": major, "minor": minor + 1, "patch": 0}
    if change_type == "patch":
        return {"major": major, "minor": minor, "patch": patch + 1}
    return {"major": major, "minor": minor, "patch": patch}


def create_token_list(
    tokens: list[dict[str, Any]], version: dict[str, int], timestamp: str
) -> dict[str, Any]:
    """Create the token list structure.

    Args:
        tokens: List of token data dictionaries.
        version: Version dict with major, minor, patch.
        timestamp: ISO format timestamp string.

    Returns:
        dict: Complete token list structure.
    """
    return {
        "name": TOKEN_LIST_NAME,
        "logoURI": LOGO_URI,
        "keywords": KEYWORDS,
        "timestamp": timestamp,
        "tokens": tokens,
        "version": version,
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
                indent=2,
                quote_keys=True,
                trailing_commas=False,
            )
    except OSError as e:
        raise OSError(f"Cannot write to {output_path}: {e}") from e


def format_version(version: dict[str, int]) -> str:
    """Format the version number as a string.

    Args:
        version: Version dict with major, minor, patch.

    Returns:
        str: Formatted version string.
    """
    return f"{version['major']}.{version['minor']}.{version['patch']}"


def main() -> int:
    """Main entry point for the token list generator.

    Returns:
        int: Exit code (0 for success, 1 for failure).
    """
    try:
        data_dir = get_data_directory()
        output_path = Path(__file__).resolve().parent.parent / OUTPUT_FILE

        token_dirs = get_token_dirs(data_dir)
        if not token_dirs:
            print(f"No token files found in {DATA_DIR}/")
            return 0

        print(f"Processing {len(token_dirs)} token(s)...")

        existing_token_list = load_existing_token_list(output_path)
        new_tokens = load_all_tokens(token_dirs)

        if existing_token_list:
            # Compare with existing token list
            change_type, change_description = compare_tokens(
                existing_token_list.get("tokens", []), new_tokens
            )
            old_version = existing_token_list["version"]

            if change_type is None:
                print("No changes detected. Token list remains unchanged.")
                print(f"   - Current version: {format_version(old_version)}")
                return 0

            new_version = increment_version(existing_token_list["version"], change_type)
            new_timestamp = datetime.now(timezone.utc).isoformat()

            print(f"Changes detected: {change_description}")
            print(f"   - Change type: {change_type}")
            print(f"   - Version: {format_version(old_version)} -> {format_version(new_version)}")
        else:
            # First time generation
            new_version = {
                "major": DEFAULT_VERSION_MAJOR,
                "minor": DEFAULT_VERSION_MINOR,
                "patch": DEFAULT_VERSION_PATCH,
            }
            new_timestamp = datetime.now(timezone.utc).isoformat()
            print("Generating token list for the first time...")

        token_list = create_token_list(new_tokens, new_version, new_timestamp)
        write_token_list(token_list, output_path)

        print(f"Successfully created '{OUTPUT_FILE}'")
        print(f"   - {len(new_tokens)} token(s) included")
        print(f"   - Timestamp: {token_list['timestamp']}")
        print(f"   - Version: {format_version(new_version)}")

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
