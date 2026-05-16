"""Resolve ISINs to market quotes (OpenFIGI → Yahoo Finance).

Free, indicative last prices for personal portfolio reporting. Not
licensed market data — suitable for family reports only.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from functools import lru_cache

import certifi
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence

from app.services.portfolio import CombinedHoldingRow
from app.utils.decimal_utils import ZERO
from app.utils.logging import get_logger

logger = get_logger(__name__)

OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SEARCH_URL = (
    "https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=5"
)

# OpenFIGI: 10 jobs/request without key, 100 with key (we stay conservative).
_OPENFIGI_CHUNK = 10
_OPENFIGI_PAUSE_SEC = 0.25

# Bloomberg `exchCode` → Yahoo Finance ticker suffix (common EU / US).
_EXCH_TO_YAHOO_SUFFIX: dict[str, str] = {
    "GR": ".DE",
    "GF": ".DE",
    "GS": ".DE",
    "GB": ".DE",
    "GD": ".DE",
    "GY": ".DE",
    "GM": ".DE",
    "GT": ".DE",
    "GH": ".DE",
    "GA": ".DE",
    "GQ": ".DE",
    "LSE": ".L",
    "LN": ".L",
    "SW": ".SW",
    "SE": ".SW",
    "FP": ".PA",
    "PA": ".PA",
    "NA": ".AS",
    "AS": ".AS",
    "IM": ".MI",
    "MI": ".MI",
    "CO": ".CO",
    "ST": ".ST",
    "HE": ".HE",
    "VI": ".VI",
    "BR": ".BR",
    "MC": ".MC",
    "LS": ".LS",
}

# When OpenFIGI returns several listings, prefer venues that usually quote
# in EUR (or other European currencies we convert from) over US primary.
_EUR_LISTING_EXCH_CODES: frozenset[str] = frozenset(_EXCH_TO_YAHOO_SUFFIX)

# Yahoo quotes some exchanges in minor units. Keys are exact ``meta.currency``
# strings (case-sensitive). Values are (ISO major currency, multiplier).
_YAHOO_MINOR_UNIT_CURRENCIES: dict[str, tuple[str, Decimal]] = {
    "GBp": ("GBP", Decimal("0.01")),  # LSE: pence, not pounds
    "GBX": ("GBP", Decimal("0.01")),
    "ILA": ("ILS", Decimal("0.01")),
    "ZAc": ("ZAR", Decimal("0.01")),
}


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """TLS context with Mozilla's CA bundle (macOS python.org builds often lack one)."""

    return ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class MarketQuote:
    """Last price resolved for one ISIN."""

    isin: str
    price: Decimal
    yahoo_symbol: str


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Optional[bytes] = None,
    headers: Optional[dict[str, str]] = None,
) -> object:
    req_headers = {"User-Agent": "portfolio-ledger/1.0", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=body,
        headers=req_headers,
        method=method,
    )
    with urllib.request.urlopen(
        request,
        timeout=30,
        context=_ssl_context(),
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def _to_yahoo_symbol(ticker: str, exch_code: str) -> str:
    ticker = ticker.strip()
    if not ticker:
        return ticker
    suffix = _EXCH_TO_YAHOO_SUFFIX.get(exch_code.upper())
    if suffix:
        return f"{ticker}{suffix}"
    return ticker


def _openfigi_resolve_batch(
    isins: Sequence[str],
    *,
    api_key: Optional[str],
) -> dict[str, str]:
    """Map ISIN → Yahoo symbol via OpenFIGI."""

    if not isins:
        return {}

    jobs = [{"idType": "ISIN", "idValue": isin} for isin in isins]
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    try:
        payload = _http_json(
            OPENFIGI_MAPPING_URL,
            method="POST",
            body=json.dumps(jobs).encode("utf-8"),
            headers=headers,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("OpenFIGI request failed: %s", exc)
        return {}

    if not isinstance(payload, list):
        logger.warning("Unexpected OpenFIGI response type: %s", type(payload))
        return {}

    out: dict[str, str] = {}
    for isin, block in zip(isins, payload):
        if not isinstance(block, dict):
            continue
        data = block.get("data")
        if not isinstance(data, list):
            continue
        symbol = _pick_best_yahoo_symbol(data)
        if symbol:
            out[isin] = symbol
    return out


def _pick_best_yahoo_symbol(data: list[object]) -> Optional[str]:
    """Choose a Yahoo symbol, preferring European listings when available."""

    best_symbol: Optional[str] = None
    best_rank = -1
    for item in data:
        if not isinstance(item, dict):
            continue
        ticker = (item.get("ticker") or "").strip()
        if not ticker:
            continue
        exch = (item.get("exchCode") or "").strip().upper()
        rank = 2 if exch in _EUR_LISTING_EXCH_CODES else 0
        if rank > best_rank:
            best_rank = rank
            best_symbol = _to_yahoo_symbol(ticker, exch)
    return best_symbol


def _yahoo_search_symbol(isin: str) -> Optional[str]:
    """Fallback: Yahoo search by ISIN when OpenFIGI did not resolve."""

    url = YAHOO_SEARCH_URL.format(query=urllib.request.quote(isin, safe=""))
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("Yahoo search failed for %s: %s", isin, exc)
        return None

    if not isinstance(payload, dict):
        return None
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return None
    for quote in quotes:
        if not isinstance(quote, dict):
            continue
        symbol = (quote.get("symbol") or "").strip()
        if symbol:
            return symbol
    return None


def _normalize_yahoo_quote_currency(
    price: Decimal,
    currency: str,
) -> tuple[Decimal, str]:
    """Map Yahoo minor-unit currencies (e.g. GBp) to major units (GBP)."""

    raw = currency.strip()
    minor = _YAHOO_MINOR_UNIT_CURRENCIES.get(raw)
    if minor is not None:
        major, factor = minor
        return price * factor, major
    return price, raw.upper()


def _yahoo_price_and_currency(
    yahoo_symbol: str,
) -> Optional[tuple[Decimal, str]]:
    """Read last price and quote currency from Yahoo's chart endpoint."""

    encoded = urllib.request.quote(yahoo_symbol, safe="")
    url = YAHOO_CHART_URL.format(symbol=encoded)
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("Yahoo chart failed for %s: %s", yahoo_symbol, exc)
        return None

    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return None
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    meta = first.get("meta")
    if not isinstance(meta, dict):
        return None
    raw = meta.get("regularMarketPrice")
    currency_raw = (meta.get("currency") or "").strip()
    if raw is None or not currency_raw:
        return None
    try:
        price = Decimal(str(raw))
    except Exception:
        return None
    if price <= ZERO:
        return None
    price, currency = _normalize_yahoo_quote_currency(price, currency_raw)
    return price, currency


def _fx_multiplier(
    from_currency: str,
    to_currency: str,
    cache: dict[tuple[str, str], Decimal],
) -> Optional[Decimal]:
    """Return factor to multiply an amount in ``from_currency`` into ``to_currency``."""

    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return Decimal("1")

    key = (from_currency, to_currency)
    if key in cache:
        return cache[key]

    # e.g. USDEUR=X → price is EUR per 1 USD → multiply USD amount by rate.
    direct_pair = f"{from_currency}{to_currency}=X"
    direct = _yahoo_price_and_currency(direct_pair)
    if direct is not None:
        rate, quote_ccy = direct
        if quote_ccy == to_currency:
            cache[key] = rate
            return rate

    # e.g. EURUSD=X → price is USD per 1 EUR → divide USD amount by rate.
    inverse_pair = f"{to_currency}{from_currency}=X"
    inverse = _yahoo_price_and_currency(inverse_pair)
    if inverse is not None:
        rate, quote_ccy = inverse
        if quote_ccy == from_currency and rate != ZERO:
            mult = Decimal("1") / rate
            cache[key] = mult
            return mult

    logger.warning(
        "No FX rate found to convert %s → %s (tried %s and %s)",
        from_currency,
        to_currency,
        direct_pair,
        inverse_pair,
    )
    return None


def _convert_price_to_currency(
    price: Decimal,
    from_currency: str,
    to_currency: str,
    fx_cache: dict[tuple[str, str], Decimal],
) -> Optional[Decimal]:
    mult = _fx_multiplier(from_currency, to_currency, fx_cache)
    if mult is None:
        return None
    return price * mult


def resolve_isins_to_yahoo_symbols(
    isins: Iterable[str],
    *,
    openfigi_api_key: Optional[str] = None,
) -> dict[str, str]:
    """Resolve each ISIN to a Yahoo Finance ticker symbol."""

    unique = list(dict.fromkeys(i.upper() for i in isins if i))
    symbols: dict[str, str] = {}

    for start in range(0, len(unique), _OPENFIGI_CHUNK):
        chunk = unique[start : start + _OPENFIGI_CHUNK]
        symbols.update(
            _openfigi_resolve_batch(chunk, api_key=openfigi_api_key),
        )
        if start + _OPENFIGI_CHUNK < len(unique):
            time.sleep(_OPENFIGI_PAUSE_SEC)

    for isin in unique:
        if isin in symbols:
            continue
        fallback = _yahoo_search_symbol(isin)
        if fallback:
            symbols[isin] = fallback
            logger.info("Yahoo search resolved %s → %s", isin, fallback)
        else:
            logger.warning("Could not resolve ISIN %s to a market symbol", isin)

    return symbols


def fetch_last_prices(
    yahoo_symbols_by_isin: Mapping[str, str],
    *,
    target_currency: str = "EUR",
) -> dict[str, MarketQuote]:
    """Fetch indicative last prices for each ISIN in ``target_currency``."""

    target = target_currency.upper()
    fx_cache: dict[tuple[str, str], Decimal] = {}
    quotes: dict[str, MarketQuote] = {}
    for isin, yahoo_symbol in yahoo_symbols_by_isin.items():
        quote = _yahoo_price_and_currency(yahoo_symbol)
        if quote is None:
            logger.warning(
                "No price returned for ISIN %s (Yahoo symbol %s)",
                isin,
                yahoo_symbol,
            )
            continue
        price, quote_ccy = quote
        if quote_ccy != target:
            converted = _convert_price_to_currency(
                price, quote_ccy, target, fx_cache,
            )
            if converted is None:
                logger.warning(
                    "Skipping ISIN %s: could not convert %s %s to %s",
                    isin,
                    price,
                    quote_ccy,
                    target,
                )
                continue
            logger.info(
                "Converted %s price for ISIN %s: %s %s → %s %s",
                yahoo_symbol,
                isin,
                price,
                quote_ccy,
                converted,
                target,
            )
            price = converted
        quotes[isin.upper()] = MarketQuote(
            isin=isin.upper(),
            price=price,
            yahoo_symbol=yahoo_symbol,
        )
        time.sleep(0.1)
    return quotes


def fetch_market_quotes_for_isins(
    isins: Iterable[str],
    *,
    target_currency: str = "EUR",
    openfigi_api_key: Optional[str] = None,
) -> dict[str, MarketQuote]:
    """Resolve ISINs and fetch last prices (OpenFIGI + Yahoo Finance)."""

    symbols = resolve_isins_to_yahoo_symbols(
        isins,
        openfigi_api_key=openfigi_api_key,
    )
    return fetch_last_prices(symbols, target_currency=target_currency)


def apply_market_quotes_to_combined(
    rows: list[CombinedHoldingRow],
    quotes_by_isin: Mapping[str, MarketQuote],
) -> list[CombinedHoldingRow]:
    """Attach current price, market value, and unrealized G/L to security rows."""

    if not quotes_by_isin:
        return rows

    updated: list[CombinedHoldingRow] = []
    for row in rows:
        if row.is_cash:
            updated.append(row)
            continue
        quote = quotes_by_isin.get(row.isin.upper())
        if quote is None:
            updated.append(row)
            continue
        market_value = quote.price * row.combined_shares
        updated.append(
            CombinedHoldingRow(
                isin=row.isin,
                symbol=row.symbol,
                shares_per_account=dict(row.shares_per_account),
                combined_shares=row.combined_shares,
                combined_average_price=row.combined_average_price,
                total_invested=row.total_invested,
                family_percentage=row.family_percentage,
                is_cash=False,
                current_price=quote.price,
                market_value=market_value,
                unrealized_gain_loss=market_value - row.total_invested,
            )
        )
    return updated
