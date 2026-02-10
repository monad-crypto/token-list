# @monad-crypto/token-list

[![npm](https://img.shields.io/npm/v/@monad-crypto/token-list)](https://www.npmjs.com/package/@monad-crypto/token-list)
[![license](https://img.shields.io/npm/l/@monad-crypto/token-list)](./LICENSE)

This package provides a publicly maintained registry of tokens on Monad. This token list is for informational and convenience purposes only. The inclusion of any token, asset, or contract address in this list does not constitute an endorsement, recommendation, or representation as to the safety, legitimacy, or value of such token. Use of this package and its contents is entirely at your own risk.

## Install

```sh
npm install @monad-crypto/token-list
# or
pnpm add @monad-crypto/token-list
# or
yarn add @monad-crypto/token-list
# or
bun add @monad-crypto/token-list
```

Requires Node.js 18 or later.

## Quick Start

```ts
import { mainnetTokenList } from "@monad-crypto/token-list";

// The full list object
console.log(mainnetTokenList.tokens); // Token[]

// Find a specific token
const usdc = mainnetTokenList.tokens.find((token) => token.symbol === "USDC");
```

## Token Interface

Each entry in the `tokens` array follows this structure:

```ts
interface Token {
  chainId: 143 | 10143;    // 143 = mainnet, 10143 = testnet
  address: `0x${string}`;  // checksum address
  name: string;            // e.g. "USD Coin"
  symbol: string;          // e.g. "USDC"
  decimals: number;        // e.g. 6
  logoURI: string;         // hosted image URL
  extensions?: {
    coinGeckoId?: string;
    bridgeInfo?: {
      protocol: "LayerZero OFT" | "Wormhole" | "Circle CCTP" | /* … */;
      bridgeAddress: `0x${string}`;
    };
    // token address on other chains, keyed by chain ID
    crossChainAddresses?: Record<string, { address: `0x${string}` }>;
  };
}
```

The `extensions` field is only present for tokens that have additional metadata. Bridged tokens include `bridgeInfo` and their canonical addresses on other chains in `crossChainAddresses`.

## License

MIT
