#!/usr/bin/env python3
"""Mine an official web app (or integration front-end) for a token's SVG logo.

DeFi front-ends (the project's own app, or an integration like Morpho, Euler,
Pendle that lists the token) ship per-token icons keyed by contract address, so
the exact address pulls the right icon, usually a crisp SVG rather than the
250x250 PNG CoinGecko serves.

The icon is either a linked build asset (e.g. `/assets/PRIME-CI4jqIYf.svg`,
often named after the symbol) or an inline `data:image/svg+xml` URI referenced
from the token's config object. This script fetches the page and its JS/CSS
bundles, extracts both forms, ranks candidates by how tightly they tie to this
token (symbol-named file, or an icon next to the address/symbol in a config
object), writes each to the output dir, and prints a JSON report.

It discovers, it does not judge: a match only means "this app serves this icon
for this address", trustworthy only when the app is the token's official app or
an integration reached from official docs, not a random site.

Usage:
    uv run --env-file=.env python .claude/skills/add-token/scripts/fetch_app_logo.py \
        --app-url https://app.strata.markets \
        --address 0x5ec32e3f2cc925296dc37edf3b2388868d5525c5 \
        --symbol jrUSDat \
        --out mainnet/jrUSDat

Pass --app-url more than once to sweep several front-ends in one run.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import re
import sys
import urllib.parse
from pathlib import Path

try:
    import requests
except ImportError:
    print(json.dumps({"error": "requests not installed; run via `uv run`"}))
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    Image = None

# A single bundle can be several MB (SPA vendor chunks); allow room but cap so a
# pathological asset does not hang the run.
MAX_BUNDLE_BYTES = 25 * 1024 * 1024
MAX_BUNDLES = 40
REQUEST_TIMEOUT = 30
MIN_LOGO_SIZE = 200
PREFERRED_LOGO_SIZE = 256

ASSET_LINK_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+\.(?:js|css))["']""", re.IGNORECASE)
DATA_URI_RE = re.compile(r"""data:image/svg\+xml[^"'`]*""")
IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
ICON_FIELD_RE = re.compile(rf"""(?:asset|icon|logo|image|img)\s*:\s*({IDENT})""")


def registrable(host: str) -> str:
    """Best-effort registrable domain so `foo.com` and `app.foo.com` match."""

    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def domain_of(url: str) -> str:
    """The registrable domain of a URL: the SSRF gate's allowed_domain."""

    return registrable(urllib.parse.urlparse(url).netloc)


def is_fetchable(url: str, allowed_domain: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
        and registrable(parsed.netloc) == allowed_domain
    )


def fetch(url: str, allowed_domain: str) -> bytes | None:
    # Only fetch http(s) URLs on the app's own registrable domain, re-checked
    # after every redirect, so text mined from a fetched page cannot aim the
    # fetcher at an internal or attacker-chosen host (SSRF).
    if not is_fetchable(url, allowed_domain):
        return None
    try:
        for _ in range(6):
            response = requests.get(
                url, timeout=REQUEST_TIMEOUT, stream=True, allow_redirects=False
            )
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()

                if not location:
                    return None

                url = urllib.parse.urljoin(url, location)
                if not is_fetchable(url, allowed_domain):
                    return None

                continue

            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > MAX_BUNDLE_BYTES:
                return None

            # Abort past the cap instead of buffering a pathological asset in full
            buffer = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                buffer.extend(chunk)

                if len(buffer) > MAX_BUNDLE_BYTES:
                    return None

            return bytes(buffer)
        return None
    except Exception:
        return None


def origin_of(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


def to_asset_url(raw: str, source: str) -> str:
    if raw.startswith("http"):
        return raw
    return urllib.parse.urljoin(origin_of(source) + "/", raw.lstrip("/"))


APP_HOST_KEYWORDS = ("app", "dapp", "portal", "earn", "trade", "stake", "yield", "market")
# Extra words that hint at an app only when they appear in a URL path.
APP_PATH_KEYWORDS = ("launch", "vault")

APP_HOST_RE = re.compile(rf"^({'|'.join(APP_HOST_KEYWORDS)})s?\.", re.IGNORECASE)
APP_HINT_RE = re.compile("|".join(APP_HOST_KEYWORDS + APP_PATH_KEYWORDS), re.IGNORECASE)
# Conventional subdomains tried only as a last resort, after URLs mined from the
# site's own code; a miss is a cheap failed request and MAX_APP_PAGES caps the total.
CONVENTIONAL_APP_HOSTS = APP_HOST_KEYWORDS
ABS_URL_RE = re.compile(r"""https?://[a-zA-Z0-9.\-]+(?:/[a-zA-Z0-9/_%.\-]*)?""")
MAX_APP_PAGES = 6


def assets_linked_by(page_text: str, page_url: str) -> list[str]:
    return [to_asset_url(match.group(1), page_url) for match in ASSET_LINK_RE.finditer(page_text)]


def fetch_page_and_assets(page_url: str, bundles: dict[str, str], allowed_domain: str) -> None:
    """Fetch a page and its linked JS/CSS into ``bundles`` (in place)."""

    if page_url in bundles or len(bundles) > MAX_BUNDLES:
        return

    content = fetch(page_url, allowed_domain)
    if content is None:
        return

    bundles[page_url] = content.decode("utf-8", "replace")

    for asset_url in dict.fromkeys(assets_linked_by(bundles[page_url], page_url)):
        if len(bundles) > MAX_BUNDLES:
            break

        if asset_url in bundles:
            continue

        asset = fetch(asset_url, allowed_domain)

        if asset is not None:
            bundles[asset_url] = asset.decode("utf-8", "replace")


def discover_app_pages(seed_bundles: dict[str, str], seed_url: str) -> list[str]:
    """Find where the app actually lives, given the seed page and its bundles.

    The app is not always `app.<domain>`; it may be the site root, a path like
    `/earn`, or a separate host, and a marketing homepage is often a JS shell
    with no static links. So mine the HTML and JS for same-domain app URLs (an
    app-ish host or path) and only guess conventional hosts if none turn up.
    """

    seed_host = urllib.parse.urlparse(seed_url).netloc
    home_domain = registrable(seed_host)
    scheme = urllib.parse.urlparse(seed_url).scheme or "https"
    # Ordered and de-duplicated, and lets us stop scanning multi-MB bundles early
    discovered: dict[str, None] = {}

    for text in seed_bundles.values():
        if len(discovered) >= MAX_APP_PAGES:
            break

        for match in ABS_URL_RE.finditer(text):
            parsed = urllib.parse.urlparse(match.group(0))
            host = parsed.netloc
            if not host or registrable(host) != home_domain:
                continue

            if APP_HOST_RE.match(host):
                discovered[f"{parsed.scheme}://{host}/"] = None
            elif host == seed_host and parsed.path.strip("/"):
                first_segment = parsed.path.strip("/").split("/")[0]
                if APP_HINT_RE.search(first_segment):
                    discovered[f"{parsed.scheme}://{host}/{first_segment}"] = None

            if len(discovered) >= MAX_APP_PAGES:
                break

    # Conventional host guesses are a blind fallback, so only reach for them when
    # the site's own code named no app URL. Otherwise we would probe portal./earn.
    # /... on every run even after already finding the real app in the bundles.
    if not discovered:
        return [f"{scheme}://{host}.{home_domain}/" for host in CONVENTIONAL_APP_HOSTS][
            :MAX_APP_PAGES
        ]

    return list(discovered)[:MAX_APP_PAGES]


def collect_bundles(app_url: str, discover: bool = True) -> dict[str, str]:
    """Return {url: text} for the page, the app it points to, and their JS/CSS."""

    bundles: dict[str, str] = {}
    allowed_domain = domain_of(app_url)

    fetch_page_and_assets(app_url, bundles, allowed_domain)

    if not bundles:
        return bundles

    if discover:
        for page in discover_app_pages(bundles, app_url):
            fetch_page_and_assets(page, bundles, allowed_domain)

    return bundles


def decode_data_uri(uri: str) -> str | None:
    if "," not in uri:
        return None

    header, data = uri.split(",", 1)

    try:
        if "base64" in header:
            return base64.b64decode(data).decode("utf-8", "replace")
        return urllib.parse.unquote(data)
    except Exception:
        return None


def svg_dimensions(svg_text: str) -> dict:
    def attr(name: str) -> str | None:
        match = re.search(rf"""\b{name}\s*=\s*["']([^"']+)["']""", svg_text[:600])
        return match.group(1) if match else None

    def to_int(value: str | None) -> int | None:
        if not value:
            return None
        cleaned = re.sub(r"[^0-9.]", "", value)
        if not cleaned:
            return None
        return int(float(cleaned))

    viewbox = attr("viewBox")
    width = to_int(attr("width"))
    height = to_int(attr("height"))

    square_via_viewbox = False
    if viewbox:
        nums = [float(part) for part in re.findall(r"-?\d+(?:\.\d+)?", viewbox)]
        if len(nums) == 4:
            square_via_viewbox = abs(nums[2] - nums[3]) < 1e-6

    return {
        "width": width,
        "height": height,
        "viewBox": viewbox,
        "square_via_viewbox": square_via_viewbox,
    }


def sniff_ext(content: bytes) -> str | None:
    """Identify an image by magic bytes; content-type headers lie (Euler serves
    WebP under an image/png type)."""

    head = content[:512].lstrip()
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"

    lowered = head[:400].lower()

    if b"<svg" in lowered and b"<html" not in lowered:
        return "svg"

    return None


def raster_dimensions(content: bytes) -> dict:
    width = height = None
    if Image is not None:
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
        except Exception:
            width = height = None

    return {"width": width, "height": height, "viewBox": None, "square_via_viewbox": False}


def normalize_svg_size(svg_text: str, dims: dict) -> tuple[str, str | None]:
    """Bump a small square SVG's declared width/height up to a usable size.

    The validator reads the width/height attributes, not the viewBox, so a 40x40
    declaration is rejected even though the icon scales to any size. Only those
    two attributes change; the viewBox and the art stay identical.
    """

    width = dims.get("width")
    height = dims.get("height")

    needs_bump = dims.get("square_via_viewbox") and (
        width is None or height is None or width < MIN_LOGO_SIZE or height < MIN_LOGO_SIZE
    )
    if not needs_bump:
        return svg_text, None

    note = f"{width}x{height}" if width and height else "unset"

    if width and height:
        new_text, count = re.subn(
            r"""(\bwidth\s*=\s*["'])[^"']+(["'])""",
            rf"\g<1>{PREFERRED_LOGO_SIZE}\g<2>",
            svg_text,
            count=1,
        )
        new_text, count2 = re.subn(
            r"""(\bheight\s*=\s*["'])[^"']+(["'])""",
            rf"\g<1>{PREFERRED_LOGO_SIZE}\g<2>",
            new_text,
            count=1,
        )
        if count and count2:
            return (
                new_text,
                f"resized width/height {note} -> {PREFERRED_LOGO_SIZE} (square viewBox preserved)",
            )

    new_text, count = re.subn(
        r"<svg\b",
        f'<svg width="{PREFERRED_LOGO_SIZE}" height="{PREFERRED_LOGO_SIZE}"',
        svg_text,
        count=1,
    )
    if count:
        return new_text, f"added width/height={PREFERRED_LOGO_SIZE} (square viewBox preserved)"

    return svg_text, None


def find_config_asset_idents(bundle: str, address: str, symbol: str | None) -> list[dict]:
    """Locate the token's config object by address (preferred) or symbol and
    return the identifier its ``asset``/``icon``/``logo`` field points at."""

    findings: list[dict] = []
    anchors: list[tuple[str, int]] = [
        ("address", match.start())
        for match in re.finditer(re.escape(address), bundle, re.IGNORECASE)
    ]

    if symbol:
        symbol_re = rf"""symbol\s*:\s*["']{re.escape(symbol)}["']"""
        anchors += [("symbol", m.start()) for m in re.finditer(symbol_re, bundle, re.IGNORECASE)]

    seen: set[tuple[str, str]] = set()

    for how, position in anchors:
        window_start = max(0, position - 900)
        window = bundle[window_start : position + 300]
        # nearest icon ref to the anchor is the enclosing token's own; a wider
        # window would sweep in adjacent tokens' icons
        nearest_ident = None
        nearest_distance = None

        for match in ICON_FIELD_RE.finditer(window):
            absolute = window_start + match.start()
            distance = abs(absolute - position)

            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_ident = match.group(1)

        if nearest_ident and (how, nearest_ident) not in seen:
            seen.add((how, nearest_ident))
            findings.append({"how": how, "ident": nearest_ident})

    return findings


def resolve_ident(bundle: str, ident: str) -> dict | None:
    """Resolve `ident="data:..."` or `ident="/assets/x.svg"` in a bundle.

    Captures up to the matching opening quote, not the first quote of either
    kind: an inline data-URI SVG uses the opposite quote internally
    (`She="data:image/svg+xml,<svg width='40'...>"`), which a naive `[^"']+`
    would truncate at that inner quote.
    """

    match = re.search(rf"""\b{re.escape(ident)}\s*=\s*(["'])((?:(?!\1).)*)\1""", bundle)
    if not match:
        return None

    value = match.group(2)

    if value.startswith("data:image/svg+xml"):
        return {"kind": "data-uri", "value": value}
    if value.endswith(".svg"):
        return {"kind": "asset-path", "value": value}

    return None


class CandidateCollector:
    """Collects de-duplicated logo candidates, writing each to the output dir.

    An identical image seen via another signal is merged onto the existing
    candidate instead of written twice, so a candidate carries every signal
    that agreed on it.
    """

    def __init__(self, out_dir: Path, resize: bool) -> None:
        self.out_dir = out_dir
        self.resize = resize
        self.candidates: list[dict] = []
        self._seen: set[str] = set()
        self._index = 0

    @property
    def count(self) -> int:
        return self._index

    def add(
        self, content: bytes, ext: str, signal: str, source: str, ident: str | None = None
    ) -> None:
        is_svg = ext == "svg"
        svg_text = content.decode("utf-8", "replace") if is_svg else None
        fingerprint = re.sub(r"\s+", "", svg_text) if is_svg else hashlib.sha1(content).hexdigest()

        if fingerprint in self._seen:
            for existing in self.candidates:
                if existing["_fingerprint"] == fingerprint and signal not in existing["signals"]:
                    existing["signals"].append(signal)
            return

        self._seen.add(fingerprint)

        resize_note = None

        if is_svg:
            dims = svg_dimensions(svg_text)
            if self.resize:
                svg_text, resize_note = normalize_svg_size(svg_text, dims)
                if resize_note:
                    # Normalize just set both; no need to re-parse to read them back
                    dims["width"] = dims["height"] = PREFERRED_LOGO_SIZE
        else:
            dims = raster_dimensions(content)

        path = self.out_dir / f"candidate-{self._index}-{signal}.{ext}"
        self._index += 1

        if is_svg:
            path.write_text(svg_text, encoding="utf-8")
        else:
            path.write_bytes(content)

        self.candidates.append(
            {
                "signals": [signal],
                "source_bundle": source,
                "ident": ident,
                "saved_path": str(path),
                "ext": ext,
                "dimensions": dims,
                "resize_note": resize_note,
                "square": bool(dims.get("square_via_viewbox"))
                or (dims.get("width") and dims["width"] == dims.get("height")),
                "_fingerprint": fingerprint,
            }
        )


def harvest_symbol_filename(
    bundles: dict[str, str], symbol: str, collector: CandidateCollector
) -> None:
    """Asset files whose name starts with the symbol: the cleanest signal."""

    symbol_svg_re = re.compile(
        rf"""["'(]([^"'()\s]*/{re.escape(symbol)}[-_.][^"'()\s]*\.svg)""", re.IGNORECASE
    )
    seen: set[str] = set()

    for source, bundle in bundles.items():
        allowed_domain = domain_of(source)

        for match in symbol_svg_re.finditer(bundle):
            asset_url = to_asset_url(match.group(1), source)
            if asset_url in seen:
                continue

            seen.add(asset_url)
            content = fetch(asset_url, allowed_domain)

            if content:
                collector.add(content, "svg", "symbol-filename", asset_url)


def harvest_config_anchored(
    bundles: dict[str, str], address: str, symbol: str | None, collector: CandidateCollector
) -> None:
    """Icons referenced next to the address or symbol in a token config object."""
    for source, bundle in bundles.items():
        allowed_domain = domain_of(source)

        for finding in find_config_asset_idents(bundle, address, symbol):
            resolved = resolve_ident(bundle, finding["ident"])
            if not resolved:
                continue

            label = f"{finding['how']}-config"
            if resolved["kind"] == "data-uri":
                svg_text = decode_data_uri(resolved["value"])
                if svg_text:
                    collector.add(svg_text.encode("utf-8"), "svg", label, source, finding["ident"])
                continue

            asset_url = to_asset_url(resolved["value"], source)
            content = fetch(asset_url, allowed_domain)

            if content:
                collector.add(content, "svg", label, asset_url, finding["ident"])


IMG_URL_RE = re.compile(
    r"""https?://[^\s"'()<>]+?\.(?:svg|png|webp|jpe?g)(?=["'\s()<>?]|$)""", re.IGNORECASE
)
MAX_URL_ICONS = 8
SYMBOL_TEXT_WINDOW = 500
# Path words marking a non-token image: a share/preview banner, a favicon, the
# app's own brand logo, or a blockchain/network icon. None is the token's mark.
# (Legit dirs like assets/images/icons/tokens are deliberately absent.)
NON_TOKEN_URL_WORDS = {
    "og",
    "ogimage",
    "opengraph",
    "social",
    "preview",
    "banner",
    "twitter",
    "share",
    "cover",
    "hero",
    "splash",
    "background",
    "screenshot",
    "mockup",
    "promo",
    "poster",
    "placeholder",
    "avatar",
    "favicon",
    "webclip",
    "apple",
    "touch",
    "manifest",
    "logo",
    "logos",
    "logomark",
    "wordmark",
    "brand",
    "branding",
    "network",
    "networks",
    "chain",
    "chains",
    "illustration",
    "graphic",
    "solana",
    "ethereum",
    "avalanche",
    "algorand",
    "arbitrum",
    "optimism",
    "polygon",
    "binance",
    "gnosis",
    "fantom",
    "tron",
    "cardano",
    "cosmos",
    "aptos",
    "linea",
    "scroll",
    "mantle",
    "blast",
    "celo",
    "ripple",
}


def is_non_token_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    # Alphabetic runs, so a glued name like "Favicon32x32" still yields "favicon".
    return bool(NON_TOKEN_URL_WORDS.intersection(re.findall(r"[a-z]+", path)))


def looks_like_logo(content: bytes, ext: str) -> bool:
    """A token logo is roughly square and not tiny; reject wide banners (~1.9:1)
    and favicon-sized sprites."""

    dims = (
        svg_dimensions(content.decode("utf-8", "replace"))
        if ext == "svg"
        else raster_dimensions(content)
    )
    width, height = dims.get("width"), dims.get("height")

    if width and height:
        low, high = sorted((width, height))
        return low > 0 and high / low <= 1.3 and high >= 100

    return True  # unknown pixel size (e.g. an SVG sized only by viewBox)


def harvest_url_icons(bundles, addresses_by_chain, symbol, collector) -> None:
    """Icons an app references by URL (often a raster on a CDN), keyed to this
    token by the symbol in the filename, by sitting next to the address, or by
    the symbol appearing as text near the URL (a card that renders the icon
    beside the token name, where the filename is an internal ticker).

    Unlike bundle SVG assets, these are fetched even cross-domain: the app that
    embeds them was reached from a vetted official source, and the match is
    constrained to a symbol/address-keyed image URL, so it is not the arbitrary
    host-following the same-domain gate exists to stop. `fetch` still enforces
    the scheme, the size cap, and no off-host redirects.

    Share/preview banners, favicons and brand logos are skipped by URL, and
    anything not roughly square is dropped; the symbol-nearby match especially
    would otherwise pull in an OG image sitting near the token name.
    """

    # Match the symbol as a bounded token, not a substring, so "EUL" does not
    # match "euler-symbol" and "USD" does not match "usdc".
    filename_re = nearby_re = None
    if symbol and len(symbol) >= 3:
        filename_re = re.compile(rf"(?<![a-z0-9]){re.escape(symbol.lower())}(?![a-z0-9])")

    if symbol and len(symbol) >= 4:
        nearby_re = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", re.IGNORECASE
        )

    addr_set = {address.lower() for _chain, address in addresses_by_chain}
    seen: set[str] = set()
    fetched = 0

    for bundle in bundles.values():
        symbol_positions = [m.start() for m in nearby_re.finditer(bundle)] if nearby_re else []

        for match in IMG_URL_RE.finditer(bundle):
            url = match.group(0)
            if url in seen or fetched >= MAX_URL_ICONS or is_non_token_url(url):
                continue

            filename = url.rsplit("/", 1)[-1].split("?")[0].lower()
            if filename_re and filename_re.search(filename):
                signal = "symbol-url"
            elif any(
                addr in bundle[max(0, match.start() - 240) : match.start()].lower()
                for addr in addr_set
            ):
                signal = "address-url"
            elif (
                symbol_positions
                and min(abs(match.start() - p) for p in symbol_positions) <= SYMBOL_TEXT_WINDOW
            ):
                signal = "symbol-nearby"
            else:
                continue

            seen.add(url)
            fetched += 1
            content = fetch(url, domain_of(url))

            if not content:
                continue

            ext = sniff_ext(content)
            if ext and looks_like_logo(content, ext):
                collector.add(content, ext, signal, url)


def harvest_all_inline(bundles: dict[str, str], collector: CandidateCollector) -> None:
    """Last resort: surface every inline SVG so a run never comes back empty-handed."""

    for source, bundle in bundles.items():
        for match in DATA_URI_RE.finditer(bundle):
            svg_text = decode_data_uri(match.group(0))
            if svg_text and len(svg_text) < 20000:
                collector.add(svg_text.encode("utf-8"), "svg", "unmatched", source)
            if collector.count >= 40:
                return


EULER_DEFAULT_URL = (
    "https://token-images.euler.finance/1/0x0000000000000000000000000000000000000000"
)


def _api(url: str, allowed_domain: str, payload: dict | None = None):
    """GET, or POST when `payload` is given; JSON on 200, else None. Domain-gated."""

    if not is_fetchable(url, allowed_domain):
        return None
    try:
        if payload is None:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=False)
        else:
            response = requests.post(
                url, json=payload, timeout=REQUEST_TIMEOUT, allow_redirects=False
            )
        return response.json() if response.status_code == 200 else None
    except Exception:
        return None


# Apps that serve an icon per token at a predictable symbol-slug path. Each
# entry is (name, url-template with {slug}); a wrong slug just 404s to HTML and
# is discarded. Casing varies per app, so every slug variant below is tried.
SYMBOL_PATH_APPS = (
    ("curvance", "https://app.curvance.com/tokens/{slug}.svg"),
    ("mento", "https://app.mento.org/tokens/{slug}.svg"),
)


def _symbol_slugs(symbol: str | None) -> list[str]:
    if not symbol:
        return []

    slugs = [symbol, symbol.lower(), re.sub(r"[^a-z0-9]", "", symbol.lower())]
    return list(dict.fromkeys(s for s in slugs if s))


class Integrations:
    """Address/symbol-keyed logo lookups on trusted integration hosts.

    Morpho, Euler and the symbol-path apps load their token lists at runtime, so
    their icons are never in a static bundle, but each exposes an address- or
    symbol-keyed source. These hosts are hardcoded and vetted, so their fetches
    gate on the integration's own domain rather than the app being mined. Maps
    are built once (one query per protocol/chain) and reused across every token.
    """

    def __init__(self, chain_ids) -> None:
        chains = sorted({int(c) for c in chain_ids})
        self.morpho = self._build_morpho(chains)
        self.euler = {chain: self._build_euler(chain) for chain in chains}
        default = fetch(EULER_DEFAULT_URL, "euler.finance")
        self.euler_default = hashlib.sha1(default).hexdigest() if default else None

    @staticmethod
    def _build_morpho(chains) -> dict[str, str]:
        # One query per chain: Morpho errors the whole request if any chainId in
        # a chainId_in list is a network it does not index, so mixing in an
        # unsupported chain would otherwise zero the entire map.
        mapping: dict[str, str] = {}

        for chain in chains:
            query = {
                "query": "{assets(first:1000,where:{chainId_in:["
                + str(chain)
                + "]}){items{address logoURI}}}"
            }
            data = _api("https://blue-api.morpho.org/graphql", "morpho.org", query)

            try:
                items = data["data"]["assets"]["items"]
            except (TypeError, KeyError):
                continue

            for item in items:
                if item.get("logoURI"):
                    mapping[item["address"].lower()] = item["logoURI"]

        return mapping

    @staticmethod
    def _build_euler(chain_id) -> set[str]:
        data = _api(
            f"https://app.euler.finance/api/public/metadata?chainId={chain_id}", "euler.finance"
        )
        members: set[str] = set()

        if isinstance(data, dict):
            for entry in data.values():
                asset = entry.get("asset") if isinstance(entry, dict) else None
                if isinstance(asset, dict) and asset.get("address"):
                    members.add(asset["address"].lower())

        return members

    def candidates_for(self, addresses_by_chain, symbol):
        """Yield (signal, url, allowed_domain) for every integration listing this token."""
        for _chain, address in addresses_by_chain:
            uri = self.morpho.get(address.lower())
            if uri:
                yield ("morpho-address", uri, "morpho.org")

        for chain_id, address in addresses_by_chain:
            if address.lower() in self.euler.get(int(chain_id), set()):
                yield (
                    "euler-address",
                    f"https://token-images.euler.finance/{chain_id}/{address}",
                    "euler.finance",
                )

        for name, template in SYMBOL_PATH_APPS:
            domain = domain_of(template)

            for slug in _symbol_slugs(symbol):
                yield (f"{name}-symbol", template.format(slug=slug), domain)


def harvest_integrations(integrations, addresses_by_chain, symbol, collector) -> None:
    """Fetch address/symbol-keyed logos from trusted integrations into the collector."""

    for signal, url, domain in integrations.candidates_for(addresses_by_chain, symbol):
        content = fetch(url, domain)

        if not content:
            continue

        ext = sniff_ext(content)
        if not ext:
            continue

        if (
            integrations.euler_default
            and hashlib.sha1(content).hexdigest() == integrations.euler_default
        ):
            continue  # Euler's generic fallback image, not this token's logo

        collector.add(content, ext, signal, url)


# Each signal's rank facts in one place: (priority, is_strong, is_app_origin).
# Lower priority ranks first, ordered by evidence quality. A candidate keyed to
# the contract address (the app's own address-anchored icon, then a trusted
# integration's per-address icon) is provably this token, so it outranks any
# match made by symbol name or nearby text. A config-anchored guess stays weak
# until a second signal agrees.
SIGNAL_FACTS = {
    "address-url": (0, True, True),
    "morpho-address": (1, True, False),
    "euler-address": (1, True, False),
    "symbol-filename": (2, True, True),
    "symbol-url": (3, True, True),
    "symbol-nearby": (5, True, True),
    "address-config": (4, False, True),
    "symbol-config": (6, False, True),
    "unmatched": (9, False, False),
    **{f"{name}-symbol": (3, True, False) for name, _template in SYMBOL_PATH_APPS},
}
STRONG_SIGNALS = {name for name, (_priority, is_strong, _app) in SIGNAL_FACTS.items() if is_strong}
APP_SIGNALS = {name for name, (_priority, _strong, is_app) in SIGNAL_FACTS.items() if is_app}
SIGNAL_PRIORITY = {name: facts[0] for name, facts in SIGNAL_FACTS.items()}


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Order best-first and tag confidence.

    Two agreeing signals (address and symbol both pointing at one icon) are far
    safer than an address-only hit, because a token's config can reference a
    sibling token's address (paired tranches, collateral), so address-only
    occasionally resolves to the wrong icon.
    """

    for candidate in candidates:
        candidate.pop("_fingerprint", None)
        signals = candidate["signals"]
        candidate["match"] = "+".join(signals)
        candidate["confidence"] = (
            "strong" if (STRONG_SIGNALS.intersection(signals) or len(signals) >= 2) else "weak"
        )

    # Order: strong before weak; then by evidence quality (address-keyed before
    # symbol-keyed, per SIGNAL_PRIORITY); prefer a vector logo over a raster;
    # then more agreeing signals; the token's own app breaks a final tie over an
    # integration's copy of the same icon.
    candidates.sort(
        key=lambda item: (
            item["confidence"] != "strong",
            min(SIGNAL_PRIORITY.get(signal, 7) for signal in item["signals"]),
            item["ext"] != "svg",
            -len(item["signals"]),
            not APP_SIGNALS.intersection(item["signals"]),
        )
    )
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Mine an official web app for a token's SVG logo.")
    parser.add_argument("--app-url", action="append", help="App root URL to mine; repeatable.")
    parser.add_argument(
        "--address", required=True, help="Token contract address on Monad (chain 143)."
    )
    parser.add_argument(
        "--extra-address",
        action="append",
        default=[],
        metavar="CHAINID=0xADDR",
        help="A cross-chain address for integration lookups, e.g. 1=0x...; repeatable.",
    )
    parser.add_argument("--symbol", default=None, help="Token symbol, used as a secondary matcher.")
    parser.add_argument("--out", required=True, help="Directory to write candidate logos into.")
    parser.add_argument(
        "--no-resize", action="store_true", help="Do not bump small square SVGs to 256px."
    )
    parser.add_argument(
        "--no-integrations",
        action="store_true",
        help="Skip the Morpho/Euler/Curvance/Mento address- and symbol-keyed logo lookups.",
    )
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    address = args.address
    symbol = args.symbol

    addresses_by_chain = [(143, address)]

    for item in args.extra_address:
        chain, _, addr = item.partition("=")
        if chain.strip().isdigit() and addr:
            addresses_by_chain.append((int(chain), addr.strip()))

    report: dict = {
        "address": address,
        "symbol": symbol,
        "app_urls": args.app_url or [],
        "bundles_scanned": [],
        "candidates": [],
        "notes": [],
    }

    bundles: dict[str, str] = {}

    for app_url in args.app_url or []:
        bundles.update(collect_bundles(app_url))

    report["bundles_scanned"] = list(bundles.keys())

    if args.app_url and not bundles:
        report["notes"].append("No bundles fetched; check the app URL is reachable and correct.")

    collector = CandidateCollector(out_dir, resize=not args.no_resize)
    if bundles:
        if symbol:
            harvest_symbol_filename(bundles, symbol, collector)
        harvest_config_anchored(bundles, address, symbol, collector)
        harvest_url_icons(bundles, addresses_by_chain, symbol, collector)

    if not args.no_integrations:
        integrations = Integrations(chain for chain, _ in addresses_by_chain)
        harvest_integrations(integrations, addresses_by_chain, symbol, collector)

    if not collector.candidates and bundles:
        report["notes"].append(
            "No address- or symbol-anchored icon found. Dumping all inline SVGs as unmatched "
            "candidates; inspect them and re-check the app URL and that it lists this token."
        )
        harvest_all_inline(bundles, collector)

    report["candidates"] = rank_candidates(collector.candidates)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
