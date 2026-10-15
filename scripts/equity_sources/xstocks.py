"""xStocks (by Backed) listings from the xStocks public API."""

from dataclasses import dataclass

from utils.equities import (
    SOURCE_HTTP_TIMEOUT_SECONDS,
    EquitySource,
    Listing,
    create_source_session,
)
from utils.json_values import read_field, read_object_list
from utils.token_list import CrossChainId
from web3 import Web3

XSTOCKS_ASSETS_URL = "https://api.xstocks.fi/api/v2/public/assets"
XSTOCKS_API_HEADERS = {"Accept": "application/json"}
XSTOCKS_API_MAX_PAGES = 100
MONAD_NETWORK = "Monad"
ADDRESS_FIELD = "address"
# ERC-4626 wrapper over the xStock, for DeFi integrations that cannot handle the
# xStock's balance multiplier. Backed publishes no wrapper logos, so wrappers
# reuse their xStock's. The older "wrapperAddress" field predates Monad.
WRAPPER_ADDRESS_FIELD = "wrapperAddressV2"
# API network names for the chains the token list schema accepts as cross-chain IDs
CROSS_CHAIN_NETWORKS: dict[str, CrossChainId] = {
    "Ethereum": "1",
    "Optimism": "10",
    "BinanceSmartChain": "56",
    "HyperEVM": "999",
    "Arbitrum": "42161",
}


@dataclass(frozen=True)
class XStocksAsset:
    """The parts of an xStocks API asset the sync uses.

    Attributes:
        symbol: The xStock's symbol, e.g. AAPLx.
        logo: https URL of the xStock's logo.
        deployments: Raw deployment objects, one per network.
    """

    symbol: str
    logo: str
    deployments: list[dict[str, object]]


def parse_asset(value: object) -> XStocksAsset:
    """Narrow one decoded asset from the xStocks API.

    Args:
        value: The asset as decoded from JSON.

    Returns:
        XStocksAsset: The asset's symbol, logo and deployments.

    Raises:
        ValueError: If a field the sync uses is missing or has the wrong type.
    """
    return XStocksAsset(
        symbol=read_field(value, "symbol", str),
        logo=read_field(value, "logo", str),
        deployments=read_object_list(value, "deployments"),
    )


def fetch_xstocks_assets() -> list[XStocksAsset]:
    """Fetch every asset from the paginated xStocks API.

    Returns:
        list[XStocksAsset]: Every asset the API lists.

    Raises:
        requests.RequestException: If a page cannot be fetched.
        RuntimeError: If pagination does not end within XSTOCKS_API_MAX_PAGES.
        ValueError: If a page or asset does not have the expected shape.
    """
    assets: list[XStocksAsset] = []

    with create_source_session() as session:
        for page in range(XSTOCKS_API_MAX_PAGES):
            response = session.get(
                XSTOCKS_ASSETS_URL,
                params={"page": page},
                headers=XSTOCKS_API_HEADERS,
                timeout=SOURCE_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()

            assets.extend(parse_asset(node) for node in read_object_list(body, "nodes"))

            if not read_field(read_field(body, "page", dict), "hasNextPage", bool):
                return assets

    raise RuntimeError(f"xStocks API returned more than {XSTOCKS_API_MAX_PAGES} pages")


def map_deployments_by_network(asset: XStocksAsset, address_field: str) -> dict[str, str]:
    """Map each API network name to the checksummed address in one deployment field.

    Non-EVM deployments (Solana, TON, Tron) and deployments without the field
    are skipped.

    Args:
        asset: An asset from the xStocks API.
        address_field: Deployment field holding the address, e.g. ADDRESS_FIELD.

    Returns:
        dict[str, str]: API network name to checksummed address.

    Raises:
        ValueError: If a deployment with an address has no string network.
    """
    return {
        read_field(deployment, "network", str): Web3.to_checksum_address(address)
        for deployment in asset.deployments
        if isinstance(address := deployment.get(address_field), str) and Web3.is_address(address)
    }


def build_listing(deployments: dict[str, str], symbol: str, logo_uri: str) -> Listing:
    """Build a Monad listing with the cross-chain addresses the schema accepts.

    Args:
        deployments: API network name to checksummed address; must include Monad.
        symbol: Symbol the issuer claims for the token.
        logo_uri: https URL of the token logo.

    Returns:
        Listing: The token's listing.
    """
    return Listing(
        address=deployments[MONAD_NETWORK],
        symbol=symbol,
        logo_uri=logo_uri,
        cross_chain_addresses={
            chain_id: deployments[network]
            for network, chain_id in CROSS_CHAIN_NETWORKS.items()
            if network in deployments
        },
    )


def fetch_listings() -> list[Listing]:
    """Return every xStock deployed on Monad, plus its wrapper when it has one.

    Halted assets stay in the list: halts are usually temporary (e.g. corporate
    actions), and dropping them would cause a major version bump each time.
    Wrappers are listed whatever their supply, so minting or redeeming one never
    changes the list. A wrapper whose symbol is exactly an xStock's is skipped, and
    the xStock keeps the symbol.

    Returns:
        list[Listing]: The xStock listings followed by the wrapper listings.

    Raises:
        requests.RequestException: If the API cannot be reached.
        RuntimeError: If the API paginates without end.
        ValueError: If the API's response does not have the expected shape.
    """
    xstock_listings = []
    wrapper_listings = []

    for asset in fetch_xstocks_assets():
        deployments = map_deployments_by_network(asset, ADDRESS_FIELD)

        if MONAD_NETWORK not in deployments:
            continue

        xstock_listings.append(build_listing(deployments, asset.symbol, asset.logo))
        wrapper_deployments = map_deployments_by_network(asset, WRAPPER_ADDRESS_FIELD)

        if MONAD_NETWORK in wrapper_deployments:
            wrapper_listings.append(
                build_listing(wrapper_deployments, f"w{asset.symbol}", asset.logo)
            )

    colliding_symbols = find_symbol_collisions(wrapper_listings, xstock_listings)

    if colliding_symbols:
        print(
            "   - skipped wrappers whose symbol collides with an xStock: "
            + ", ".join(sorted(colliding_symbols))
        )

    return xstock_listings + [
        wrapper for wrapper in wrapper_listings if wrapper.symbol not in colliding_symbols
    ]


def find_symbol_collisions(wrappers: list[Listing], xstocks: list[Listing]) -> set[str]:
    """Return the wrapper symbols that are exactly an xStock's.

    A collision needs an xStock whose own symbol starts with a lowercase "w", which
    none does today.

    Args:
        wrappers: Wrapper listings.
        xstocks: xStock listings.

    Returns:
        set[str]: Symbols shared by a wrapper and an xStock; empty when none are.
    """
    return {wrapper.symbol for wrapper in wrappers} & {xstock.symbol for xstock in xstocks}


SOURCE = EquitySource(
    tag="xstocks",
    name="xStocks",
    description="Tokenized stocks and ETFs issued by Backed under the xStocks brand",
    fetch_listings=fetch_listings,
)
