"""
Which companies each ETF holds — the table that answers "how much NVIDIA do I
really own, counting what my funds hold for me".

`etf_compositions.csv` says an ETF is 40% technology. It cannot say that 15%
of it is one company, and it cannot be added to the NVDA position on the book.
This fills `data/market/etf_holdings.csv`, which can.

🚨 **These weights are a FLOOR and are NEVER scaled to 100%.** That is the
opposite of `ops/refresh_etf_compositions.py`, and on purpose. A partial
sector breakdown claims to describe the whole fund, so normalising it is
honest. Ten holdings do not claim that: they are ten holdings. Scaling them
would turn a floor into a fabricated total, and nothing downstream could tell
the difference. `coverage` is how much of the fund the stored rows really
account for, and it is computed BEFORE the dust threshold is applied and
BEFORE a cash line is dropped — otherwise removing a placeholder would
inflate what remains.

**Where a fund's holdings come from, in order.**

1. The issuer's own file, downloaded. Written once per ISSUER, not per fund,
   and keyed off `instruments.issuer` — so a fund bought next year from a
   family already here needs no code at all. SPDR and Global X are the two
   that can be automated; all nine issuers he holds were tested on
   2026-10-05 and the registry comment beside `ISSUER_DOWNLOADERS` records
   what each one does, so nobody re-tests them to find out.
2. A file he dropped into `data/market/holdings_inbox/` himself. Not a queue
   and not a deadline: a fund with no file simply stays on the floor. A
   website redesign cannot break a file already on disk. A spreadsheet is
   converted to CSV on the way in, so ONE parser reads every file — the
   preamble handling, the percent-sign cleaning and the as-of search are
   proven once instead of twice.
3. Yahoo's top holdings. Always available, always labelled a floor.

Every exact source is checked before anything is stored — the weights add up
to about the whole fund, the fund has a plausible number of holdings, and the
symbols look like symbols. A site redesign that still returns a readable sheet
is the dangerous failure, so a half-parsed page is refused and the fund falls
through to the next source rather than being stored as a fund that is 12%
invested and 88% nothing.

Run from the repo root:

    venv/bin/python ops/refresh_etf_holdings.py            # every held ETF, report only
    venv/bin/python ops/refresh_etf_holdings.py XLK QQQ    # just these
    venv/bin/python ops/refresh_etf_holdings.py --write
"""

from __future__ import annotations

import csv
import io
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import exposure, instruments, market  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# His decision, 2026-10-04: store every holding at or above 0.05% of its fund
# and no deeper. VTI alone publishes thousands of constituents, and at the
# size of the position he holds in it, a holding a tenth of this size is worth
# small change — a row that can never change a decision. A constant, never a
# number typed into a fetcher.
MIN_HOLDING_WEIGHT = 0.0005

# Files he downloads himself, and COMMITTED since 2026-10-06. Each one is a
# dated snapshot that cannot be re-fetched — an issuer serves today's
# holdings, never the ones a stored row was parsed from — so the file is the
# provenance of every figure that came out of it, not merely its delivery.
INBOX_DIR = market.MARKET_DIR / "holdings_inbox"

# No source, exact or floor, may claim more of a fund than the whole of it.
# Applied in assemble() so it covers EVERY path: the stored `coverage` is
# clamped into the range the table allows, and a clamp without this ceiling
# turns nonsense into the most reassuring number in the file — a provider
# answering with a junk 140% cash line would store "100% covered" for a fund
# whose only real row is 8% of it. The headroom above 1 is for rounding;
# issuer files land a hair either side of 100%.
MAX_COVERAGE = 1.05

# What an issuer's own file must ALSO clear before a single row of it is
# stored. It claims to be the WHOLE fund, so it has to add up to one.
MIN_FULL_COVERAGE = 0.90
# Deliberately low: a real fund can genuinely hold seven companies (MAGS
# does), so this only catches a file that is obviously a fragment. Coverage
# is the check that does the work.
MIN_FULL_HOLDINGS = 3

# Above this, a weight column is percentages where fractions are expected.
# Nothing real sits between: a fund's weights add to about 1 or about 100.
_PERCENT_IF_SUM_ABOVE = 1.5

# Funds with no company inside them at all. Each is here because of WHAT IT
# HOLDS, not because a fetch failed — so the gap report must not list them:
# a queue that cannot reach zero stops being read, and then the real gaps in
# it go unread too. Adding a line here is a statement about the fund.
NO_EQUITY_INSIDE = {
    "SGOV": "Treasury bills",
    "SLV": "silver bullion",
    "IBIT": "bitcoin",
    "DBA": "agricultural futures, and the cash that collateralises them",
}

# Placeholders a provider puts in the symbol column. A row like this carries
# real weight, so dropping it must not change the coverage figure.
_JUNK_SYMBOLS = {"--", "N/A", "NA", "CASH", "USD", "-", ".",
                 # What an aggregator puts in the symbol column for
                 # everything that is not a listed company: QQQ's US dollars,
                 # VTI's futures, derivatives and private placements. Stored,
                 # they MERGE into one invented company called "Other".
                 "OTHER"}

# Where a fund parks the cash it has not invested. These carry real-looking
# tickers, so the placeholder list above sails straight past them — and
# stored, they become "companies" that resolve to no instrument and count as
# equity exposure the fund does not have. Not a rounding question: in some
# funds a sweep is a large minority of everything the floor returns. Run the
# fetcher over DRAM or MAGS and compare the two percentages it prints — the
# gap between them IS the cash, and nothing else in the repo can show it,
# because these rows are deliberately not stored.
_CASH_VEHICLE_SYMBOLS = {
    "XTSLA",    # BlackRock Cash Funds Treasury — the iShares sweep
    "FGXXX",    # First American Government Obligations
    "AGPXX",    # Invesco Short-Term Investments Government & Agency
    "XBOX",     # Roundhill Ultra Short Duration
    "TBLL",     # Invesco Short Term Treasury
    "BIL", "SHV", "SGOV",   # short-bill ETFs, held for the same reason
}

# ...and the words that name one where the ticker is unfamiliar. Deliberately
# whole phrases, never a bare "cash": "Cash America International" was a real
# listed company, and a wrong drop silently removes a real holding.
_CASH_VEHICLE_WORDS = (
    "money market", "government oblig", "govt oblig", "treasury oblig",
    "liquidity fund", "cash management", "ultra short duration",
    "ultra shrt dur", "short-term investments trust",
    # Collateral behind a swap-based fund. DRAM parks 22% of itself in bills
    # to fund its contracts; stored, they are the fund's third-largest
    # "company". Named in full — "treasury" alone would swallow a company.
    "treasury bill", "treasury bills",
)


# A fund's balance in one currency: "KRW CASH", "EUR CASH". The three-letter
# code looks exactly like a ticker, so the placeholder list sails past it and
# the currency is stored as a company. Anchored, so a company whose name ends
# in the word cash is untouched.
_FX_CASH = re.compile(r"^[A-Z]{3}\s+CASH(\s|\(|$)", re.IGNORECASE)


# A currency written out in full, as Amplify does: symbol `CAD`, name
# "CANADIAN DOLLAR". Matched WHOLE and exactly, never as a substring — "EURO"
# inside a name would otherwise take Eurofins and Euronet with it, which is
# the failure the cash rules are written to avoid.
_CURRENCY_NAMES = {
    "US DOLLAR", "U.S. DOLLAR", "EURO", "CANADIAN DOLLAR", "BRITISH POUNDS",
    "BRITISH POUND", "JAPANESE YEN", "SWISS FRANC", "AUSTRALIAN DOLLAR",
    "HONG KONG DOLLAR", "KOREAN WON", "PERUVIAN SOL", "INDIAN RUPEE",
    "SINGAPORE DOLLAR", "NEW TAIWAN DOLLAR", "DANISH KRONE",
    "NORWEGIAN KRONE", "SWEDISH KRONA", "MEXICAN PESO", "CHINESE YUAN",
}


def _is_cash_vehicle(symbol: str, name) -> bool:
    """A money-market, sweep or currency holding — not a company."""
    if symbol.upper() in _CASH_VEHICLE_SYMBOLS:
        return True
    text = name if isinstance(name, str) else ""
    stripped = text.strip()
    if _FX_CASH.match(stripped) or stripped.upper() in _CURRENCY_NAMES:
        return True
    return any(word in text.lower() for word in _CASH_VEHICLE_WORDS)

# A real ticker, loosely. Only used to notice that a whole column was parsed
# out of the wrong place — never to reject one symbol.
#
# The optional two-letter tail is an exchange code: a fund of miners holds
# `ARG CN`, `FFM AU`, `1515 JP`, and refusing those read COPX's own file as a
# column of company names. Two letters exactly, and only one such tail, so a
# name column stays rejected — "BELLAVISTA RESOURCES LTD" is three tokens and
# "AMERIGO RESOURCES" ends in nine.
_SYMBOL_SHAPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9./\-]{0,11}$")

# ...and the tails that are an exchange rather than part of a company's name.
# A KNOWN list, not "any two letters": that looser rule let `SIEMENS AG`,
# `ALLIANZ SE` and `HEINEKEN NV` read as tickers, which is exactly how a
# column of company NAMES passes the guard meant to catch it. Every code here
# appears in a file he has actually downloaded. A code not yet listed makes
# the guard stricter, never looser — the file is refused rather than stored
# wrong, which is the direction this whole module errs in.
_EXCHANGE_CODES = frozenset("""
    AB AU CN GR HK IB IJ IN JP KS LN MM PW SS TT WI
    FP IM SM SW NO BZ TB KQ CH C1 C2 SP ID PM TI GA QD UH DH EY
""".split())

# How the issuers spell the three columns that matter. Read by _header_row()
# as well as by the inbox parser, so they live up here with the other
# vocabularies rather than beside the one function that happens to use them
# last.
_SYMBOL_HEADERS = ("ticker", "symbol", "ticker symbol", "holding ticker",
                   "security ticker")      # WisdomTree
_WEIGHT_HEADERS = ("weight", "weight (%)", "weight %", "% weight", "weighting",
                   "% of net assets", "% of fund", "portfolio weight",
                   "percent of fund",
                   # Invesco's spelling: total net assets.
                   "% tna", "% of tna", "weight (% tna)",
                   # Amplify's: the holding's value as a share of the fund.
                   "market value (%)", "market value %")
_NAME_HEADERS = ("name", "security name", "holding name", "description",
                 "security", "company", "issuer name",
                 # etfdb heads the company column "Holding" and the ticker
                 # column "Symbol". Without this, QQQ and VTI stored 390 rows
                 # with no company name at all. Safe beside "holding ticker"
                 # above, because the symbol column is matched first.
                 "holding")
_IDENTIFIER_HEADERS = ("identifier", "cusip", "sedol", "isin", "security id")

# A derivative leg: `595112103 TRS 050427 NM`, `02079K305 SWP`. The leading
# token is the identifier of the company the contract is written on.
_DERIVATIVE_LEG = re.compile(r"^(?P<id>\S+)\s+(?:TRS|SWP|SWAP)\b", re.IGNORECASE)


def fold_derivative_legs(symbols, names, identifiers) -> tuple[list[str], list[str]]:
    """Swap legs -> the symbol of the company each one is exposure to.

    A fund can hold a company through shares, through a total return swap, or
    through both at once, and split one swap across several banks: DRAM's
    Micron arrives as three rows and MAGS's seven companies as four each.
    Stored verbatim they are neither one holding nor seven — they are
    twenty-one rows whose symbols resolve to no company at all, while the real
    exposure to Micron reads as a third of itself.

    Each leg names its underlying by identifier, so the identifier column is
    what joins them back up. A leg whose underlying is not separately held
    keeps its own row (DRAM holds CXMT only through a swap); assemble() then
    sums whatever now shares a symbol.
    """
    def cell(value) -> str:
        # An empty CSV cell is NaN, and `str(NaN)` is the truthy "nan" — so a
        # bare `or` fallback below could never reach the symbol column, which
        # is the one column a swap leg always has.
        text = str(value).strip()
        return "" if text.lower() in ("nan", "none") else text

    by_identifier = {}
    for symbol, name, identifier in zip(symbols, names, identifiers):
        key = cell(identifier)
        if key and not _DERIVATIVE_LEG.match(key):
            by_identifier.setdefault(key, (symbol, name))
    out_symbols, out_names = [], []
    for symbol, name, identifier in zip(symbols, names, identifiers):
        leg = _DERIVATIVE_LEG.match(cell(identifier) or cell(symbol))
        underlying = by_identifier.get(leg.group("id")) if leg else None
        # The company's own name, not the contract's: a leg is spelled
        # "MICRON TECHNOLOGY, INC.-SWAP-GOLD-L", and the largest leg would
        # otherwise name the merged row.
        if underlying:
            out_symbols.append(underlying[0])
            out_names.append(underlying[1])
        elif leg:
            # Held ONLY through contracts — DRAM's CXMT. Keyed on the
            # underlying's identifier rather than the contract string, or two
            # legs with different banks and maturities keep two symbols and
            # assemble() has nothing to sum: CXMT split across two
            # counterparties would read 5.01% instead of 9.01%.
            out_symbols.append(leg.group("id"))
            out_names.append(name)
        else:
            out_symbols.append(symbol)
            out_names.append(name)
    return out_symbols, out_names

_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "*/*",
}


def clean_symbol(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.upper() in _JUNK_SYMBOLS:
        return None
    return s


def weights_of(column) -> list[float]:
    """A weight column -> plain numbers, whatever punctuation it arrived with.

    Roundhill, Fundstrat and Invesco all write `3.32%`; pandas reads that as
    not-a-number, every weight becomes zero, and the fund comes back empty
    rather than wrong — which is why three of his funds refused their own
    issuer's file. Commas are stripped for the same reason (`1,234.5`).
    """
    cleaned = (pd.Series(column).astype(str)
               .str.strip()
               .str.replace("%", "", regex=False)
               .str.replace(",", "", regex=False)
               # The accountant's negative: `(0.89%)` is minus 0.89, not
               # unreadable. Read as nothing, a fund's negative cash line
               # vanishes and the weights it offsets are left overstating how
               # much of the fund was accounted for — silently, because a
               # zero looks like a row that simply was not there.
               .str.replace(r"^\((.*)\)$", r"-\1", regex=True))
    return [float(w) if pd.notna(w) else 0.0
            for w in pd.to_numeric(cleaned, errors="coerce")]


def _as_fractions(weights: list[float]) -> list[float]:
    """A weight column as fractions of the fund, whatever units it arrived in.

    SPDR writes 15.470648 for 15.47%; yfinance writes 0.1547 for the same
    thing. Applied once per source, never twice, so the rule stays one rule.
    """
    total = sum(weights)
    return [w / 100 for w in weights] if total > _PERCENT_IF_SUM_ABOVE else weights


def _looks_like_symbol(s: str) -> bool:
    """Does this look like a ticker rather than a company name?

    Only ever used to notice that a whole COLUMN was read out of the wrong
    place — never to reject one holding.
    """
    s = str(s).strip()
    if _SYMBOL_SHAPE.match(s):
        return True
    head, _, tail = s.rpartition(" ")
    return bool(head and tail.upper() in _EXCHANGE_CODES
                and _SYMBOL_SHAPE.match(head))


def dated(published: str) -> tuple[str, str]:
    """(as_of, as_of_kind) for a source that published a date, or did not.

    The single place in this file that can produce `published`, and it cannot
    produce it without a date to point at. Every branch funnels through here
    so "the issuer said these holdings were true on this day" and "this is the
    day we looked" can never be confused for one another — which they are in
    the cell itself, both being a plain ISO date.
    """
    if published:
        return published, "published"
    return date.today().isoformat(), "read"


def assemble(ticker: str, pairs: list[tuple], source: str, as_of: str,
             as_of_kind: str, exact: bool) -> dict | None:
    """Raw (symbol, name, weight) triples -> one fund's storable rows.

    The order here is the whole point and must not be rearranged: coverage is
    summed off the raw triples FIRST, before a placeholder is dropped and
    before the dust threshold is applied. Computed any later, removing a 3%
    cash line from a fund covered at 50% would report 47% of a set of rows
    that sums to 47% — a complete-looking picture of half a fund.
    """
    if not pairs:
        return None
    raw_coverage = sum(float(w) for _, _, w in pairs)
    if raw_coverage > MAX_COVERAGE:
        # Refused, never clamped. The clamp below exists for an issuer file
        # that rounds to a hair over 100%; letting it also absorb a 148% answer
        # would store "100% covered" for a fund we know almost nothing about,
        # which is exactly the failure this whole script is built against —
        # incomplete and indistinguishable from complete. Checked here rather
        # than in why_not_exact() so it binds the floor as well, which is the
        # source that has no other validation at all.
        print(f"  {ticker:5} refused — the source accounts for "
              f"{raw_coverage:.1%} of the fund, which is more than all of it")
        return None
    # Clamped at both ends for the same reason: `coverage` is a fraction of a
    # fund, and a fund is neither more nor less than all of itself. Issuer
    # files round — XBI's add up to a hair over 100% — and throwing a good
    # file away over the sixth decimal would be absurd. The UNCLAMPED figure
    # is kept below, because it is what says a parse went wrong.
    coverage = max(0.0, min(1.0, round(raw_coverage, 6)))

    merged: dict[str, dict] = {}
    for raw_symbol, raw_name, weight in pairs:
        symbol = clean_symbol(raw_symbol)
        w = float(weight)
        if symbol is None or w <= 0:
            continue
        if _is_cash_vehicle(symbol, raw_name):
            # Dropped like a placeholder, and for the same reason it does not
            # change `coverage`: the money is genuinely in the fund, it is
            # just not in a company. Stored, it would join a look-through
            # total as equity it is not.
            continue
        if symbol in merged:
            # Two lines for one company. Crashing loses the fund, dropping
            # understates the company; adding them up is the only answer
            # that is true — and the table forbids a duplicate anyway.
            merged[symbol]["weight"] += w
        else:
            name = raw_name if isinstance(raw_name, str) else ""
            merged[symbol] = {"holding": symbol, "holding_name": name.strip(),
                              "weight": w}

    named = len(merged)
    rows = [r for r in merged.values() if r["weight"] >= MIN_HOLDING_WEIGHT]
    if any(r["weight"] > 1 for r in rows):
        # A single holding cannot be more than the fund. Something was parsed
        # out of the wrong column; refuse rather than store it — and say so,
        # because a fund that silently returns nothing reads as a fund with
        # nothing in it.
        print(f"  {ticker:5} refused — a holding weighs more than the whole "
              f"fund, so a column was read out of the wrong place")
        return None
    if not rows:
        return None
    rows.sort(key=lambda r: (-r["weight"], r["holding"]))
    for r in rows:
        r["weight"] = round(r["weight"], 8)
    return {"ticker": ticker, "rows": rows, "coverage": coverage,
            # What the stored COMPANY rows add up to, which is less than
            # `coverage` by whatever cash and dust was dropped. Reported
            # beside it so a fund that is mostly cash cannot read as a fund
            # that is mostly described.
            "stored": round(sum(r["weight"] for r in rows), 8),
            "raw_coverage": raw_coverage, "named": named,
            "source": source, "as_of": as_of, "as_of_kind": as_of_kind,
            "exact": exact}


def why_not_exact(result: dict | None) -> str:
    """Why an issuer file cannot be trusted as the whole fund, or "".

    The failure to guard against is not "incomplete" — it is "incomplete and
    indistinguishable from complete". A redesigned page that still parses into
    three readable rows is exactly that.
    """
    if result is None:
        return "nothing readable in it"
    # Only the lower bound here: assemble() already refused anything over the
    # ceiling, for every source rather than just this one.
    cov = result["raw_coverage"]
    if cov < MIN_FULL_COVERAGE:
        return (f"its weights add up to {cov:.1%} of the fund — an issuer file "
                f"claims to be all of it")
    if result["named"] < MIN_FULL_HOLDINGS:
        return f"only {result['named']} holdings survived parsing"
    shaped = sum(r["weight"] for r in result["rows"] if _looks_like_symbol(r["holding"]))
    if shaped < 0.5 * cov:
        return "most of the weight sits in rows that are not symbols"
    return ""


# ─── 1. the issuer's own file ───────────────────────────────────────────────

SPDR_URL = ("https://www.ssga.com/us/en/intermediary/library-content/products/"
            "fund-data/etfs/us/holdings-daily-us-en-{ticker}.xlsx")


def _download(url: str) -> bytes:
    """One HTTP GET, as a browser. The single place the network is touched."""
    r = requests.get(url, headers=_BROWSER, timeout=60)
    r.raise_for_status()
    return r.content


def _header_row(raw: pd.DataFrame) -> int:
    """Index of the row that holds the column names, not the fund's preamble.

    Found by looking for the words rather than counting lines, so a redesign
    that adds or removes a line above the table shifts nothing — and so one
    rule serves every issuer's spelling of the same two columns.
    """
    symbols, weights = set(_SYMBOL_HEADERS), set(_WEIGHT_HEADERS)
    for i in range(len(raw)):
        cells = {str(v).strip().lower() for v in raw.iloc[i]}
        if cells & symbols and cells & weights:
            return i
    raise ValueError("no row in it names both a ticker column and a weight "
                     "column — this is not a holdings file")


# Date spellings seen on issuer files. Tried in order against whatever follows
# the words "as of"; an unparseable one is no date at all, never a guess.
_AS_OF_FORMATS = ("%d-%b-%Y", "%d %b %Y", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y",
)

# "as of" WHAT. A fund file carries several dates and only one of them is the
# day the holdings were true: iShares ships an `Accrual Date` column reaching
# back to 1994, Invesco footers state an inception, and a price-as-of is two
# days stale by construction. Any of them, read as the fund's own as-of,
# would be stored as `published` — an issuer's word for a date it never gave.
_NOT_THE_HOLDINGS_DATE = ("inception", "accru", "maturity", "price",
                          "trade", "settle", "dividend", "nav", "launch")


def _parsed_date(text: str) -> str:
    """One cell's text as an ISO date, or "" — never a guess."""
    text = text.strip().strip(":").strip().strip('"').strip()
    # A spreadsheet date cell becomes "2026-10-02 00:00:00" once converted
    # to CSV. Unstripped, converting an xlsx threw the issuer's own published
    # date away on exactly the path added to make the two formats behave
    # identically.
    text = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?$", "", text)
    for fmt in _AS_OF_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _as_of_near(raw: pd.DataFrame, header: int) -> str:
    """The issuer's own "As of" date, ISO, or "" if the file carries none.

    The day we fetched is not the day the holdings are true for.

    Two things issuers do that a narrower reader missed, each costing a real
    published date and silently substituting the day we looked:

    * iShares writes the label and the date in SEPARATE CELLS —
      `Fund Holdings as of | Oct 02, 2026` — so the cells after the label are
      tried too, not only the rest of the label's own cell.
    * Invesco writes `# as of 2026-10-02` BELOW the table, so the rows after
      the holdings are searched once the preamble turns up nothing. Above
      first, because that is where a label belongs and where a stray match is
      least likely.
    """
    def labelled(value) -> bool:
        """Says "as of", and does not say which OTHER date it means."""
        if not isinstance(value, str) or not re.search(r"as of", value, re.I):
            return False
        return not any(w in value.lower() for w in _NOT_THE_HOLDINGS_DATE)

    def scan(indexes) -> str:
        for i in indexes:
            cells = list(raw.iloc[i])
            for j, value in enumerate(cells):
                if not labelled(value):
                    continue
                after = re.search(r"as of", value, re.I).end()
                for text in [value[after:],
                             *(str(c) for c in cells[j + 1:j + 3])]:
                    found = _parsed_date(text)
                    if found:
                        return found
        return ""

    # The preamble first: a label above the table is where a fund states the
    # day its holdings were true, and it outranks anything found later.
    above = scan(range(header))
    if above:
        return above
    # Amplify heads a COLUMN "As of Date" and repeats the date down it, so
    # there is no preamble to read — the label is the header cell itself.
    for j, label in enumerate(raw.iloc[header]):
        if labelled(label):
            for value in raw.iloc[header + 1:header + 4, j]:
                found = _parsed_date(str(value))
                if found:
                    return found
    # ...and last, a footer. Only the final few rows: scanning the whole
    # table bottom-up reads every HOLDING, and a bond's "interest accrued as
    # of" or a maturity in 2027 would become the fund's date — sorting for
    # ever as the freshest row in the file.
    return scan(range(len(raw) - 1, max(header, len(raw) - 6), -1))


def _widest(text: str) -> int:
    """How many fields the widest row of a CSV has.

    pandas sizes a header-less read from the FIRST line. An issuer's first
    line is the fund's own name — one cell — so the holdings table below it
    reads as "too many fields" and the file dies before a single holding is
    seen. Measured across every row instead, so a title and an "as of" line
    above the table cost nothing.
    """
    return max((len(row) for row in csv.reader(io.StringIO(text))), default=1)


def parse_csv(content: bytes) -> tuple[list[tuple], str]:
    """A CSV holdings file -> (symbol, name, fraction) triples + its as-of.

    Shared by the inbox and by every issuer that publishes CSV, so a file he
    downloads by hand and the same file fetched for him can never parse
    differently.
    """
    text = content.decode("utf-8-sig", errors="replace")
    raw = pd.read_csv(io.StringIO(text), header=None,
                      names=range(_widest(text)), engine="python")
    header = _header_row(raw)
    as_of = _as_of_near(raw, header)
    # Built from the SAME frame the header was found in, never re-read. A
    # second `read_csv` sizes itself from the header row, so a file whose
    # data rows are one field wider silently shifts every label one column
    # off the values it names — and the shifted file still parses, still
    # sums to 100%, and stores company NAMES in the ticker column.
    frame = raw.iloc[header + 1:].reset_index(drop=True)
    frame.columns = [str(c) if pd.notna(c) else f"_unnamed_{i}"
                     for i, c in enumerate(raw.iloc[header])]
    frame = frame.loc[:, ~frame.columns.duplicated()]
    return parse_sheet(frame), as_of


def parse_spdr(content: bytes) -> tuple[list[tuple], str]:
    """A SPDR holdings spreadsheet -> (symbol, name, fraction) triples + as-of.

    Raises rather than returning a fragment: what iShares serves today is an
    HTML page with a spreadsheet's content type, and "unreadable" has to stay
    distinguishable from "empty fund".
    """
    try:
        raw = pd.read_excel(io.BytesIO(content), header=None, engine="openpyxl")
    except Exception as e:                      # noqa: BLE001 — any unreadable file
        raise ValueError(f"not a readable spreadsheet ({type(e).__name__})") from e
    header = _header_row(raw)
    as_of = _as_of_near(raw, header)
    frame = pd.read_excel(io.BytesIO(content), header=header, engine="openpyxl")
    weights = _as_fractions(weights_of(frame["Weight"]))
    return list(zip(frame["Ticker"], frame["Name"], weights)), as_of


def from_spdr(ticker: str) -> dict | None:
    """SPDR's own daily holdings file, or None if it cannot be trusted."""
    try:
        content = _download(SPDR_URL.format(ticker=ticker.lower()))
        pairs, as_of = parse_spdr(content)
    except Exception as e:                      # noqa: BLE001 — a site being down is not fatal
        print(f"  {ticker:5} SPDR file unusable: {type(e).__name__} {e}")
        return None
    when, kind = dated(as_of)
    label = ("SPDR holdings file" if kind == "published" else
             f"SPDR holdings file (no as-of in it; read {when})")
    result = assemble(ticker, pairs, label, when, kind, exact=True)
    refusal = why_not_exact(result)
    if refusal:
        print(f"  {ticker:5} SPDR file refused — {refusal}")
        return None
    return result


GLOBALX_FUND_URL = "https://globalxetfs.com/funds/{ticker}/"
# Global X names each file for the day it covers, so the address cannot be
# built from the ticker alone — the fund page is read first for the one it
# currently points at.
_GLOBALX_CSV = re.compile(
    r"https://assets\.globalxetfs\.com/funds/holdings/[^\"'\s<>]+\.csv")


def from_globalx(ticker: str) -> dict | None:
    """Global X's own full-holdings CSV, found via the fund's page."""
    try:
        page = _download(GLOBALX_FUND_URL.format(ticker=ticker.lower()))
        match = _GLOBALX_CSV.search(page.decode("utf-8", errors="replace"))
        if match is None:
            raise ValueError("its fund page links to no holdings CSV")
        pairs, as_of = parse_csv(_download(match.group(0)))
    except Exception as e:                      # noqa: BLE001 — a site being down is not fatal
        print(f"  {ticker:5} Global X file unusable: {type(e).__name__} {e}")
        return None
    when, kind = dated(as_of)
    label = ("Global X holdings file" if kind == "published" else
             f"Global X holdings file (no as-of in it; read {when})")
    result = assemble(ticker, pairs, label, when, kind, exact=True)
    refusal = why_not_exact(result)
    if refusal:
        print(f"  {ticker:5} Global X file refused — {refusal}")
        return None
    return result


# issuer -> how to download that family's holdings. The registry is the reason
# `instruments.issuer` exists: a new fund from a family already here needs no
# code. Every other issuer he holds was tested on 2026-10-05 and none can be
# automated: iShares answers a CSV request with a web page labelled text/csv,
# Invesco and WisdomTree refuse the request outright, and Vanguard, Amplify,
# VanEck, Roundhill and Fundstrat build their holdings tables in the browser,
# so no file exists at an address to fetch. Their funds fall to the inbox,
# then to the floor.
ISSUER_DOWNLOADERS = {"SPDR": from_spdr, "Global X": from_globalx}


# ─── 2. a file he dropped in himself ────────────────────────────────────────

def _column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lower = {str(c).strip().lower(): c for c in frame.columns}
    for want in candidates:
        if want in lower:
            return lower[want]
    return None


def parse_sheet(frame: pd.DataFrame) -> list[tuple]:
    """Any holdings table -> (symbol, name, fraction) triples.

    Reads whatever is present and ignores the rest, which is the point of the
    inbox: he downloads a file from the fund's own site whenever he feels like
    it, and the columns differ by issuer.
    """
    symbol_col = _column(frame, _SYMBOL_HEADERS)
    weight_col = _column(frame, _WEIGHT_HEADERS)
    if symbol_col is None or weight_col is None:
        raise ValueError("no ticker and weight columns in it")
    name_col = _column(frame, _NAME_HEADERS)
    weights = _as_fractions(weights_of(frame[weight_col]))
    names = frame[name_col] if name_col else [""] * len(frame)
    symbols = list(frame[symbol_col])
    id_col = _column(frame, _IDENTIFIER_HEADERS)
    if id_col is not None:
        symbols, names = fold_derivative_legs(symbols, list(names), frame[id_col])
    return list(zip(symbols, names, weights))


def as_csv(path: Path) -> bytes:
    """One of his downloads as CSV bytes, converting a spreadsheet if needed.

    His instruction, 2026-10-06: *"if there are xlsx just convert it to CSV
    and that's it, we need to keep consistency."* Converting at the door buys
    more than a tidy folder — every file then takes the SAME parse path, so
    the preamble handling, the percent-sign cleaning and the as-of search are
    proven once instead of twice. The spreadsheet branch that used to sit
    here was the less-travelled of the two, which is exactly where a defect
    hides.
    """
    if path.suffix.lower() == ".csv":
        return path.read_bytes()
    frame = pd.read_excel(path, header=None, engine="openpyxl")
    return frame.to_csv(index=False, header=False).encode()


# A site named in a file's preamble. Issuer files name no site at all; an
# aggregator stamps its own address on what it redistributes.
_PUBLISHER = re.compile(r"https?://(?:www\.)?([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def published_by(content: bytes) -> str:
    """The site a downloaded file came from, or "" for an issuer's own.

    QQQ and VTI arrived from etfdb.com, not from Invesco and Vanguard. The
    rows are the same claim either way — the whole fund, adding to 100% — but
    a stored row that reads "inbox QQQ-holdings.csv" says the issuer wrote it,
    and that is a statement about who to believe. Only the preamble is read:
    a holdings table can carry a company's own URL.
    """
    symbols, weights = set(_SYMBOL_HEADERS), set(_WEIGHT_HEADERS)
    preamble = []
    for line in content.decode("utf-8-sig", errors="replace").splitlines():
        cells = {c.strip().strip('"').lower() for c in line.split(",")}
        if cells & symbols and cells & weights:
            break               # the table starts here; stop before it
        preamble.append(line)
    found = _PUBLISHER.search("\n".join(preamble))
    return found.group(1).lower() if found else ""


def _inbox_file(ticker: str, inbox: Path) -> Path | None:
    """His file for this fund. The name must START with the ticker.

    A loose match would read IWM's spreadsheet as EEM's holdings, and the
    stored rows would carry the wrong fund's companies with nothing saying so.
    """
    if not inbox.is_dir():
        return None
    pattern = re.compile(rf"^{re.escape(ticker.upper())}([^A-Z0-9].*)?$")
    for path in sorted(inbox.iterdir()):
        if path.suffix.lower() not in (".csv", ".xlsx", ".xls"):
            continue
        if pattern.match(path.stem.upper()):
            return path
    return None


def from_inbox(ticker: str, inbox: Path) -> dict | None:
    """A spreadsheet he downloaded himself, or None if there is not one."""
    path = _inbox_file(ticker, inbox)
    if path is None:
        return None
    try:
        pairs, as_of = parse_csv(content := as_csv(path))
    except Exception as e:                      # noqa: BLE001 — his file, any shape
        print(f"  {ticker:5} inbox {path.name} unusable: {type(e).__name__} {e}")
        return None
    # Say which kind of date it is, in the column AND in the prose. An
    # issuer's own as-of is when the holdings were true; ours is only when we
    # read the file, which is not the same claim and must not be able to pass
    # for it.
    when, kind = dated(as_of)
    who = published_by(content)
    source = f"inbox {path.name}" + (f" via {who}" if who else "")
    if kind != "published":
        source += f" (no as-of in it; read {when})"
    result = assemble(ticker, pairs, source, when, kind, exact=True)
    refusal = why_not_exact(result)
    if refusal:
        print(f"  {ticker:5} inbox {path.name} refused — {refusal}")
        return None
    return result


# ─── 3. the floor ───────────────────────────────────────────────────────────

def _top_holdings(ticker: str) -> pd.DataFrame:
    """Yahoo's top holdings for one fund. Indexed by symbol; may be empty."""
    import yfinance as yf

    frame = yf.Ticker(ticker).funds_data.top_holdings
    return pd.DataFrame() if frame is None else frame


def from_yahoo(ticker: str) -> dict | None:
    """Roughly a fund's ten largest holdings. Never more than a floor."""
    try:
        frame = _top_holdings(ticker)
    except Exception as e:                      # noqa: BLE001 — a provider being down is not fatal
        print(f"  {ticker:5} Yahoo unavailable: {type(e).__name__}")
        return None
    if frame is None or frame.empty:
        return None
    weights = _as_fractions([float(w) if pd.notna(w) else 0.0
                             for w in pd.to_numeric(frame["Holding Percent"],
                                                    errors="coerce")])
    names = frame["Name"] if "Name" in frame.columns else [""] * len(frame)
    pairs = list(zip(frame.index, names, weights))
    # Yahoo publishes no as-of, so the date on these rows is the day we read
    # them and nothing more — `dated("")` is how that is said in the column,
    # and the sentence below is how it is said to a person. Without both, the
    # cell is shaped exactly like SPDR's genuine issuer date and a panel
    # sorting on `as_of` shows the stalest rows in the table as the freshest:
    # some of Yahoo's European holdings still name companies that were
    # renamed years ago. Run the fetcher over IMAE to see it.
    when, kind = dated("")
    return assemble(ticker, pairs,
                    f"Yahoo top holdings — a floor, not the whole fund "
                    f"(no as-of published; read {when})",
                    when, kind, exact=False)


# ─── putting the three in order ─────────────────────────────────────────────

def issuer_of(ticker: str, instruments_path: Path | None = None) -> str:
    """Who runs this fund, from the reference table — guessed if blank."""
    path = instruments_path or instruments.INSTRUMENTS_PATH
    df = instruments.load(path).fillna("")
    row = df.loc[df["instrument_id"] == ticker]
    if row.empty:
        return ""
    recorded = str(row.iloc[0].get("issuer", "")).strip()
    return recorded or instruments.issuer_from_name(row.iloc[0].get("name", ""))


def named_in_composition(ticker: str,
                         compositions_path: Path | None = None) -> int:
    """How many companies `etf_compositions.csv` already names for this fund.

    `top_holding` is filled only where a sector slice IS one company, which
    is true of a small equal-weight fund and of nothing else — so this is 0
    for QQQ and VTI, and seven for MAGS. That makes it a floor under the
    floor: a number of companies already known to be in the fund, from the
    issuer, with no network call.
    """
    try:
        comp = exposure.load(compositions_path or exposure.COMPOSITIONS_PATH)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return 0
    rows = comp[comp["ticker"] == ticker]
    named = rows["top_holding"].astype(str).str.strip()
    return int(named[named != ""].nunique())


def stored_coverage(ticker: str, holdings_path: Path | None = None) -> float | None:
    """How much of this fund the table already accounts for, or None.

    None means nothing is on file yet — the first refresh of a fund must not
    be compared against an answer that does not exist.
    """
    try:
        df = market.load_holdings(holdings_path or market.HOLDINGS_PATH)
    except (FileNotFoundError, ValueError):
        return None
    rows = df.loc[df["ticker"] == ticker, "coverage"]
    return float(rows.max()) if not rows.empty else None


def fetch_holdings(ticker: str, issuer: str | None = None,
                   inbox: Path | None = None,
                   instruments_path: Path | None = None,
                   compositions_path: Path | None = None,
                   holdings_path: Path | None = None) -> dict | None:
    """Best available holdings for one fund, or None if nobody has any.

    Order: the issuer's own download, then a file he dropped in, then the
    floor. An exact source that cannot be trusted falls through to the next
    one rather than being stored — and says so on the way past.
    """
    if issuer is None:
        issuer = issuer_of(ticker, instruments_path)
    downloader = ISSUER_DOWNLOADERS.get(issuer)
    if downloader is not None:
        got = downloader(ticker)
        if got:
            return got
    got = from_inbox(ticker, inbox if inbox is not None else INBOX_DIR)
    if got:
        return got
    got = from_yahoo(ticker)
    # MIN_FULL_HOLDINGS guards the issuer path; nothing guarded this one, and
    # a floor is only worth storing while it is the best answer there is.
    # Yahoo returned ONE holding for MAGS, a seven-name equal-weight fund
    # whose other six are already on file in etf_compositions.top_holding.
    # Stored, that floor said the book owns ~0.02% of NAV in META when the real
    # figure is about 0.71% — two tables in data/market/ describing the same
    # fund and disagreeing by thirty times. Refusing leaves the thin answer
    # out rather than overwriting a better one with it; ops/data_gaps.py
    # reports the fund as a hole, which is what it is.
    if got is not None and not got["exact"]:
        # A floor may never replace a BETTER answer already on file. His
        # inbox is git-ignored, so the files behind 25 of his 26 funds exist
        # only on his laptop: clear them, re-run, and every fund would be
        # overwritten with Yahoo's ten names — IWM from 714 companies back
        # to 10, in total silence, because `write()` replaces a fund's rows
        # wholesale and a floor always "succeeds".
        #
        # Coverage is the comparison because it is the field that means
        # "how much of this fund was accounted for". An EXACT source is
        # exempt: the issuer's own file is the better answer by definition,
        # even in the rare quarter it covers slightly less.
        before = stored_coverage(ticker, holdings_path)
        if before is not None and got["coverage"] < before:
            print(f"  {ticker:5} floor REFUSED — it accounts for "
                  f"{got['coverage']:.1%} of the fund and what is already "
                  f"stored accounts for {before:.1%}. Refreshing would make "
                  f"the record worse; the file it came from is probably "
                  f"missing from {INBOX_DIR.name}/.")
            return None
        floor_under = named_in_composition(ticker, compositions_path)
        if got["named"] < floor_under:
            print(f"  {ticker:5} floor REFUSED — {got['named']} holding"
                  f"{'' if got['named'] == 1 else 's'} from Yahoo, but "
                  f"etf_compositions.csv already names {floor_under} "
                  f"companies in this fund. Storing it would make the two "
                  f"tables disagree about the same fund.")
            return None
    return got


# ─── what to run it over, and what came back ────────────────────────────────

def held_etfs(positions_path: Path | None = None,
              instruments_path: Path | None = None) -> list[str]:
    """Every ETF with money in it today, biggest first.

    Read at run time and never a hardcoded list: a fund bought this morning
    that is not in some constant would be skipped in silence, and its money
    would leave every look-through figure while the report still said the
    refresh had run.

    Biggest first so that an interrupted run has covered the money that
    matters most.
    """
    positions_path = positions_path or (PROJECT_ROOT / "data" / "positions.csv")
    pos = pd.read_csv(positions_path, dtype=str, keep_default_na=False)
    pos["mv"] = pd.to_numeric(pos["market_value"], errors="coerce").fillna(0.0)
    by_id = pos.groupby("instrument_id")["mv"].sum().sort_values(ascending=False)
    inst = instruments.load(
        instruments_path or instruments.INSTRUMENTS_PATH).fillna("")
    etfs = set(inst.loc[inst["instrument_type"] == "etf", "instrument_id"])
    return [i for i, v in by_id.items() if i in etfs and v != 0]


def gap_report(results: dict[str, dict | None]) -> dict[str, list[str]]:
    """Which funds are exact, which are a floor, and which are really missing.

    A floor is not a gap — it is a worse answer that says so. A fund with no
    company inside it is not a gap either. Only a fund nobody could answer at
    all is.
    """
    out: dict[str, list[str]] = {"exact": [], "floor": [], "missing": [],
                                 "no_equity_inside": []}
    for ticker, result in results.items():
        if result is None:
            key = "no_equity_inside" if ticker in NO_EQUITY_INSIDE else "missing"
            out[key].append(ticker)
        elif result["exact"]:
            out["exact"].append(ticker)
        else:
            out["floor"].append(ticker)
    return out


def write(results: list[dict], path: Path | None = None,
          clear: set[str] | None = None) -> pd.DataFrame:
    """Replace each refreshed fund's rows wholesale; leave every other fund be.

    Never a merge. A company the fund sold yesterday would otherwise survive
    its own removal and keep counting toward his exposure for ever.

    `clear` is for a fund we now know holds no companies at all. A fetch that
    FAILS must leave yesterday's rows alone — losing data to a provider
    outage would be worse than holding a stale number — but a fund
    reclassified as having nothing to look through to has to lose its rows,
    or they outlive the decision that removed them.
    """
    path = path or market.HOLDINGS_PATH
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        df = pd.DataFrame(columns=market.HOLDINGS_COLUMNS)
    df = df.reindex(columns=market.HOLDINGS_COLUMNS)
    refreshed = {r["ticker"] for r in results} | set(clear or ())
    kept = df[~df["ticker"].isin(refreshed)]

    new_rows = [
        {"ticker": r["ticker"], "holding": row["holding"],
         "holding_name": row["holding_name"], "weight": row["weight"],
         "coverage": r["coverage"], "source": r["source"], "as_of": r["as_of"],
         "as_of_kind": r["as_of_kind"]}
        for r in results for row in r["rows"]
    ]
    out = pd.concat([kept, pd.DataFrame(new_rows, columns=market.HOLDINGS_COLUMNS)],
                    ignore_index=True)
    out["weight"] = pd.to_numeric(out["weight"])
    out["coverage"] = pd.to_numeric(out["coverage"])
    out = out.sort_values(["ticker", "weight"], ascending=[True, False])
    market.holdings_schema().validate(out, lazy=True)   # before anything lands
    out.to_csv(path, index=False, quoting=csv.QUOTE_NONNUMERIC)
    return out


def main(argv: list[str] | None = None, positions_path: Path | None = None,
         instruments_path: Path | None = None,
         holdings_path: Path | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    asked = [a for a in argv if not a.startswith("--")]
    writing = "--write" in argv

    tickers = asked or held_etfs(positions_path, instruments_path)
    if not tickers:
        raise SystemExit("No ETFs held, and none named. Nothing to fetch.")

    results: dict[str, dict | None] = {}
    for i, ticker in enumerate(tickers):
        if ticker in NO_EQUITY_INSIDE:
            # Not fetched at all, rather than fetched and found empty. A
            # provider that one day answers with a bullion or cash line would
            # otherwise have it stored as a company holding, and these are
            # among the largest positions on the book.
            results[ticker] = None
            print(f"  {ticker:5} holds {NO_EQUITY_INSIDE[ticker]} — "
                  f"no company inside it to look through to; not fetched")
            continue
        issuer = issuer_of(ticker, instruments_path)
        if writing:
            _record_issuer(ticker, issuer, instruments_path)
        results[ticker] = fetch_holdings(ticker, issuer=issuer,
                                         instruments_path=instruments_path)
        got = results[ticker]
        if got:
            # Both numbers, always. `coverage` is how much of the fund the
            # SOURCE accounted for; `stored` is how much of it is in the
            # company rows actually written. They differ by the cash and the
            # dust, and for a fund that is mostly cash they differ a lot.
            n = len(got["rows"])
            # `stored` is capped at `coverage` for display only. Both are real
            # numbers, but an issuer file that rounds to a hair over 100% has
            # its coverage clamped to 1.0 while the stored rows are not, and
            # "104.9% of the fund, out of the 100.0% its source accounted
            # for" reads as a bug in the arithmetic rather than as rounding.
            shown = min(got["stored"], got["coverage"])
            print(f"  {ticker:5} {n:4} holding{'' if n == 1 else 's'} worth "
                  f"{shown:6.1%} of the fund, out of the "
                  f"{got['coverage']:6.1%} its source accounted for, "
                  f"{'exact' if got['exact'] else 'FLOOR'}  "
                  f"as of {got['as_of']} ({got['as_of_kind']})  "
                  f"({got['source']})")
        else:
            print(f"  {ticker:5} NO HOLDINGS FROM ANY SOURCE")
        if i < len(tickers) - 1:
            time.sleep(1)               # don't hammer a free provider

    report = gap_report(results)
    print(f"\nExact from the issuer: {len(report['exact'])}. "
          f"A floor from Yahoo: {len(report['floor'])}. "
          f"Nothing at all: {len(report['missing'])}.")
    if report["floor"]:
        # Thinnest first, because that is the order in which dropping a file
        # into the inbox changes an answer. A fund whose floor is 3% of it is
        # not wrong, but almost everything in it is still unaccounted for.
        thin = sorted(report["floor"], key=lambda t: results[t]["stored"])
        print("  Floor only — a real number, just not the whole fund "
              "(least of the fund described first, stored/accounted-for):")
        print("    " + ", ".join(
            f"{t} {min(results[t]['stored'], results[t]['coverage']):.1%}"
            f"/{results[t]['coverage']:.1%}" for t in thin))
        print(f"  To make one exact, drop its holdings file into "
              f"{INBOX_DIR.relative_to(PROJECT_ROOT)}/ and re-run. No hurry.")
    if report["missing"]:
        print(f"  ⚠ No source had anything for: {', '.join(report['missing'])}")

    usable = [r for r in results.values() if r]
    if not usable:
        raise SystemExit("\nNothing fetched.")
    if writing:
        out = write(usable, holdings_path,
                    clear={t for t in tickers if t in NO_EQUITY_INSIDE})
        print(f"\nWrote {len(out)} rows to "
              f"{(holdings_path or market.HOLDINGS_PATH).name}")
    else:
        print("\nReport only. Re-run with --write to update the file.")


def _record_issuer(ticker: str, issuer: str,
                   instruments_path: Path | None = None) -> None:
    """Write a guessed issuer down once, and never overwrite a hand correction."""
    path = instruments_path or instruments.INSTRUMENTS_PATH
    df = instruments.load(path).fillna("")
    row = df.loc[df["instrument_id"] == ticker]
    if row.empty or str(row.iloc[0].get("issuer", "")).strip() or not issuer:
        return
    instruments.set_issuer(ticker, issuer, path=path)
    print(f"  {ticker:5} issuer recorded as {issuer} (from its name; correct it "
          f"by hand if wrong)")


if __name__ == "__main__":
    main()
