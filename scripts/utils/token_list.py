"""Shared helpers for generating versioned token list files.

Both the curated mainnet list and the synced equities list use the same
versioning rules, so the diffing, version bumping and writing live here.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict, TypeGuard, get_args

from utils.json_values import read_field, read_object_list


class Version(TypedDict):
    """A token list version, following semantic versioning."""

    major: int
    minor: int
    patch: int


# Entries are only partly typed: versioning reads symbol and address, and compares
# the rest whole, so mainnet extensions need no schema here.
TokenEntry: TypeAlias = Mapping[str, object]
ChangeType: TypeAlias = Literal["major", "minor", "patch"]
# Chain IDs the token list schema accepts as crossChainAddresses keys; keep in sync
# with CrossChainId in npm/token-list/src/types.ts.
CrossChainId: TypeAlias = Literal[
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
]

LOGO_URI = (
    "https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/assets/monad.svg"
)
INITIAL_VERSION: Version = {"major": 1, "minor": 0, "patch": 0}
KNOWN_CROSS_CHAIN_IDS: frozenset[str] = frozenset(get_args(CrossChainId))


def is_cross_chain_id(value: object) -> TypeGuard[CrossChainId]:
    """Return whether value is a chain ID the schema accepts in crossChainAddresses.

    Args:
        value: A decoded JSON value.

    Returns:
        bool: True when value is one of KNOWN_CROSS_CHAIN_IDS.
    """
    return isinstance(value, str) and value in KNOWN_CROSS_CHAIN_IDS


def load_existing_token_list(output_path: Path) -> dict[str, object] | None:
    """Load existing token list if it exists.

    A file that exists but does not parse is an error rather than a missing list:
    treating it as missing would restart the version at INITIAL_VERSION.

    Args:
        output_path: Path to the existing token list file.

    Returns:
        dict[str, object] | None: Existing token list, or None if the file doesn't exist.

    Raises:
        OSError: If the file exists but cannot be read.
        ValueError: If the file is not valid JSON or not a JSON object.
    """
    if not output_path.exists():
        return None

    with output_path.open(mode="r", encoding="utf-8") as f:
        try:
            token_list = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{output_path.name} is not valid JSON: {e}") from e

    if not isinstance(token_list, dict):
        raise ValueError(f"{output_path.name} is not a JSON object")

    return token_list


def parse_version(value: object) -> Version:
    """Narrow a decoded JSON value to a Version.

    Args:
        value: The list's version field as decoded from JSON.

    Returns:
        Version: The version.

    Raises:
        ValueError: If value is not an object of integer major, minor and patch.
    """
    return {
        "major": read_field(value, "major", int),
        "minor": read_field(value, "minor", int),
        "patch": read_field(value, "patch", int),
    }


def find_repeated_values(values: list[str]) -> list[str]:
    """Return each value that appears again after its first occurrence.

    Args:
        values: Values to check, already normalized by the caller.

    Returns:
        list[str]: Repeats in order of appearance; empty when all are unique.
    """
    seen: set[str] = set()
    repeated = []

    for value in values:
        if value in seen:
            repeated.append(value)
        seen.add(value)

    return repeated


def collect_string_values(tokens: Sequence[TokenEntry], field: str) -> list[str]:
    """Return the field's value of every token that has it as a string.

    Args:
        tokens: Token list entries, possibly unvalidated.
        field: Name of the field to collect.

    Returns:
        list[str]: The string values in token order; tokens without one are skipped.
    """
    return [value for token in tokens if isinstance(value := token.get(field), str)]


def find_duplicate_errors(tokens: Sequence[TokenEntry]) -> list[str]:
    """Report symbols and addresses that appear more than once.

    Symbols are case-sensitive, so wENx and WENx are distinct tokens; addresses
    are compared case-insensitively since checksumming only changes their case.
    compare_tokens keys tokens by symbol, so a duplicate would also hide changes.

    Args:
        tokens: Token list entries; missing or non-string fields are left to the
            structural checks.

    Returns:
        list[str]: One message per duplicate; empty when all are unique.
    """
    repeated_symbols = find_repeated_values(collect_string_values(tokens, "symbol"))
    repeated_addresses = find_repeated_values(
        [address.lower() for address in collect_string_values(tokens, "address")]
    )

    return [f"duplicate symbol: {symbol}" for symbol in repeated_symbols] + [
        f"duplicate address: {address}" for address in repeated_addresses
    ]


def index_tokens_by_symbol(tokens: Sequence[TokenEntry]) -> dict[str, TokenEntry]:
    """Key token entries by their symbol.

    Args:
        tokens: Token list entries.

    Returns:
        dict[str, TokenEntry]: Entries keyed by symbol.

    Raises:
        ValueError: If an entry has no string symbol.
    """
    return {read_field(token, "symbol", str): token for token in tokens}


def compare_tokens(
    old_tokens: Sequence[TokenEntry], new_tokens: Sequence[TokenEntry]
) -> tuple[ChangeType, str] | None:
    """Compare old and new token lists to determine change type.

    Args:
        old_tokens: List of tokens from existing token list.
        new_tokens: List of tokens from newly generated token list.

    Returns:
        tuple[ChangeType, str] | None: (change_type, description), or None when
        nothing changed. change_type is one of:
            - "major": Tokens removed or addresses changed
            - "minor": Tokens added
            - "patch": Other changes (metadata, logoURI, etc.)

    Raises:
        ValueError: If an entry has no string symbol.
    """
    old_tokens_by_symbol = index_tokens_by_symbol(old_tokens)
    new_tokens_by_symbol = index_tokens_by_symbol(new_tokens)
    old_symbols = set(old_tokens_by_symbol)
    new_symbols = set(new_tokens_by_symbol)
    kept_symbols = old_symbols & new_symbols
    removed_symbols = old_symbols - new_symbols
    added_symbols = new_symbols - old_symbols

    if removed_symbols:
        return ("major", f"Tokens removed: {', '.join(sorted(removed_symbols))}")

    for symbol in sorted(kept_symbols):
        old_address = old_tokens_by_symbol[symbol].get("address")
        if old_address != new_tokens_by_symbol[symbol].get("address"):
            return ("major", f"Token address changed: {symbol}")

    if added_symbols:
        return ("minor", f"Tokens added: {', '.join(sorted(added_symbols))}")

    for symbol in kept_symbols:
        if old_tokens_by_symbol[symbol] != new_tokens_by_symbol[symbol]:
            return ("patch", "Token metadata updated")

    return None


def increment_version(current_version: Version, change_type: ChangeType) -> Version:
    """Increment version based on change type.

    Args:
        current_version: Current version dict with major, minor, patch.
        change_type: Which part of the version the change bumps.

    Returns:
        Version: New version with appropriate increment.
    """
    major = current_version["major"]
    minor = current_version["minor"]
    patch = current_version["patch"]

    if change_type == "major":
        return {"major": major + 1, "minor": 0, "patch": 0}

    if change_type == "minor":
        return {"major": major, "minor": minor + 1, "patch": 0}

    return {"major": major, "minor": minor, "patch": patch + 1}


def write_token_list(token_list: Mapping[str, object], output_path: Path) -> None:
    """Write the token list as JSON indented by two spaces.

    Args:
        token_list: The token list data structure.
        output_path: Path where the file should be written.

    Raises:
        IOError: If the file cannot be written.
    """
    try:
        with output_path.open(mode="w", encoding="utf-8") as f:
            json.dump(token_list, f, indent=2)
    except OSError as e:
        raise OSError(f"Cannot write to {output_path}: {e}") from e


def format_version(version: Version) -> str:
    """Format the version number as a string.

    Args:
        version: Version dict with major, minor, patch.

    Returns:
        str: Formatted version string.
    """
    return f"{version['major']}.{version['minor']}.{version['patch']}"


def create_token_list(
    name: str,
    keywords: list[str],
    tokens: Sequence[TokenEntry],
    version: Version,
    timestamp: str,
    tags: dict[str, dict[str, str]] | None = None,
) -> dict[str, object]:
    """Create the token list structure.

    Args:
        name: Display name of the list.
        keywords: List keywords.
        tokens: List of token data dictionaries.
        version: Version dict with major, minor, patch.
        timestamp: ISO format timestamp string.
        tags: Optional tag definitions keyed by tag ID, as {"name", "description"}.

    Returns:
        dict[str, object]: Complete token list structure.
    """
    token_list: dict[str, object] = {"name": name, "logoURI": LOGO_URI, "keywords": keywords}

    if tags:
        token_list["tags"] = tags

    return token_list | {"timestamp": timestamp, "tokens": tokens, "version": version}


def resolve_next_version(
    existing_token_list: Mapping[str, object] | None, new_tokens: Sequence[TokenEntry]
) -> tuple[Version | None, list[str]]:
    """Decide the version of the regenerated list.

    Args:
        existing_token_list: The currently committed list, or None if there is none yet.
        new_tokens: Tokens of the regenerated list.

    Returns:
        tuple[Version | None, list[str]]: The new version, or None when nothing changed
        and the list should not be rewritten; and report lines explaining the decision.

    Raises:
        ValueError: If the existing list's version or tokens are malformed.
    """
    if existing_token_list is None:
        return INITIAL_VERSION.copy(), ["Generating token list for the first time..."]

    old_version = parse_version(existing_token_list.get("version"))
    change = compare_tokens(read_object_list(existing_token_list, "tokens"), new_tokens)

    if change is None:
        return None, [
            "No changes detected. Token list remains unchanged.",
            f"   - Current version: {format_version(old_version)}",
        ]

    change_type, change_description = change
    new_version = increment_version(old_version, change_type)

    return new_version, [
        f"Changes detected: {change_description}",
        f"   - Change type: {change_type}",
        f"   - Version: {format_version(old_version)} -> {format_version(new_version)}",
    ]
