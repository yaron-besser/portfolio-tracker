"""Market data a provider gave us, as opposed to anything Yaron decided.

These tables live in `data/market/` for that reason. Nothing here is his
judgement; all of it is refetchable, and all of it carries the date it was
fetched, because a value with no date is not recent — it is of unknown age.

Usage:
    from src import market
    market.provider_ticker("BRKB", instruments.load())   # -> "BRK-B"
    market.unresolvable(instruments.load())              # ids worth fixing
    market.load_holdings()                   # what each ETF owns, company by company
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pandera.pandas as pa

from src.claims import _is_real_date, _vocab_check

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DIR = PROJECT_ROOT / "data" / "market"

# Instrument kinds no data provider has a quote or a sector for. Excluded from
# the gap report on purpose: a cash balance with no sector is not a hole.
NON_MARKET_KINDS = {"cash", "fx"}

# Share classes the providers spell with a separator. An instrument_id is ours
# and deliberately has no punctuation; the provider's does.
_KNOWN_SHARE_CLASSES = {"BRKB": "BRK-B"}


def provider_ticker(instrument_id: str, instruments: pd.DataFrame) -> str | None:
    """The symbol a provider will recognise, or None if there cannot be one.

    Order matters: `price_ticker` is the column that exists precisely because
    an id and a quote symbol diverge, so it wins. `av_ticker` is next. The id
    itself is the last resort, and it is right far more often than not —
    foreign listings like `005930.KS` already carry their exchange suffix.
    """
    row = instruments.loc[instruments["instrument_id"] == instrument_id]
    if row.empty:
        return None
    row = row.iloc[0]
    if str(row.get("instrument_type", "")).lower() in NON_MARKET_KINDS:
        return None
    for column in ("price_ticker", "av_ticker"):
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _KNOWN_SHARE_CLASSES.get(instrument_id, instrument_id)


# A holding's exchange, as an ISSUER writes it, against the suffix HIS BOOK
# uses. Issuer files are Bloomberg-style (`005930 KS`); his own ids and every
# Yahoo-sourced row are Yahoo-style (`005930.KS`), so his spelling is the one
# that wins — it is `instruments.csv` that says what a ticker means, and every
# other table points at it.
#
# Only codes worth the risk are here. A wrong guess cannot invent a join —
# holding_instrument() returns an id only when that id is already in his book
# — but a missing code costs nothing but a join that does not happen.
_EXCHANGE_SUFFIX = {
    "KS": ".KS",    # Korea          "SS": Stockholm, NOT Shanghai
    "SS": ".ST", "LN": ".L", "AU": ".AX", "CN": ".TO", "JP": ".T",
    "TT": ".TW", "HK": ".HK", "GR": ".DE", "FP": ".PA", "NO": ".OL",
    "SM": ".MC", "IM": ".MI", "SW": ".SW", "BZ": ".SA",
    # India: WisdomTree writes IB where Bloomberg writes IN. Both appear in
    # EPI, over a hundred rows between them.
    "IN": ".NS", "IB": ".NS",
}


def _squash(symbol: str) -> str:
    """A ticker with its separators removed: `BRK.B` and `BRK-B` -> `BRKB`."""
    return re.sub(r"[.\-/\s]", "", str(symbol)).upper()


def holding_instrument(symbol: str, instruments: pd.DataFrame) -> str | None:
    """Which of his instruments an ETF holding IS, or None.

    DRAM and EEM hold `005930 KS`; his book holds `005930.KS`. Both are
    Samsung, nothing can join them, and his real Samsung exposure reads
    $1,240 short — the money he bought on purpose, plus a chunk his funds
    bought for him, counted as two unrelated things.

    Stored rows keep whatever their issuer wrote, because a holdings row
    should say what its source said; the translation happens here, on the way
    out. The guarantee that makes a guessed exchange code safe: this only
    ever returns an id that ALREADY EXISTS in his book, so the worst a wrong
    guess can do is fail to join.
    """
    symbol = str(symbol).strip()
    # `instruments.load()` reads blanks as NaN, and on pandas 2 `astype(str)`
    # turns those into the literal "nan" — which then equals a NaN symbol and
    # resolves EVERY blank-ticker row to the first one. Guarded here rather
    # than left to the installed pandas, because requirements.txt permits 2.2.
    if not symbol or symbol.lower() in ("nan", "none"):
        return None
    ids = set(instruments["instrument_id"])
    if symbol in ids:
        return symbol
    for column in ("price_ticker", "av_ticker"):
        if column in instruments.columns:
            # fillna FIRST: on pandas 2 a NaN cell renders as the string
            # "nan" and would match a NaN symbol. An empty symbol is already
            # rejected above, so "" can never match here.
            values = instruments[column].fillna("").astype(str).str.strip()
            hit = instruments.loc[values == symbol, "instrument_id"]
            if not hit.empty:
                return hit.iloc[0]
    # A share class an issuer writes with a separator. XLF's largest holding
    # is `BRK.B` and his book says `BRKB`: unjoined, his Berkshire read
    # $1,940 when $2,210 of it is his — understated by an eighth. Only fires
    # when the symbol HAS punctuation, and only on a single match.
    squashed = _squash(symbol)
    if squashed != symbol.upper():
        hit = [x for x in ids if _squash(x) == squashed]
        if len(hit) == 1:
            return hit[0]
    parts = symbol.split()
    if len(parts) == 2:
        candidate = parts[0] + _EXCHANGE_SUFFIX.get(parts[1].upper(), "")
        if candidate != parts[0] and candidate in ids:
            return candidate
    # A fourth spelling of the same company: iShares writes Samsung bare,
    # `005930`, naming the exchange in a column instead of the ticker.
    #
    # NUMERIC ONLY, and that restriction is the whole safety of this branch.
    # Unlike the one above it, this consults no exchange at all — it attaches
    # whatever foreign suffix happens to be unique in his book. Let it see
    # letters and XLP's `EL` (Estée Lauder) would join an `EL.PA`
    # (EssilorLuxottica) the day he buys one, crediting him $22 of a company
    # he does not hold and deleting one he does. Korean, Taiwanese, Japanese
    # and Hong Kong codes are digits; a US ticker never is.
    #
    # Cash and FX are excluded first — `EUR.USD` would otherwise swallow a
    # holding spelled `EUR`.
    if len(parts) == 1 and symbol.isdigit():
        tradeable = instruments[~instruments["instrument_type"].astype(str)
                                .str.lower().isin(NON_MARKET_KINDS)]
        roots = tradeable.loc[
            tradeable["instrument_id"].str.split(".").str[0] == symbol,
            "instrument_id"]
        if len(roots) == 1 and roots.iloc[0] != symbol:
            return roots.iloc[0]
    return None


def unresolvable(instruments: pd.DataFrame) -> pd.DataFrame:
    """Instruments whose id is probably not a provider ticker, and why.

    Heuristic on purpose, and deliberately narrow: a wrong sector is worse
    than a missing one, so this reports a suspicion for a human to settle
    rather than silently rewriting anything. A share class written without
    its separator is the case that actually occurs here.
    """
    rows = []
    for _, r in instruments.iterrows():
        iid = r["instrument_id"]
        if str(r.get("instrument_type", "")).lower() in NON_MARKET_KINDS:
            continue
        if iid in _KNOWN_SHARE_CLASSES and not _has_explicit_ticker(r):
            rows.append({
                "instrument_id": iid,
                "name": r.get("name", ""),
                "why": f"providers spell this {_KNOWN_SHARE_CLASSES[iid]!r} — "
                       f"set price_ticker so it is recorded, not inferred",
            })
    return pd.DataFrame(rows, columns=["instrument_id", "name", "why"])


def _has_explicit_ticker(row: pd.Series) -> bool:
    return any(isinstance(row.get(c), str) and row.get(c).strip()
               for c in ("price_ticker", "av_ticker"))


HOLDINGS_PATH = MARKET_DIR / "etf_holdings.csv"

HOLDINGS_COLUMNS = ["ticker", "holding", "holding_name", "weight", "coverage",
                    "source", "as_of", "as_of_kind"]


def holdings_schema() -> pa.DataFrameSchema:
    """What an ETF owns, company by company.

    `etf_compositions.csv` holds sectors; this is the table that answers "how
    much NVDA do I really own, counting what my ETFs hold for me" — a question
    a sector weight cannot answer on its own.

    `coverage` is on every row and not optional: providers return roughly a
    fund's top ten holdings, which for a broad fund is about half of it.
    Without the number beside the weights, a later reader treats ten holdings
    as the whole fund, overstates concentration, and has no way to notice.
    Deliberately NOT checked against the weight column summing to anything —
    a later task stores holdings only down to a small threshold, computing
    `coverage` before that cutoff is applied, so the rows are not meant to add
    up to `coverage`, or to 1.0.

    `as_of_kind` is why `as_of` can be trusted. Two of the three sources
    publish a date for their own holdings and one does not, so without this
    column the same cell means "the issuer says these holdings were true on
    this day" for some rows and "this is the day we looked" for others —
    and the second kind sorts as the freshest data in the table while being
    the oldest by years. Saying it in `source` prose is not enough: nothing
    can sort or filter on an English sentence.
    """
    return pa.DataFrameSchema(
        {
            "ticker": pa.Column(str, nullable=False),
            "holding": pa.Column(str, nullable=False),
            "holding_name": pa.Column(str, nullable=True),
            "weight": pa.Column(float, pa.Check.in_range(0, 1), nullable=False,
                                coerce=True),
            "coverage": pa.Column(float, pa.Check.in_range(0, 1), nullable=False,
                                  coerce=True),
            "source": pa.Column(str, nullable=False),
            # `_is_real_date`, not a regex, and for the same reason its
            # sibling `etf_compositions.as_of` uses it: a regex accepts
            # 2026-02-30, which no reader can ever resolve into a real day.
            # Unlike that sibling, blank is not allowed — a holdings row
            # always comes from one fetch, so there is always a day to name.
            "as_of": pa.Column(
                str,
                pa.Check(lambda s: s.map(_is_real_date),
                         error="as_of must be a real ISO date, YYYY-MM-DD"),
                nullable=False,
            ),
            # `published` — the source stated this date for these holdings.
            # `read`      — nobody published one; this is when we looked.
            # Not nullable: a blank would be a third state meaning "unknown
            # which", and an unknown provenance has to be assumed the worse
            # of the two, not left for a reader to guess.
            "as_of_kind": pa.Column(str, _vocab_check("as_of_kind"),
                                    nullable=False),
        },
        unique=["ticker", "holding"],
        strict=True,
    )


def load_holdings(path: Path = HOLDINGS_PATH) -> pd.DataFrame:
    """Read and validate the holdings table. Empty frame if nothing is filed yet.

    Columns are read as explicit `str`/numeric dtypes, same as
    `exposure.load()`, rather than left to pandas' type sniffing — a
    header-only file has no rows to sniff a type from, and the schema below
    would otherwise refuse that empty table as a dtype mismatch.
    """
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=HOLDINGS_COLUMNS)
    str_cols = [c for c in HOLDINGS_COLUMNS if c not in ("weight", "coverage")]
    df = pd.read_csv(path, dtype={c: str for c in str_cols}, keep_default_na=False)
    df["weight"] = pd.to_numeric(df["weight"])
    df["coverage"] = pd.to_numeric(df["coverage"])
    return holdings_schema().validate(df, lazy=True)
