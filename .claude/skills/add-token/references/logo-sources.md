# Sourcing a token logo

The goal is a crisp, square logo that is provably *this* token's, not a look-alike's. The order below is about evidence quality, not convenience: address-keyed sources come first because a spam clone can copy a name and symbol but cannot put its address into the real project's app or docs.

CoinGecko's `image` is listed last on purpose. It is a 250x250 PNG, and until you have address-verified the coin (step 3) it is only as trustworthy as a name match - which is exactly what a clone defeats. Reach for it only when the address-keyed sources below genuinely come up empty, not as the default.

## Why official apps and integrations are the best source

A DeFi token almost always appears in a web front-end: the project's own app, and often integrations that list it - Morpho and Euler (as collateral or a vault asset), Pendle (as an underlying), a DEX or lending market. These front-ends render a per-token icon, and they look that icon up **by contract address**. So once you have the exact address, the app hands you the right icon with high confidence - and it is usually an SVG (sharp at any size) rather than a rasterised PNG.

The icon lives in one of two forms, both of which the bundled `fetch_app_logo.py` harvests:

1. **A build asset the page links**, e.g. `/assets/PRIME-CI4jqIYf.svg` - the filename often starts with the token symbol.
2. **An inline `data:image/svg+xml` URI** embedded in a JS bundle and referenced from the token's config object, e.g. `{symbol:"jrUSDat", address:"0x011e...", asset:She, networks:[...]}` where elsewhere `She="data:image/svg+xml,<svg ...>"`.

## Finding the app URL

Start from a legitimate, address-verified origin - the same trust policy as the rest of the skill:

- The address-verified CoinGecko coin's `homepage` (from `coingecko_lookup.py`) - the project's own site.
- The project's official docs / GitHub, reached from that homepage.
- A known integration's app when the token is a vault/collateral there (`app.morpho.org`, `app.euler.finance`, `app.pendle.finance`, ...). Use these when the token *is* a position in that protocol - its name often says so ("Pareto AA Tranche", "Morpho ... Vault").

**Do not assume the app lives at `app.<domain>`.** It might be the domain root itself, a path like `/app` or `/earn`, or a separate host reached by a "Launch App" link - and a marketing homepage is frequently just a JS shell with no static links at all. So dig for it rather than guessing: `fetch_app_logo.py` does this for you when you hand it the project homepage - it mines both the HTML and the homepage's JS bundles for same-domain URLs that point at an app (an `app.`/`dapp.` host, an app-ish path), follows what it finds, and only then falls back to conventional host guesses. In practice you can pass the homepage and let discovery reach the app; pass an explicit app/integration URL when you already know it. Do not take an app URL from general search results or an aggregator. If you cannot establish the official site from an address-verified coin or official docs, treat the logo as not-yet-found rather than guess.

## Using `fetch_app_logo.py`

```bash
uv run --env-file=.env python .claude/skills/add-token/scripts/fetch_app_logo.py \
  --app-url https://<app domain> \
  --address <monad address> \
  --extra-address 1=<eth address> \
  --symbol <SYMBOL> \
  --out mainnet/<SYMBOL>
```

- Pass **any** address the app is likely to key on. The Monad address is the obvious first try, but an app may only list the token's Ethereum (or other home-chain) deployment - the cross-chain address you established in step 4. Give each via `--extra-address CHAINID=0x...` (repeatable), since the integrations key by the address on each chain.
- `--app-url` is repeatable; pass the project app and any integration app in one run to sweep them together. It is optional - the integrations below run from the address/symbol even with no app URL.
- Output is a JSON report with a ranked `candidates` list. Each candidate has:
  - `match`: which signals tied it to the token - `symbol-filename` (asset file named after the symbol), `symbol-url` / `address-url` / `symbol-nearby` (an image URL keyed by the symbol in its filename, by the address next to it, or by the symbol as nearby text), `address-config` / `symbol-config` (an icon next to the address/symbol in a config object), the integration signals `morpho-address` / `euler-address` / `curvance-symbol` / `mento-symbol`, or `unmatched`. Multiple signals joined by `+` mean they agreed on the same icon.
  - `confidence`: `strong` when an address-keyed registry hit, an official-app symbol lookup, or >=2 signals agree; `weak` otherwise.
  - `ext`: `svg`, `png`, or `webp` - save with this extension.
  - `dimensions`, `resize_note`, and `saved_path`. Candidates are listed best-first, so `candidates[0]` is the top pick. (The number in the filename is discovery order, not rank.)

**Picking a candidate.** Take the top `strong` candidate. The ranking is by evidence quality: a candidate keyed to the contract address (the app's own address-anchored icon, then a trusted integration's per-address icon) ranks above one matched by symbol name or nearby text, and an SVG ranks above a raster. This is deliberate: symbol-name and symbol-text matches routinely grab a brand logo or an illustration rather than the coin icon, whereas the address-keyed icon is provably this token. Be deliberate when only `weak` candidates exist or several disagree: a token's config can reference a *sibling* token's address (paired tranches, a collateral link), so an address-only hit occasionally resolves to the wrong icon. Open the SVG and sanity-check it depicts this token before writing it.

## Integrations (Morpho, Euler, Curvance, Mento)

Some tokens have no icon in any static app bundle because the listing app loads its token list at runtime. For those, the script also queries integration front-ends that expose an address- or symbol-keyed logo source directly. These run automatically from `--address` / `--extra-address` / `--symbol` (no `--app-url` needed) unless you pass `--no-integrations`:

- **Morpho** - `POST https://blue-api.morpho.org/graphql` returns `{address, logoURI}` per chain (queried one chain at a time, since a chainId Morpho does not index errors the whole request); matched logos live on `cdn.morpho.org` (SVG). Address-keyed, so it produces a `morpho-address` signal.
- **Euler** - `https://token-images.euler.finance/{chainId}/{address}` serves the logo directly (SVG/PNG/WebP); membership is confirmed against `https://app.euler.finance/api/public/metadata?chainId=<c>` so the generic fallback image is skipped. `euler-address` signal.
- **Symbol-path apps** - some apps serve an icon per token at `/<path>/<symbol>.svg`: **Curvance** (`app.curvance.com/tokens/<symbol>.svg`) and **Mento** (`app.mento.org/tokens/<symbol>.svg`). Every symbol casing is tried (Curvance is lowercase, Mento is exact-case); a miss returns a non-image page and is discarded. `<name>-symbol` signal. Add another by appending one line to `SYMBOL_PATH_APPS`.

Because Morpho and Euler key by the address *on each chain*, pass every cross-chain address (`--extra-address 1=0x...`) - a token often appears only under its Ethereum deployment. These hosts are hardcoded and vetted, so their fetches gate on the integration's own domain rather than the mined app's, which keeps the SSRF constraint intact. Address-keyed integration hits and the official-app symbol lookups all rank as `strong`.

## Non-token images are filtered out

The URL-icon matchers (`symbol-url` / `address-url` / `symbol-nearby`) would otherwise pull in share/preview banners, favicons, the app's own brand logo, or a chain-selector icon sitting near the token name. Two guards prevent that: a path denylist (`og`, `social`, `banner`, `favicon`, `logo`, `branding`, `network`, common chain names, ...) matched on the URL's alphabetic runs so a glued `Favicon32x32` still trips it, and an aspect/size check that drops anything not roughly square or under 100px. The symbol is matched as a bounded token, not a substring, so `EUL` does not match `euler-symbol`.

## Resizing (why the script rewrites width/height)

`validate_tokens.py` reads the SVG's `width`/`height` attributes and rejects anything under 200px, even though an SVG scales to any size. App icons are frequently declared at their display size (e.g. `40x40`). When the art has a square `viewBox`, the script bumps `width`/`height` up to 256 and leaves the `viewBox` untouched - the geometry and therefore the rendered image are identical, only the two declared attributes change. `resize_note` records what it did. Pass `--no-resize` to disable this if you want the untouched original.

## Manual extraction (when the script cannot resolve it)

Minified bundles vary; if the script returns only `unmatched` candidates or misses the icon, do it by hand - the technique is the same:

1. Fetch the app page, note the linked JS/CSS assets (`<script src>`, `<link href>`), and download them.
2. Search a bundle for the contract address or `symbol:"<SYMBOL>"` to find the token's config object, and read off its `asset:`/`icon:` identifier.
3. Find that identifier's definition. If it is `="data:image/svg+xml,..."`, URL-decode (or base64-decode) the part after the comma into an `.svg`. If it is a `.svg` path, download it from the app origin.
4. Confirm the SVG is square (equal `width`/`height`, or a square `viewBox`) and bump the declared size to >=200 if needed, then save as `logo.svg`.

## Finishing

Save the chosen candidate as `mainnet/<SYMBOL>/logo.<ext>` and remove any other candidate files (and any earlier PNG) so only the final logo remains. If every avenue - official app, integrations, official brand assets, and finally the address-verified CoinGecko image - comes up empty, leave the logo missing and say so in the report. Validation will fail until a human adds one, which is the intended behavior.
