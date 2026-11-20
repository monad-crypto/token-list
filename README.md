# Monad Token List

The Monad Token List provides a standardized list of tokens that have been deployed on Monad.

Links to the latest Monad token lists:

- [Monad Mainnet Token List](https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/tokenlist-mainnet.json)
- [Monad Testnet Token List](https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/tokenlist-testnet.json)
- [Tokenized Equities Token List](https://raw.githubusercontent.com/monad-crypto/token-list/refs/heads/main/tokenlist-equities.json)

This list serves as a resource for token issuers to submit token metadata for convenience and interoperability across ecosystem protocols, apps and interfaces. Inclusion of a token in this list does not imply endorsement, verification, or approval. No due diligence or verification is performed on token issuers or associated projects. It is imperative to conduct your own research before engaging with any token.

## Setup

1. Install dependencies:

```bash
uv sync
```

2. Configure environment variables:

```bash
cp .env.example .env
```

## Adding a New Token

For detailed instructions on how to add a new token to this repository, please see [CONTRIBUTING.md](CONTRIBUTING.md).

## Tokenized Equities

Tokenized equities (tokens tracking exchange-listed stocks and ETFs) are kept in a separate list, `tokenlist-equities.json`, published on npm as [`@monad-crypto/equities-token-list`](npm/equities). The list is generated weekly by `scripts/sync_equities.py` from each issuer's source in `scripts/equity_sources/`, currently [xStocks](https://xstocks.fi) and [Anchored](https://anchored.finance). Every token carries its issuer as a tag. Do not add tokenized equities to `mainnet/` by hand; to support a new issuer, add a source module.

To run the sync or the validation locally:

```bash
uv run python scripts/sync_equities.py
uv run python scripts/validate_equities.py
```

CI runs the validation with `--skip-onchain`, checking only the list's structure, since the sync already reads every token's name, symbol and decimals on-chain.
