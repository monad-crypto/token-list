/**
 * TypeScript type definitions for the Monad token list.
 *
 * @module @monad-crypto/token-list
 */

// ---------------------------------------------------------------------------
// Address type
// ---------------------------------------------------------------------------

/** A hex-prefixed address. */
export type Address = `0x${string}`;

// ---------------------------------------------------------------------------
// Chain IDs
// ---------------------------------------------------------------------------

/** Monad Mainnet chain ID. */
export type MonadMainnetChainId = 143;

/** Monad Testnet chain ID. */
export type MonadTestnetChainId = 10143;

/** Any Monad chain ID. */
export type MonadChainId = MonadMainnetChainId | MonadTestnetChainId;

/** Known cross-chain EVM chain IDs. */
export type CrossChainId =
  | "1" // Ethereum
  | "10" // Optimism
  | "56" // BSC
  | "137" // Polygon
  | "999" // HyperEVM
  | "8453" // Base
  | "9745" // Plasma
  | "42161" // Arbitrum
  | "42220" // Celo
  | "43114"; // Avalanche

// ---------------------------------------------------------------------------
// Bridge protocol
// ---------------------------------------------------------------------------

/** Valid bridge protocol identifiers. */
export type BridgeProtocol =
  | "Chainlink CCIP"
  | "Circle CCTP"
  | "Hyperlane Warp Route"
  | "LayerZero OFT"
  | "Wormhole"
  | "Wormhole NTT";

// ---------------------------------------------------------------------------
// Token extensions
// ---------------------------------------------------------------------------

/** An address entry on a remote chain. */
export interface CrossChainAddressEntry {
  readonly address: Address;
  readonly symbol?: string;
  readonly decimals?: number;
}

/** Cross-chain address mapping, keyed by chain ID string. */
export type CrossChainAddresses = {
  readonly [K in CrossChainId]?: CrossChainAddressEntry;
};

/** Bridge information for a bridged token. */
export interface BridgeInfo {
  readonly protocol: BridgeProtocol;
  readonly bridgeAddress: Address;
}

/** Optional metadata extensions on a token. */
export interface TokenExtensions {
  readonly coinGeckoId?: string;
  readonly bridgeInfo?: BridgeInfo;
  readonly crossChainAddresses?: CrossChainAddresses;
}

// ---------------------------------------------------------------------------
// Token
// ---------------------------------------------------------------------------

/** A single token entry in the token list. */
export interface Token<ChainId extends number = MonadChainId> {
  readonly chainId: ChainId;
  readonly address: Address;
  readonly name: string;
  readonly symbol: string;
  readonly decimals: number;
  readonly logoURI: string;
  readonly extensions?: TokenExtensions;
}

/** A mainnet token (chainId is always 143). */
export type MainnetToken = Token<MonadMainnetChainId>;

/** A testnet token (chainId is always 10143). */
export type TestnetToken = Token<MonadTestnetChainId>;

// ---------------------------------------------------------------------------
// Version
// ---------------------------------------------------------------------------

/** Semantic version object. */
export interface Version {
  readonly major: number;
  readonly minor: number;
  readonly patch: number;
}

// ---------------------------------------------------------------------------
// Token list
// ---------------------------------------------------------------------------

/** Full token list structure. */
export interface TokenList<T extends Token = Token> {
  readonly name: string;
  readonly logoURI: string;
  readonly keywords: readonly string[];
  readonly timestamp: string;
  readonly tokens: readonly T[];
  readonly version: Version;
}

/** The mainnet token list type. */
export type MainnetTokenList = TokenList<MainnetToken>;

/** The testnet token list type. */
export type TestnetTokenList = TokenList<TestnetToken>;
