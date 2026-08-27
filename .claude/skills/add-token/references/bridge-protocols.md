# Bridge protocols

Reference for step 2 (detect the native bridge) and step 4 (cross-chain addresses). Reconstructed for the add-token skill from the protocol primitives in `scripts/utils/web3.py` and the accepted-protocol constants in `scripts/validate_tokens.py`.

## What the validator accepts

`bridgeInfo.protocol` must be exactly one of (`VALID_BRIDGE_PROTOCOLS` in `validate_tokens.py`):

- `Circle CCTP`
- `Chainlink CCIP`
- `M0 Portal`
- `Wormhole`
- `Wormhole NTT`
- `LayerZero OFT`
- `Hyperlane Warp Route`

For four of these the `bridgeAddress` is a **fixed shared contract** the validator checks against `EXPECTED_BRIDGE_ADDRESSES`, so you copy it verbatim:

- Circle CCTP -> `0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d`
- Chainlink CCIP -> `0x33566fE5976AAa420F3d5C64996641Fc3858CaDB`
- M0 Portal -> `0xD925C84b55E4e44a53749fF5F2a5A13F63D128fd`
- Wormhole -> `0x0B2719cdA2F10595369e6673ceA3Ee2EDFa13BA7`

For `LayerZero OFT`, `Hyperlane Warp Route`, and `Wormhole NTT` the `bridgeAddress` is **token-specific** - the OFT/adapter, the warp route, or the NTT manager - and the probe reports the exact address.

## Mint-class vs lock-class

A **mint-class** bridge mints/burns the token on Monad, so the Monad contract *is* (or is wired to) the bridge - this is what `bridgeInfo` records, and exactly one should verify. A **lock-class** bridge locks a canonical token elsewhere and the Monad side is a plain wrapper; when nothing mint-class verifies, write no `bridgeInfo`.

## How each is detected on-chain (`probe_bridges.py`)

Each probe is a read against the Monad contract using a primitive in `utils/web3.py`; a revert means "not this protocol".

- **Circle CCTP** - `burnLimitsPerMessage(token)` on the CCTP minter (`CCTP_TOKEN_MINTER_V2_ADDRESS`) returns a nonzero limit.
- **Chainlink CCIP** - `getTokenConfig(token).tokenPool` on the CCIP admin registry (`CCIP_TOKEN_ADMIN_REGISTRY_ADDRESS`) is a nonzero pool.
- **M0 Portal** - the token exposes `mToken()` (an M extension or the Portal's bridged $M).
- **Wormhole (wrapped)** - the token exposes `chainId()` (uint16), the wrapped-asset marker.
- **LayerZero OFT** - the token is its own OFT: `token()` returns itself, and `endpoint()` is the Monad LayerZero EndpointV2 (`0x6f475642a6e85809b1c36fa62763669b1b48dd5b`). A separately-deployed OFT adapter points `token()` at the canonical token instead; its own address is the `bridgeAddress`.
- **Hyperlane Warp Route** - the token exposes `wrappedToken()`.
- **Wormhole NTT** - the token's `minter()` is an NTT manager whose `token()` returns this token; the manager address is the `bridgeAddress`.

**The shared multi-token NTT manager** (`0x36878C6FCa7e0E8a88F90dc410CfBBcA5B695C95`) is the one case that reaches `needsHumanConfirmation`: its `token()` reverts because it carries several tokens, so no on-chain check can tie a same-receipt event to *this* token. Never write it yourself; show the user the candidate and let them confirm.

Outcomes: exactly one mint-class hit -> `detected` (tier A). Several -> `ambiguous` (present all; do not pick). None -> consult official docs, verify any candidate with the same back-reference checks, and otherwise write no `bridgeInfo`.

## Cross-chain enumeration (`find_cross_chain.py`)

Enumeration for every source below is gated on the supported counterpart chains (the `SUPPORTED_CHAINS` set, mirroring the "Chain ID Reference" table in `CONTRIBUTING.md`): a counterpart on any other chain is ignored, because the list has nowhere to record it. The per-protocol chain maps (`CCIP_SELECTOR_TO_CHAIN`, `WORMHOLE_ID_TO_CHAIN`, and the LayerZero eid index) list only those chains.

Every counterpart is drawn from an authoritative source that ties the address to *this* token - a bridge peer registry or a protocol directory. A shared address is never a source on its own: the same address on another chain can be an unrelated or malicious deployment, and a matching `symbol()` does not prove identity (a clone sets its symbol freely). The peer registry is the strongest source because it lives on the same contract you verified.

- **LayerZero OFT** - `peers(uint32 eid)` on the OFT (or the adapter given via `--bridge-address`, or the adapter the LayerZero registry names for a plain ERC20) returns a `bytes32` peer; its low 20 bytes are the counterpart address. Endpoint IDs map to chains via the LayerZero metadata index, e.g. eid `30101` -> Ethereum (`1`), `30184` -> Base (`8453`), `30110` -> Arbitrum (`42161`). The registry's `peggedTo` gives one more counterpart.
- **Chainlink CCIP** - the offchain CCIP directory (`https://docs.chain.link/api/ccip/v1/tokens?environment=mainnet`, keyed by symbol then chain id) lists every chain the token exists on with its address there, confirmed by matching the Monad (`143`) entry to this token. This is the primary source because the Monad pool's on-chain remote table (`getSupportedChains()` / `getRemoteToken(selector)`, selectors via `CCIP_SELECTOR_TO_CHAIN`) only holds the lanes that one pool enrolled; the on-chain read is the fallback when the directory is unreachable.
- **Wormhole NTT** - `getPeer(uint16 whChainId)` on the NTT manager returns the peer manager per Wormhole chain id (`WORMHOLE_ID_TO_CHAIN`), resolved to the remote token via its `token()`. Only a per-token manager (whose `token()` is this token) is enumerated: the shared multi-token manager (`0x36878C6FCa7e0E8a88F90dc410CfBBcA5B695C95`, e.g. the Mento family) reverts `token()` and its peers are other shared managers, so its counterparts cannot be resolved.
- **Hyperlane Warp Route** - the `hyperlane-registry` warp config for the token's symbol lists its underlying address per chain (`collateralAddressOrDenom`, or the router for a synthetic route), matched to this token by its Monad address. This handles XERC20 routes (e.g. ezETH), where the token is separate from the router and on-chain discovery from the token does not work. On the home chain the config points at the XERC20 lockbox rather than the canonical token, so that entry fails on-chain verification and is dropped.

A deterministic multi-chain deploy that no registry enumerates (a hub-and-spoke token often enrolls only its hub as a peer, yet is deployed identically on many chains) is **not** picked up automatically. Add such a chain only from official docs or an appropriate registry that names the address for this token - never from a same-address guess, even when the remote `symbol()` matches. Verify the docs/registry address on its own chain before writing it, exactly like any other candidate.

Every candidate is then re-verified on its own chain via `get_web3_connection_for_chain(chain_id)`, reading `symbol()`/`decimals()` and emitting per-chain overrides when they differ from Monad. A candidate that fails to verify (unreachable RPC, or a different token at that address) is skipped and logged, not written. Two counterparts that verify to different addresses on one chain land in `conflicts` for you to resolve against the "Chain ID Reference" table in `CONTRIBUTING.md`.

## Endpoint ids are not guessable

A LayerZero peer entry is `eid -> address`. The eid is LayerZero's own chain id and has no relationship to the EVM chain id, so the mapping has to be looked up, never recalled. Neighbouring eids belong to unrelated chains: 30367 is HyperEVM, 30370 is Plume, 30383 is Plasma, 30390 is Monad.

`find_cross_chain.py` takes the mapping from the LayerZero metadata API (`https://metadata.layerzero-api.com/v1/metadata`, which gives eid -> `nativeChainId` plus RPCs) and then confirms it per candidate: on the remote chain it reads the peer's `endpoint()` and that endpoint's `eid()`, and records the address only when that equals the eid the peer was enrolled under. `ENDPOINT_ID_TO_CHAIN_FALLBACK` is used only when the API is unreachable.

This matters because deterministic multi-chain deploys put the same address on many chains, including chains the hub never enrolled. On such a chain the contract answers `symbol()` correctly and may even name the hub in its own `peers()`, since `peers` is set by whoever deployed it. Neither fact proves the hub bridges there. The endpoint check is what separates the two, and anything it rejects is reported under `rejected` rather than written.

## Hub-and-spoke tokens

A token bridged through a hub enrols only the hub in its own peer table. Reading just the Monad token's `peers()` for such a token returns a single entry, usually on a chain outside the supported set, which looks like "no counterparts" but is not. The script follows that peer one hop, reading the hub contract's peer table over an RPC from the metadata index, and enumerates the spokes from there; those sources are tagged `LayerZero peer via <hub> hub`.

The hub's `token()` is worth reading as a sanity check: it names the canonical token the hub locks, which is what distinguishes sibling tokens whose hubs otherwise look alike.
