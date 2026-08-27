---
name: add-token
description: Add a new token to the Monad token list end-to-end - scaffold mainnet/<SYMBOL>/data.json from the contract address, detect and verify the native bridge protocol on-chain, find a corroborated CoinGecko ID, fill cross-chain addresses, fetch a logo, and run validation. Use this skill whenever the user asks to add, list, or register a token (by address or symbol) in this repository, including requests like "add USDXY", "list token 0x...", or "create the data.json for this token".
---

# Add a token to the Monad token list

You are adding one token directory (`mainnet/<SYMBOL>/data.json` + logo) to this repo. CI will re-verify everything you write against the chain and CoinGecko, so the job is not just to fill fields — it is to fill them with **provable** values.

## Trust policy (read this first)

A spam token can copy a legitimate project's name, symbol, and logo perfectly. The only evidence that distinguishes the real token is **address-level**: on-chain back-references between contracts, registries controlled by the bridge operators, CoinGecko's own contract-address mapping, and the project's official documentation.

Therefore:
- Never source an address, CoinGecko ID, or cross-chain mapping from general web search results, aggregators, or third-party token lists.
- Every `extensions` field you write must carry evidence from the bundled scripts or official docs (and docs-sourced addresses must be re-verified on-chain).
- When you cannot prove something, **omit it and say so in your report**. Optional fields left empty are correct (`mainnet/hyAUSD` has no extensions at all); a plausible guess is not.
- But "cannot prove" has to mean you actually looked. The goal is to fill every field the evidence can support, not to stop at the first empty result. A bridged token in particular exists on other chains by definition, so an empty `crossChainAddresses` usually means the search was incomplete rather than that no counterparts exist — see step 4.

## Workflow

Work from the repository root. When `.env` exists (it normally does), prefix EVERY python command with `uv run --env-file=.env` — including `add_token.py` and `validate_tokens.py`, not just the skill's own scripts. The file carries `COINGECKO_API_KEY`, which removes CoinGecko's ~12s/request keyless throttle: with it a full `validate_tokens.py` run takes a couple of minutes, without it the ~100 CoinGecko lookups alone take 20+ minutes or die on HTTP 429. It also carries `MONAD_RPC_URL` and optional per-chain RPC. The two bundled skill scripts additionally self-load the repo `.env` as a safety net, but the repo's own scripts do NOT — for them the `--env-file=.env` prefix is the only way to get the key.

### 1. Scaffold

```bash
uv run --env-file=.env python scripts/add_token.py <address>
```

Creates `mainnet/<SYMBOL>/data.json` with on-chain `name`/`symbol`/`decimals` (chainId 143). If it fails with `FileExistsError`, the token already exists — stop and tell the user.

### 2. Detect the native bridge

```bash
uv run --env-file=.env python .claude/skills/add-token/scripts/probe_bridges.py <address>
```

This probes every protocol the validator accepts (CCTP, CCIP, M0 Portal, Wormhole, LayerZero OFT, Wormhole NTT, Hyperlane) using on-chain registries, the Hyperlane warp-route registry, and a mint/burn log-correlation scan. Read the JSON:

- `detected` (tier A): exactly one mint-class protocol verified — copy its `protocol` and `bridgeAddress` into `extensions.bridgeInfo` as-is.
- `ambiguous` non-empty: multiple protocols verified. Do NOT pick one; present both to the user with their evidence.
- `needsHumanConfirmation` non-empty: a candidate surfaced that no on-chain check can tie to this token (the shared multi-token NTT manager is the case that reaches here — its `token()` reverts, so a same-receipt event may belong to any token it carries). Never write it yourself; show the user the candidate and its evidence and let them confirm.
- `detected` null: consult the token's **official documentation only** (found via the CoinGecko coin homepage or the project's well-known domain) for Monad bridge/adapter addresses, then verify any candidate by running the probes' back-reference checks (see `references/bridge-protocols.md`). If nothing verifies, write **no bridgeInfo** and report that.

For protocol details, discovery sources, and the mint-class vs lock-class distinction, read `references/bridge-protocols.md`.

### 3. CoinGecko ID

```bash
uv run --env-file=.env python .claude/skills/add-token/scripts/coingecko_lookup.py <address> \
  --symbol <SYMBOL> --name "<NAME>" \
  --known-address 1=0x... --known-address 56=0x... \
  --official-domain <project.com>
```

Pass `--known-address` for every cross-chain address you have already established independently (bridge probe `crossChainHints`, official docs). Interpret `confidence`:
- `address-verified` → write `coinGeckoId`.
- `weak` (homepage + exact name/symbol only) → show the user; do not write silently unless they confirm.
- `none` → omit `coinGeckoId`. New tokens are often not on CoinGecko yet; that is fine and expected.

The Monad address itself may not be listed on CoinGecko even when the coin exists (listing lags new deployments) — that is exactly what the corroborated-search fallback is for.

### 4. crossChainAddresses

A bridged token is deployed somewhere else — that is what "bridged" means. So whenever step 2 detected a bridge, filling `crossChainAddresses` is the expected outcome, and an empty result is a signal you stopped searching too early. Omit the field only when the token is genuinely Monad-native (no bridge detected) or when you have worked the full supported-chain list below and could prove nothing on any of them.

Two failure modes make this go wrong, and the methodology below guards against both: giving up when the probe and CoinGecko hints come back empty, and finding one or two counterparts and stopping before checking the rest. Be exhaustive — the point is to fill every chain the evidence supports, not to fill the first one you find.

**Supported chains.** The authoritative list is the "Chain ID Reference" table in `CONTRIBUTING.md`. Read it rather than relying on memory — chains get added over time, and the validator rejects any chain ID outside that table. Walk the whole table chain by chain so coverage is complete. `scripts/utils/web3.py` exposes a working RPC for each supported chain through `get_web3_connection_for_chain(chain_id)`.

**Start with the registry.** Run the bundled enumerator, which reads the native bridge's own peer registry — the strongest evidence there is, because it comes from the same contract you verified in step 2:

```bash
uv run --env-file=.env python .claude/skills/add-token/scripts/find_cross_chain.py <token> [--bridge-address <step-2 bridgeAddress>]
```

Pass `--bridge-address` whenever step 2's `bridgeAddress` is not the token itself — a Wormhole NTT manager, or a LayerZero OFT adapter deployed separately from the token — because the peer table lives on that contract, not the token. The script covers every mint-class protocol's registry (LayerZero OFT `peers`, Wormhole NTT `getPeer`, Chainlink CCIP token-pool remote tokens; Hyperlane peers come from the probe's `crossChainHints`) and **verifies each candidate on its own chain**, printing per-chain `symbol`/`decimals` with any overrides. It does not guess counterparts from a shared address. It also follows a **hub hop**: a hub-and-spoke token enrolls only its hub, so the script reads the hub's peer table too. Read `references/bridge-protocols.md` for how each registry works and how to hand-map anything the script reports as `unmapped`.

**Never hand-roll the enumeration, and never hand-write a chain-mapping table.** This is the rule that a real incident produced, so treat it as hard:

- Every address you write into `crossChainAddresses` must come from this script's `verified` output. If you find yourself writing an inline `web3` script that walks `peers()` / `getPeer()` / a selector table to fill this field, stop — fix or extend `find_cross_chain.py` instead, then re-run it. An ad-hoc script is exactly where a wrong endpoint-id gets invented, and it carries none of the confirmations below.
- The eid/selector -> chain mapping is **never** written from memory. The script takes it from the LayerZero metadata API, and confirms every candidate against the remote endpoint's own `eid()` before recording it. eid `30370` is Plume and eid `30383` is Plasma; guessing that pair the other way round is what put four unverifiable Plasma addresses into a PR.
- Read the script's `rejected` list and report it. An entry there means a contract exists at that address on that chain and may even carry the right `symbol`, but it is not on the endpoint that enrolled it — a same-address deployment, not a proven counterpart. Never promote a `rejected` entry into `verified` by hand.
- A registry that returns nothing for a chain is a negative result, not an invitation to fall back on a same-address match. `mainnet/frxUSD` ships 8 chains, not 9, for exactly this reason.

The sources it draws on, strongest first — this is the order to trust them in:
1. **Registry / peer table.** Proves identity outright, and uniquely finds counterparts deployed at a *different* address (e.g. a canonical token that predates the bridge, reached through an adapter's `token()`). This is why the registry is the primary source.
2. **Official docs / the corroborated CoinGecko `platforms` map.** For any chain the registries miss — including a deterministic multi-chain deploy that no peer table enumerates (a hub-and-spoke token often enrolls only its hub as a peer yet is deployed identically on many chains). The same official source that gave you the logo or bridge address usually lists every deployment; pursue it actively. Verify any address it yields exactly as below before writing it.

**Never add a chain from a same-address guess.** An identical address on another chain can be an unrelated or malicious deployment, and a matching `symbol()` does not prove identity — a clone sets its symbol to anything it likes. So a shared address is written *only* when official docs or an appropriate registry name it for this token; the address matching Monad's is corroboration, never the reason. The script enforces this by never sweeping addresses on its own.

**Metadata legitimately differs across chains — record it, don't reject it.** USDC is the classic case: 6 decimals on Ethereum but often 18 on the bridged BNB side; symbols can carry chain-specific prefixes or suffixes (XAUt vs XAUt0). These differences are exactly what the per-chain `"symbol"`/`"decimals"` override fields exist to capture — the script emits them for you. What you are confirming is **identity**, not equality: a registry link or a docs listing proves it (so a renamed symbol there is fine). Never write an address the verify step could not confirm.

**Cross-check against official docs before you finish.** When the project publishes a contract-address page (found the same address-verified way as the logo homepage), diff your `crossChainAddresses` against it chain by chain. It is the cheapest catch for a chain you added that the registry never actually named, and for one you missed. A chain that is in your file but absent from both the registry output and the docs must come out.

Skip and report any chain where a candidate surfaced but could not be verified, any chain whose RPC was unreachable, and anything the script lists as `unmapped` that you did not resolve by hand. If, after all this, a bridged token still has zero provable counterparts, flag that explicitly — it is a surprising result worth surfacing, not a silent omission.

If this step establishes cross-chain addresses that step 3's CoinGecko lookup did not have, re-run the lookup with them as `--known-address` — an `address-verified` match can appear once the counterparts are supplied.

### 5. Logo

`validate_tokens.py` requires `logo.svg` or `logo.png` in the token directory: square, at least 200x200 (256x256 preferred). The same address-level trust policy applies here as everywhere else — a clone copies the logo perfectly, so the logo you write has to come from a source that keyed it to *this* address, not to a name match.

The best source is the token's **official app or an integration that lists it** (the project's own `app.<domain>`, or Morpho/Euler/Pendle when the token is a vault/collateral/underlying there). These front-ends look their icons up by contract address and serve them as SVGs, so with the exact address you get the right icon, sharp at any size. Do not stop at CoinGecko's PNG: it is a 250x250 raster and, until step 3 address-verified the coin, only as good as a name match. Reach for it only after the app route genuinely comes up empty.

Establish the project's official site from an address-verified source — the CoinGecko coin `homepage`, official docs, or a known integration's app — never from general search. Do not assume the app is at `app.<domain>`; it may be the domain root, a path, or a separate host. The bundled script digs for it: hand it the homepage and it mines the page and its JS bundles for the real app URL before mining that app for icons. Pass any address the app is likely to key on (the Monad address first; if that finds nothing, the cross-chain counterpart from step 4):

```bash
uv run --env-file=.env python .claude/skills/add-token/scripts/fetch_app_logo.py \
  --app-url https://<project-homepage-or-known-app> --address <monad-address> --symbol <SYMBOL> \
  --extra-address 1=<eth-address> --out mainnet/<SYMBOL>
```

It fetches the app and its JS/CSS bundles, extracts address- and symbol-matched icons (inline `data:image/svg+xml`, linked `.svg` assets, and symbol/address-keyed image URLs alike), skips share/favicon/brand/chain images and anything not roughly square, auto-resizes small square SVGs to 256, and prints a ranked `candidates` list with a `confidence` on each. Take the top `strong` candidate (`candidates[0].saved_path`); inspect first when only `weak` candidates exist or they disagree — an address can belong to a sibling token. Then save it (`candidates[0].ext` is `svg`/`png`/`webp`) and delete the other candidate files.

It also queries the integrations that list the token even when their app has no static icon: **Morpho** (`blue-api.morpho.org`, address-keyed), **Euler** (`token-images.euler.finance`, address-keyed), and the symbol-path apps **Curvance** and **Mento** (`app.<name>/tokens/<symbol>.svg`). These run automatically from the address and symbol — no app URL needed — so pass every cross-chain address from step 4 via `--extra-address CHAINID=0x...` (repeatable), since Morpho/Euler key by the address on each chain and the token may only be listed under its Ethereum deployment. The ranking prefers the token's own app over an integration's copy of the icon. Pass `--no-integrations` to skip them.

**Read `references/logo-sources.md`** for the full method: finding the app URL, the exact meaning of each `match`/`confidence` value, the integration sources, why the resize is safe, and the by-hand extraction fallback for when a minified bundle defeats the script. If every avenue — official app, integrations, official brand assets, and finally the address-verified CoinGecko image — comes up empty, leave the logo missing and flag it; validation failing until a human adds one is the intended behavior.

### 6. Format and validate

Write `data.json` with 2-space indent, sorted the way the other tokens are (field order: chainId, address, name, symbol, decimals, extensions; extensions: coinGeckoId, bridgeInfo, crossChainAddresses). CI requires `jq --indent 2` canonical formatting — check with:

```bash
jq --indent 2 . mainnet/<SYMBOL>/data.json | diff - mainnet/<SYMBOL>/data.json
```

Then run the validator scoped to the new token (keep the `--env-file=.env` prefix — without the CoinGecko key validation throttles heavily):

```bash
uv run --env-file=.env python scripts/validate_tokens.py <SYMBOL> --validate-cross-chain
```

This runs the exact checks CI runs, just limited to your token — the other tokens are unchanged and already pass. CI will still validate the full list on the PR.

Fix any error it reports. If an extensions value cannot be made to pass, remove it (and report why) rather than shipping a failing or fudged value. Never edit `tokenlist-mainnet.json` — it is regenerated by CI after merge.

### 7. Report

End with a summary the user can review at a glance:
- What was written to `data.json`, with the evidence for each extensions field (probe evidence lines, CoinGecko confidence, per-chain verification results).
- For a bridged token, cross-chain coverage: which supported chains you checked, which have a verified counterpart (with any `symbol`/`decimals` overrides), and which you skipped and why. This makes it easy to see the whole table was worked, not just the first hit.
- What was intentionally omitted and why (no verified bridge, no corroborated CoinGecko ID, unverifiable cross-chain address, missing logo).
- The validator's result.

If anything was ambiguous (multiple bridges, weak CoinGecko match), ask the user instead of deciding yourself.
