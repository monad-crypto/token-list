"""CoinGecko API utilities for verifying token IDs.

This module provides a small client that checks whether a given coinGeckoId
exists on CoinGecko. It supports:

- COINGECKO_API_KEY: Pro-tier key (routed to the Pro endpoint, no throttling).
- COINGECKO_DEMO_API_KEY: Demo-tier key (routed to the public endpoint with
  the demo header; still throttled).
- No key: throttles calls to stay within the free public-tier rate limit.

Transient failures (429, 5xx, network errors) are retried with exponential
backoff. Authentication failures (401/403) and unknown ids (404) surface as
errors. A 200 response whose returned id differs from the requested id is
treated as a warning, since CoinGecko sometimes redirects an alias slug to
its canonical form and a server-side rename should not break unrelated PRs.
"""

import os
import re
import time
import urllib.parse

import requests
from requests.exceptions import RequestException

COINGECKO_FREE_API_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_API_URL = "https://pro-api.coingecko.com/api/v3"
COINGECKO_REQUEST_TIMEOUT = 15.0
COINGECKO_FREE_DELAY_SECONDS = 12.0
COINGECKO_MAX_ATTEMPTS = 4
COINGECKO_RETRY_DELAY_SECONDS = 2.0
COINGECKO_RETRY_BACKOFF = 2.0
COINGECKO_429_FALLBACK_SECONDS = 60.0
COINGECKO_429_MAX_SLEEP_SECONDS = 120.0

_COINGECKO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _parse_retry_after(header_value: str | None, default: float) -> float:
    """Parse a Retry-After header value (seconds form) into a float.

    Falls back to ``default`` for missing, malformed, or HTTP-date-form values.
    """
    if not header_value:
        return default
    try:
        return max(0.0, float(header_value.strip()))
    except ValueError:
        return default


class CoinGeckoValidator:
    """Validates coinGeckoId values against the CoinGecko API."""

    def __init__(
        self,
        api_key: str | None = None,
        demo_api_key: str | None = None,
    ) -> None:
        raw_pro = api_key if api_key is not None else os.environ.get("COINGECKO_API_KEY", "")
        raw_demo = (
            demo_api_key
            if demo_api_key is not None
            else os.environ.get("COINGECKO_DEMO_API_KEY", "")
        )
        self.api_key = raw_pro.strip()
        self.demo_api_key = raw_demo.strip()
        self.is_pro = bool(self.api_key)
        self.has_key = self.is_pro or bool(self.demo_api_key)
        self.base_url = COINGECKO_PRO_API_URL if self.is_pro else COINGECKO_FREE_API_URL
        self._last_call_time: float = 0.0

    def _wait_if_needed(self) -> None:
        # Pro tier has a high rate limit; demo and anonymous tiers must be throttled.
        if self.is_pro:
            return
        elapsed = time.monotonic() - self._last_call_time
        remaining = COINGECKO_FREE_DELAY_SECONDS - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _build_headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        if self.is_pro:
            headers["x-cg-pro-api-key"] = self.api_key
        elif self.demo_api_key:
            headers["x-cg-demo-api-key"] = self.demo_api_key
        return headers

    @staticmethod
    def _check_canonical_id(data: dict | list | None, coin_gecko_id: str) -> list[str]:
        returned_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(returned_id, str):
            return [
                f"CoinGecko response for '{coin_gecko_id}' did not include "
                "an 'id' field; unable to verify canonical id"
            ]
        if returned_id != coin_gecko_id:
            return [
                f"coinGeckoId '{coin_gecko_id}' resolves to canonical id "
                f"'{returned_id}' on CoinGecko; consider updating data.json"
            ]
        return []

    def validate(self, coin_gecko_id: str) -> tuple[list[str], list[str]]:
        """Validate that a coinGeckoId exists on CoinGecko.

        Args:
            coin_gecko_id: The CoinGecko ID to validate.

        Returns:
            tuple[list[str], list[str]]: (errors, warnings).
        """
        if not coin_gecko_id or not _COINGECKO_ID_PATTERN.match(coin_gecko_id):
            return [
                f"Invalid coinGeckoId format '{coin_gecko_id}': "
                "must be a non-empty lowercase id (letters, digits, '.', '_', '-')"
            ], []

        encoded_id = urllib.parse.quote(coin_gecko_id, safe="")
        url = f"{self.base_url}/coins/{encoded_id}"
        params = {
            "localization": "false",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
        }
        headers = self._build_headers()

        last_transient_msg: str | None = None
        delay = COINGECKO_RETRY_DELAY_SECONDS
        for attempt in range(COINGECKO_MAX_ATTEMPTS):
            result, last_transient_msg, delay = self._attempt(
                url, params, headers, coin_gecko_id, attempt, delay, last_transient_msg
            )
            if result is not None:
                return result

        return [], [
            f"Failed to validate coinGeckoId '{coin_gecko_id}' after "
            f"{COINGECKO_MAX_ATTEMPTS} attempts ({last_transient_msg})"
        ]

    def _attempt(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        coin_gecko_id: str,
        attempt: int,
        delay: float,
        last_msg: str | None,
    ) -> tuple[tuple[list[str], list[str]] | None, str | None, float]:
        """Run one validate() attempt.

        Returns a tuple (terminal_result, last_transient_msg, next_delay). If
        terminal_result is None the caller should retry; otherwise it is the
        (errors, warnings) to return.
        """
        self._wait_if_needed()
        try:
            response = requests.get(
                url, params=params, headers=headers, timeout=COINGECKO_REQUEST_TIMEOUT
            )
        except RequestException as e:
            self._last_call_time = time.monotonic()
            return None, f"network error: {e}", self._sleep_backoff(attempt, delay)

        self._last_call_time = time.monotonic()
        status = response.status_code

        if status == 200:
            try:
                data = response.json()
            except ValueError as e:
                return None, f"invalid JSON: {e}", self._sleep_backoff(attempt, delay)
            return ([], self._check_canonical_id(data, coin_gecko_id)), last_msg, delay

        if status == 404:
            return (
                (
                    [f"Invalid coinGeckoId '{coin_gecko_id}': not found on CoinGecko"],
                    [],
                ),
                last_msg,
                delay,
            )

        if status in (401, 403):
            return (
                (
                    [
                        f"CoinGecko authentication failed (HTTP {status}) while validating "
                        f"'{coin_gecko_id}'. Check COINGECKO_API_KEY / COINGECKO_DEMO_API_KEY."
                    ],
                    [],
                ),
                last_msg,
                delay,
            )

        if status == 429:
            self._sleep_for_429(attempt, response)
            return None, "HTTP 429", delay

        if 500 <= status < 600:
            return None, f"HTTP {status}", self._sleep_backoff(attempt, delay)

        return (
            (
                [f"CoinGecko returned unexpected status {status} for '{coin_gecko_id}'"],
                [],
            ),
            last_msg,
            delay,
        )

    @staticmethod
    def _sleep_backoff(attempt: int, delay: float) -> float:
        if attempt < COINGECKO_MAX_ATTEMPTS - 1:
            time.sleep(delay)
        return delay * COINGECKO_RETRY_BACKOFF

    @staticmethod
    def _sleep_for_429(attempt: int, response: requests.Response) -> None:
        if attempt >= COINGECKO_MAX_ATTEMPTS - 1:
            return
        sleep_seconds = _parse_retry_after(
            response.headers.get("Retry-After"),
            default=COINGECKO_429_FALLBACK_SECONDS,
        )
        sleep_seconds = min(sleep_seconds, COINGECKO_429_MAX_SLEEP_SECONDS)
        time.sleep(sleep_seconds)
