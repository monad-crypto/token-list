# @monad-crypto/equities-token-list

[![npm](https://img.shields.io/npm/v/@monad-crypto/equities-token-list)](https://www.npmjs.com/package/@monad-crypto/equities-token-list)
[![license](https://img.shields.io/npm/l/@monad-crypto/equities-token-list)](./LICENSE)

This package provides the list of tokenized equities (stocks and ETFs) deployed on Monad, across issuers. It is generated weekly from issuer sources and kept separate from [`@monad-crypto/token-list`](https://www.npmjs.com/package/@monad-crypto/token-list). This token list is for informational and convenience purposes only. The inclusion of any token, asset, or contract address in this list does not constitute an endorsement, recommendation, or representation as to the safety, legitimacy, or value of such token. Use of this package and its contents is entirely at your own risk.

## Install

```sh
npm install @monad-crypto/equities-token-list
# or
pnpm add @monad-crypto/equities-token-list
# or
yarn add @monad-crypto/equities-token-list
# or
bun add @monad-crypto/equities-token-list
```

Requires Node.js 18 or later.

## Quick Start

```ts
import { equitiesTokenList } from "@monad-crypto/equities-token-list";

const appleTokens = equitiesTokenList.tokens.filter((token) => ["AAPLx", "aAAPL"].includes(token.symbol));

// Only one issuer's tokens
const anchoredTokens = equitiesTokenList.tokens.filter((token) => token.tags?.includes("anchored"));
```

Tokens use the same `Token` interface as [`@monad-crypto/token-list`](https://www.npmjs.com/package/@monad-crypto/token-list#token-interface). Each token carries one issuer tag, defined in the list's `tags` map:

| Tag | Issuer | Source |
| --- | --- | --- |
| `anchored` | [Anchored](https://anchored.finance) | Anchored token documentation |
| `xstocks` | [xStocks](https://xstocks.fi) by Backed | xStocks public API |

`crossChainAddresses` lists the token on the other supported EVM chains, where issuers use the same address as on Monad. `coinGeckoId` is set when CoinGecko lists the token, matched by contract address on Monad or one of those chains. Logos are served by the issuers.

## What belongs here

Tokens that track exchange-listed shares: stocks and ETFs. Tokenized funds, treasuries and credit products belong in [`@monad-crypto/token-list`](https://www.npmjs.com/package/@monad-crypto/token-list).

## License

MIT
