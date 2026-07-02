"""Web3 utilities for fetching token data from the blockchain.

This module provides utilities for connecting to the blockchain and fetching
ERC20 token metadata with retry logic.
"""

import os
import time

from web3 import Web3
from web3.exceptions import Web3Exception

CHAIN_ID = 143
DEFAULT_RPC_URL = "https://rpc.monad.xyz"
RPC_URL = os.environ.get("MONAD_RPC_URL", DEFAULT_RPC_URL)

# Chain RPC configuration with environment variable overrides
CHAIN_RPC_URLS = {
    "1": os.environ.get("ETH_RPC_URL", "https://ethereum-rpc.publicnode.com"),
    "10": os.environ.get("OPTIMISM_RPC_URL", "https://mainnet.optimism.io"),
    "56": os.environ.get("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
    "137": os.environ.get("POLYGON_RPC_URL", "https://polygon-bor-rpc.publicnode.com"),
    "999": os.environ.get("HYPEREVM_RPC_URL", "https://rpc.hyperliquid.xyz/evm"),
    "8453": os.environ.get("BASE_RPC_URL", "https://mainnet.base.org"),
    "9745": os.environ.get("PLASMA_RPC_URL", "https://rpc.plasma.to"),
    "42161": os.environ.get("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc"),
    "42220": os.environ.get("CELO_RPC_URL", "https://forno.celo.org"),
    "43114": os.environ.get("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc"),
}

CHAIN_NAMES = {
    "1": "Ethereum",
    "10": "Optimism",
    "56": "BNB Chain",
    "137": "Polygon",
    "999": "HyperEVM",
    "8453": "Base",
    "9745": "Plasma",
    "42161": "Arbitrum One",
    "42220": "Celo",
    "43114": "Avalanche",
}
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]
OFT_BRIDGE_ABI = [
    {
        "inputs": [],
        "name": "token",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
WORMHOLE_ABI = [
    {
        "inputs": [],
        "name": "chainId",
        "outputs": [{"name": "", "type": "uint16"}],
        "stateMutability": "view",
        "type": "function",
    }
]
WORMHOLE_NTT_MANAGER_ABI = [
    {
        "inputs": [],
        "name": "token",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
HYPERLANE_WARP_ROUTE_ABI = [
    {
        "inputs": [],
        "name": "wrappedToken",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]
M0_M_TOKEN_ABI = [
    {
        "inputs": [],
        "name": "mToken",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

CCTP_TOKEN_MINTER_V2_ADDRESS = "0xfd78EE919681417d192449715b2594ab58f5D002"
CCTP_TOKEN_MINTER_V2_ABI = [
    {
        "inputs": [{"name": "token", "type": "address"}],
        "name": "burnLimitsPerMessage",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

CCIP_TOKEN_ADMIN_REGISTRY_ADDRESS = "0x11ACd984DD680363117B310f6ebdf78fD6c0195f"
CCIP_TOKEN_ADMIN_REGISTRY_ABI = [
    {
        "inputs": [{"name": "token", "type": "address"}],
        "name": "getTokenConfig",
        "outputs": [
            {
                "components": [
                    {"name": "administrator", "type": "address"},
                    {"name": "pendingAdministrator", "type": "address"},
                    {"name": "tokenPool", "type": "address"},
                ],
                "name": "",
                "type": "tuple",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_RETRY_BACKOFF = 2.0  # exponential backoff multiplier


def _retry_with_backoff(
    func,
    max_retries: int,
    retry_delay: float,
    retry_backoff: float,
    operation_name: str,
):
    """Execute a function with retry logic and exponential backoff.

    Args:
        func: Callable to execute (should raise exception on failure).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.
        operation_name: Name of the operation for error messages.

    Returns:
        The return value of the successful function call.

    Raises:
        Exception: If all retries fail.
    """
    current_delay = retry_delay
    last_exception = None

    for attempt in range(max_retries):
        try:
            return func()
        except (Web3Exception, Exception) as e:
            last_exception = e
            if attempt < max_retries - 1:
                time.sleep(current_delay)
                current_delay *= retry_backoff
            continue
    raise Exception(
        f"Failed to {operation_name} after {max_retries} attempts: {last_exception}"
    ) from last_exception


def get_web3_connection(rpc_url: str | None = None) -> Web3:
    """Get a Web3 connection to the blockchain.

    Args:
        rpc_url: Optional RPC URL. If not provided, uses RPC_URL constant.

    Returns:
        Web3: Connected Web3 instance.

    Raises:
        ConnectionError: If unable to connect to the RPC.
    """
    url = rpc_url or RPC_URL
    web3 = Web3(Web3.HTTPProvider(url))

    if not web3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC at {url}")

    return web3


def get_web3_connection_for_chain(chain_id: str) -> Web3 | None:
    """Get a Web3 connection for a specific chain.

    Args:
        chain_id: The chain ID as a string (e.g., "1" for Ethereum).

    Returns:
        Web3 | None: Connected Web3 instance, or None if chain is not supported
        or connection fails.
    """
    if chain_id not in CHAIN_RPC_URLS:
        return None

    rpc_url = CHAIN_RPC_URLS[chain_id]
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not web3.is_connected():
            return None
        return web3
    except Exception:
        return None


def validate_address(address: str) -> str:
    """Validate and normalize an Ethereum address.

    Args:
        address: The address string to validate.

    Returns:
        str: Checksummed address.

    Raises:
        ValueError: If the address is invalid.
    """
    if not Web3.is_address(address):
        raise ValueError(f"Invalid Ethereum address: {address}")

    return Web3.to_checksum_address(address)


def fetch_token_data_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> dict:
    """Fetch token data from the blockchain with retry logic.

    Fetches each field separately to avoid redundant retries if only one field fails.

    Args:
        web3: Web3 instance connected to the chain.
        address: Token contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        dict: Token data containing chainId, address, name, symbol, and decimals.

    Raises:
        Exception: If fetching any token field fails after all retries.
    """
    name = fetch_token_name_with_retry(web3, address, max_retries, retry_delay, retry_backoff)
    symbol = fetch_token_symbol_with_retry(web3, address, max_retries, retry_delay, retry_backoff)
    decimals = fetch_token_decimals_with_retry(
        web3, address, max_retries, retry_delay, retry_backoff
    )

    return {
        "chainId": CHAIN_ID,
        "address": address,
        "name": name,
        "symbol": symbol,
        "decimals": decimals,
    }


def fetch_token_name_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch token name from the blockchain with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Token contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: Token name.

    Raises:
        Exception: If fetching the name fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=ERC20_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.name().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch name",
    )


def fetch_token_symbol_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch token symbol from the blockchain with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Token contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: Token symbol.

    Raises:
        Exception: If fetching the symbol fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=ERC20_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.symbol().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch symbol",
    )


def fetch_token_decimals_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> int:
    """Fetch token decimals from the blockchain with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Token contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        int: Token decimals.

    Raises:
        Exception: If fetching the decimals fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=ERC20_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.decimals().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch decimals",
    )


def fetch_wormhole_chain_id_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> int:
    """Fetch chainId from a Wormhole bridge contract with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        int: chainId (uint16)

    Raises:
        Exception: If fetching the chainId fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=WORMHOLE_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.chainId().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch chainId",
    )


def fetch_wormhole_ntt_token_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch token address from a Wormhole NTT bridge contract with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: token address

    Raises:
        Exception: If fetching the token fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=WORMHOLE_NTT_MANAGER_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.token().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch token",
    )


def fetch_oft_bridge_token_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch token address from an OFT bridge contract with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: OFT bridge contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: Token address

    Raises:
        Exception: If fetching the token fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=OFT_BRIDGE_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.token().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch bridge token",
    )


def fetch_hyperlane_wrapped_token_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch wrapped token address from a Hyperlane bridge contract with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        address: Hyperlane bridge contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: Wrapped token address

    Raises:
        Exception: If fetching the wrapped token fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=HYPERLANE_WARP_ROUTE_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.wrappedToken().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch wrapped token",
    )


def fetch_m0_m_token_with_retry(
    web3: Web3,
    address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> str:
    """Fetch the canonical $M token address by calling mToken() with retry logic.

    Works for both an M0 Portal (returns the $M it bridges) and M extension tokens.

    Args:
        web3: Web3 instance connected to the chain.
        address: M0 Portal or M extension contract address (should be checksummed).
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        str: The canonical $M token address.

    Raises:
        Exception: If fetching the M token fails after all retries.
    """
    contract = web3.eth.contract(address=address, abi=M0_M_TOKEN_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.mToken().call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch M0 M token",
    )


def fetch_cctp_burn_limits_per_message_with_retry(
    web3: Web3,
    contract_address: str,
    token_address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> int:
    """Fetch burnLimitsPerMessage for a token from the CCTP minter contract.

    Args:
        web3: Web3 instance connected to the chain.
        contract_address: CCTP minter contract address.
        token_address: Token contract address to query.
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        int: burn limit per message for the token.

    Raises:
        Exception: If fetching the burn limit fails after all retries.
    """
    contract = web3.eth.contract(address=contract_address, abi=CCTP_TOKEN_MINTER_V2_ABI)
    return _retry_with_backoff(
        lambda: contract.functions.burnLimitsPerMessage(token_address).call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch CCTP burn limits per message",
    )


def fetch_ccip_token_config_with_retry(
    web3: Web3,
    token_address: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    retry_backoff: float = DEFAULT_RETRY_BACKOFF,
) -> tuple[str, str, str]:
    """Fetch CCIP token config from the admin registry with retry logic.

    Args:
        web3: Web3 instance connected to the chain.
        token_address: Token contract address to query.
        max_retries: Maximum number of retry attempts.
        retry_delay: Initial delay between retries in seconds.
        retry_backoff: Multiplier for exponential backoff.

    Returns:
        tuple[str, str, str]: (administrator, pendingAdministrator, tokenPool)

    Raises:
        Exception: If fetching the token config fails after all retries.
    """
    contract = web3.eth.contract(
        address=CCIP_TOKEN_ADMIN_REGISTRY_ADDRESS,
        abi=CCIP_TOKEN_ADMIN_REGISTRY_ABI,
    )
    return _retry_with_backoff(
        lambda: contract.functions.getTokenConfig(token_address).call(),
        max_retries,
        retry_delay,
        retry_backoff,
        "fetch CCIP token config",
    )
