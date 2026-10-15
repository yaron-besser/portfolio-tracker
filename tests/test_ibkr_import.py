"""
Tests for the IBKR Flex XML parser and DataFrame extractors.

Uses tests/fixtures/ibkr_flex_sample.xml — a small synthetic fixture covering
the edge cases the real parser must handle (zero-position rows, the trades
section-total row, multi-currency same-symbol holdings).

Run from project root:
    pytest tests/test_ibkr_import.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ibkr_import import (
    extract_borrow_fees,
    extract_cash_positions,
    extract_cash_transactions,
    extract_corporate_actions,
    extract_option_eae,
    extract_nav_history,
    extract_open_positions,
    extract_trades,
    iter_statements,
    parse_xml,
    section_records,
    unparsed_sections,
    _mask_token,
    _to_float,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ibkr_flex_sample.xml"


@pytest.fixture(scope="module")
def stmt():
    """Return the single FlexStatement element from the test fixture."""
    root = parse_xml(FIXTURE_PATH.read_bytes())
    return next(iter_statements(root))


# ─── Low-level helpers ───────────────────────────────────────────────────


def test_to_float_handles_numeric_string() -> None:
    """Normal float strings convert cleanly."""
    assert _to_float("1.5") == 1.5
    assert _to_float("-2.5") == -2.5
    assert _to_float("0") == 0.0


def test_to_float_empty_string_returns_nan() -> None:
    """Empty XML attributes are common; must not crash."""
    import math
    assert math.isnan(_to_float(""))


def test_mask_token_hides_all_but_last_four() -> None:
    """Token must never be printed in full."""
    masked = _mask_token("abcdef1234567890")
    assert masked.endswith("7890")
    assert masked.count("*") == len("abcdef1234567890") - 4
    assert "abcdef" not in masked


def test_mask_token_short_input_fully_masked() -> None:
    """Tokens shorter than 4 chars are entirely masked (no leak)."""
    assert _mask_token("abc") == "***"
    assert _mask_token("") == ""


# ─── Section records ─────────────────────────────────────────────────────


def test_section_records_returns_attrib_dicts(stmt) -> None:
    """section_records flattens XML child elements to attribute dicts."""
    records = section_records(stmt, "OpenPositions")
    assert len(records) == 4
    assert records[0]["symbol"] == "AAPL"
    assert records[0]["currency"] == "USD"


def test_section_records_account_info_is_self(stmt) -> None:
    """AccountInformation has no children — its attributes ARE the record."""
    records = section_records(stmt, "AccountInformation")
    assert len(records) == 1
    assert records[0]["accountId"] == "UTEST"


def test_section_records_missing_section_returns_empty(stmt) -> None:
    """Sections not present in the XML return an empty list, not None/error."""
    assert section_records(stmt, "Transfers") == []


# ─── extract_open_positions ──────────────────────────────────────────────


def test_extract_open_positions_skips_zero_positions(stmt) -> None:
    """Closed lots with position=0 must be dropped."""
    df = extract_open_positions(stmt)
    assert "ZERO" not in df["symbol"].values
    assert len(df) == 3  # AAPL, ASML EUR, ASML USD


def test_extract_open_positions_keeps_multi_currency_same_symbol(stmt) -> None:
    """Same symbol in two currencies must produce two distinct rows."""
    df = extract_open_positions(stmt)
    asml = df[df["symbol"] == "ASML"]
    assert len(asml) == 2
    assert set(asml["currency"]) == {"EUR", "USD"}


def test_extract_open_positions_types_are_numeric(stmt) -> None:
    """Quantity/price/fx columns must be float, not string."""
    df = extract_open_positions(stmt)
    aapl = df[df["symbol"] == "AAPL"].iloc[0]
    assert aapl["position"] == 10.0
    assert aapl["mark_price"] == 200.0
    assert aapl["fx_rate_to_base"] == 1.0
    assert aapl["cost_basis_price"] == 150.0


# ─── extract_cash_positions ──────────────────────────────────────────────


def test_extract_cash_positions_skips_zero_balance(stmt) -> None:
    """Currencies the user no longer holds (qty=0) are dropped."""
    df = extract_cash_positions(stmt)
    assert "DKK" not in df["currency"].values


def test_extract_cash_positions_returns_base_value(stmt) -> None:
    """value_in_base must be the FX-translated USD value."""
    df = extract_cash_positions(stmt)
    eur = df[df["currency"] == "EUR"].iloc[0]
    assert eur["quantity"] == 100.0
    assert eur["close_price"] == 1.1
    assert eur["value_in_base"] == 110.0


# ─── extract_trades ──────────────────────────────────────────────────────


def test_extract_trades_skips_section_total_row(stmt) -> None:
    """The first <Trade> in Trades is a section total — no symbol, no tradeID."""
    df = extract_trades(stmt)
    assert len(df) == 2  # T1 + T2 only; the summary row is dropped
    assert set(df["trade_id"]) == {"T1", "T2"}


def test_extract_trades_preserves_signed_quantity(stmt) -> None:
    """BUY rows have positive qty; SELL rows have negative qty."""
    df = extract_trades(stmt)
    buy = df[df["trade_id"] == "T1"].iloc[0]
    sell = df[df["trade_id"] == "T2"].iloc[0]
    assert buy["quantity"] == 10.0 and buy["buy_sell"] == "BUY"
    assert sell["quantity"] == -2.0 and sell["buy_sell"] == "SELL"


def test_extract_trades_keeps_fifo_pnl_on_close(stmt) -> None:
    """Realized P&L on the closing trade should be the IBKR-reported value."""
    df = extract_trades(stmt)
    sell = df[df["trade_id"] == "T2"].iloc[0]
    assert sell["fifo_pnl"] == 60.0
    assert sell["open_close"] == "C"


# ─── extract_cash_transactions ───────────────────────────────────────────


def test_extract_cash_transactions_skips_empty_type(stmt) -> None:
    """Records with no `type` are skipped (fixture has one such row)."""
    df = extract_cash_transactions(stmt)
    assert len(df) == 4
    assert "" not in df["type"].values


def test_extract_cash_transactions_types_present(stmt) -> None:
    """All four real cash-tx types from the fixture must appear."""
    df = extract_cash_transactions(stmt)
    expected = {"Dividends", "Withholding Tax", "Deposits/Withdrawals",
                "Broker Interest Paid"}
    assert set(df["type"]) == expected


def test_extract_cash_transactions_amount_is_numeric(stmt) -> None:
    """The amount column must be float, not string."""
    df = extract_cash_transactions(stmt)
    deposit = df[df["type"] == "Deposits/Withdrawals"].iloc[0]
    assert deposit["amount"] == 1000.0
    assert isinstance(deposit["amount"], float)


# ─── extract_nav_history ─────────────────────────────────────────────────


def test_extract_nav_history_returns_one_row_per_day(stmt) -> None:
    """Fixture has 5 daily NAV rows."""
    df = extract_nav_history(stmt)
    assert len(df) == 5


def test_extract_nav_history_sorted_ascending(stmt) -> None:
    """Rows must be sorted by date so cum-max drawdown logic works."""
    df = extract_nav_history(stmt)
    assert list(df["date"]) == sorted(df["date"])


def test_extract_nav_history_nav_column_named_correctly(stmt) -> None:
    """The total NAV column must be named `nav` so it works with returns.py."""
    df = extract_nav_history(stmt)
    assert "nav" in df.columns
    assert df.iloc[0]["nav"] == 100.0
    assert df.iloc[-1]["nav"] == 110.0


# ─── extract_borrow_fees ─────────────────────────────────────────────────


def test_extract_borrow_fees_skips_empty_symbol(stmt) -> None:
    """The dummy empty row (no symbol) is dropped."""
    df = extract_borrow_fees(stmt)
    assert len(df) == 2
    assert all(df["symbol"] == "HTBSTK")


def test_extract_borrow_fees_pulls_fee_columns(stmt) -> None:
    """The `borrow_fee` column must be the per-day cost (negative)."""
    df = extract_borrow_fees(stmt)
    row = df.iloc[0]
    assert row["borrow_fee"] == -0.5
    assert row["borrow_fee_rate"] == 5.5
    assert row["value"] == 500.0


# ─── Corporate actions ───────────────────────────────────────────────────
#
# Added 2026-09-29. The section was requested and counted since the importer
# was written, and never extracted — so three splits sat in every raw file,
# unread, while `docs/data-model-open-questions.md` #9 recorded the share
# counts they explain as an unsolved bug (XLK "bought 15, sold 17, holding 8"
# is a 2-for-1 split on 2025-12-05).


def test_extract_corporate_actions_skips_the_empty_row(stmt) -> None:
    """Like Trades, the section carries a row with no transactionID."""
    df = extract_corporate_actions(stmt)
    assert len(df) == 2
    assert "" not in set(df["transaction_id"])


def test_extract_corporate_actions_keeps_the_split_ratio(stmt) -> None:
    """The ratio lives only in the description. Losing it means the row
    records that something happened but not what — which is what made the
    share-count mismatch unexplainable in the first place."""
    df = extract_corporate_actions(stmt)
    split = df[df["symbol"] == "SPLITCO"].iloc[0]
    assert "SPLIT 2 FOR 1" in split["description"]
    assert split["type"] == "FS"
    assert split["report_date"] == "2025-06-05"


def test_extract_corporate_actions_quantity_is_signed(stmt) -> None:
    """A split ADDS shares (+10); a merger removes them (-5). An unsigned
    quantity would make the two indistinguishable."""
    df = extract_corporate_actions(stmt).set_index("symbol")
    assert df.loc["SPLITCO", "quantity"] == 10.0
    assert df.loc["MERGECO", "quantity"] == -5.0


def test_extract_corporate_actions_numeric_columns_are_float(stmt) -> None:
    df = extract_corporate_actions(stmt)
    for col in ("quantity", "proceeds", "value"):
        assert df[col].dtype.kind == "f", f"{col} must be numeric"


# ─── Option exercise / assignment / expiration ───────────────────────────
#
# OPT and FUT positions leave the book through this section, not through
# Trades. Without it an expiry looks like a position that simply vanished.


def test_extract_option_eae_skips_the_empty_row(stmt) -> None:
    """The section's first element carries no attributes at all."""
    df = extract_option_eae(stmt)
    assert len(df) == 2
    assert "" not in set(df["trade_id"])


def test_extract_option_eae_keeps_realized_pnl(stmt) -> None:
    """The only place the P&L of an expiry or assignment is reported."""
    df = extract_option_eae(stmt).set_index("trade_id")
    assert df.loc["OE1", "realized_pnl"] == 120.0
    assert df.loc["OE2", "realized_pnl"] == 150.0


def test_extract_option_eae_covers_futures_not_only_options(stmt) -> None:
    """Despite the section name IBKR reports futures expiries here too — the
    A crude future closed this way, and so do expiring options."""
    df = extract_option_eae(stmt)
    assert set(df["asset_category"]) == {"OPT", "FUT"}


def test_extract_option_eae_keeps_strike_and_expiry(stmt) -> None:
    """A contract without its strike and expiry cannot be matched back to
    the position it closed."""
    df = extract_option_eae(stmt).set_index("trade_id")
    assert df.loc["OE1", "strike"] == 50.0
    assert df.loc["OE1", "expiry"] == "2025-06-20"
    assert df.loc["OE1", "put_call"] == "C"


# ─── What the broker sends that nothing reads ────────────────────────────


def test_unparsed_sections_names_what_is_downloaded_and_ignored(stmt) -> None:
    """His question, 2026-09-29: am I getting too much or too little from
    IBKR? It must stay answerable without re-deriving it by hand, or the
    next session is back to reading raw XML."""
    rows = unparsed_sections(stmt)
    by_name = {r["section"]: r for r in rows}
    assert by_name["ConversionRates"]["records"] == 2
    assert by_name["ConversionRates"]["parsed"] is False
    assert by_name["SLBActivities"]["parsed"] is False


def test_unparsed_sections_marks_the_ones_we_do_read(stmt) -> None:
    """A section we extract must not be reported as waste — otherwise the
    report argues for turning off the data the project runs on."""
    by_name = {r["section"]: r for r in unparsed_sections(stmt)}
    for section in ("Trades", "CashTransactions", "OpenPositions",
                    "CorporateActions", "OptionEAE"):
        assert by_name[section]["parsed"] is True, f"{section} is parsed"


def test_unparsed_sections_omits_sections_not_in_the_file(stmt) -> None:
    """Reports what the broker actually sent, not the wish list. A section
    listed but absent would read as 'costs nothing', which is the opposite
    of the truth — it is missing."""
    names = {r["section"] for r in unparsed_sections(stmt)}
    assert "Transfers" not in names


# ─── The token must never reach an error message ─────────────────────────


def test_a_failing_fetch_never_puts_the_token_in_the_error(monkeypatch):
    """2026-09-29: a DNS failure inside the ibflex library raised a
    ConnectionError whose message contained the full request URL — and the
    Flex token is a query parameter in that URL. It went into a log file and
    into the session transcript, and the token had to be rotated.

    The library builds that URL, so we cannot stop it appearing. We can stop
    it escaping this function.
    """
    from src import ibkr_import as m

    token = "741790719313440939363283"

    def boom(tok, qid):
        raise ConnectionError(
            "HTTPSConnectionPool(host='gdcdyn.interactivebrokers.com', port=443): "
            f"Max retries exceeded with url: /Universal/servlet/"
            f"FlexStatementService.SendRequest?v=3&t={token}&q={qid}"
        )

    # Patch the first call the bounded download makes. Until 2026-10-15 this
    # patched `client.download`, which fetch_flex_xml stopped calling when the
    # polling was capped — so the mock missed and the test made a REAL network
    # call, passing or failing on whatever IBKR happened to answer.
    monkeypatch.setattr(m.client, "request_statement", boom)
    with pytest.raises(Exception) as excinfo:
        m.fetch_flex_xml(token, "9999999")

    whole = repr(excinfo.value) + str(excinfo.value)
    assert token not in whole, "the token leaked into the error message"
    assert "3283" in whole, "the last 4 chars should survive, to identify which token"
    assert "gdcdyn.interactivebrokers.com" in whole, "the real cause must not be hidden"


def test_masking_does_not_destroy_an_exception_with_a_custom_constructor(monkeypatch):
    """ibflex's own ResponseCodeError takes (code, msg). Rebuilding it with a
    single argument raises TypeError inside the handler, so the token is safe
    but the real cause — "Statement could not be generated" — is replaced by a
    confusing TypeError. That is how error 1001 stops being diagnosable.
    """
    from ibflex.client import ResponseCodeError
    from src import ibkr_import as m

    token = "741790719313440939363283"

    def boom(tok, qid):
        raise ResponseCodeError(1001, f"could not generate for t={token}")

    # Patch the first call the bounded download makes. Until 2026-10-15 this
    # patched `client.download`, which fetch_flex_xml stopped calling when the
    # polling was capped — so the mock missed and the test made a REAL network
    # call, passing or failing on whatever IBKR happened to answer.
    monkeypatch.setattr(m.client, "request_statement", boom)
    with pytest.raises(Exception) as excinfo:
        m.fetch_flex_xml(token, "9999999")

    msg = str(excinfo.value)
    assert token not in msg, "token leaked"
    assert "could not generate" in msg, "the real cause must survive"
    assert "ResponseCodeError" in msg or type(excinfo.value).__name__ == "ResponseCodeError", (
        "the original exception type must still be identifiable"
    )
    assert "TypeError" not in msg, "the masking must not itself become the error"


# ─── The fetch is bounded ───────────────────────────────────────────────
#
# Added 2026-10-15 after the third sync in a week went wrong. ibflex's own
# `client.download()` loops `while status is not True: time.sleep(status)`
# with no cap at all, so a busy IBKR means it re-sends every 5-10 seconds
# for ever. On 2026-10-08 that ran 7 minutes before anyone noticed, and
# docs/data-conventions.md is explicit that exactly this pattern turns a
# transient 1001 into a 1025 lockout.

import types  # noqa: E402

from src import ibkr_import  # noqa: E402


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content


def _patch_client(monkeypatch, statuses, *, content=b"<FlexQueryResponse/>"):
    """Drive fetch_flex_xml with a scripted sequence of poll results.

    Each entry is what `check_statement_response` returns: an int is a retry
    delay in seconds, True means the statement arrived.
    """
    calls = {"submit": 0, "slept": 0.0}

    def fake_request_statement(token, query_id):
        return types.SimpleNamespace(Url=None, ReferenceCode="REF123")

    def fake_submit_request(url, token, query):
        calls["submit"] += 1
        return _FakeResponse(content)

    seq = list(statuses)

    def fake_check(response):
        return seq.pop(0) if seq else 5

    def fake_sleep(seconds):
        calls["slept"] += seconds

    monkeypatch.setattr(ibkr_import.client, "request_statement", fake_request_statement)
    monkeypatch.setattr(ibkr_import.client, "submit_request", fake_submit_request)
    monkeypatch.setattr(ibkr_import.client, "check_statement_response", fake_check)
    monkeypatch.setattr(ibkr_import.time, "sleep", fake_sleep)
    return calls


def test_fetch_returns_the_statement_when_ibkr_answers(monkeypatch):
    """The normal path is unchanged: poll until True, return the bytes."""
    calls = _patch_client(monkeypatch, [5, 5, True])
    out = ibkr_import.fetch_flex_xml("tok-0000000000001234", "9999999")
    assert out == b"<FlexQueryResponse/>"
    assert calls["submit"] == 3


def test_fetch_gives_up_after_the_attempt_cap(monkeypatch):
    """A server that is busy for ever must not be polled for ever."""
    calls = _patch_client(monkeypatch, [5] * 50)
    with pytest.raises(RuntimeError, match="never returned the statement"):
        ibkr_import.fetch_flex_xml("tok-0000000000001234", "9999999")
    assert calls["submit"] == ibkr_import.FETCH_MAX_ATTEMPTS


def test_fetch_gives_up_on_the_time_cap_before_the_attempt_cap(monkeypatch):
    """Throttling sleeps 10s a go, so wall-clock runs out first. The cap that
    bites must be whichever comes sooner, not whichever is listed first."""
    calls = _patch_client(monkeypatch, [600] * 50)       # one sleep blows the budget
    with pytest.raises(RuntimeError, match="never returned the statement"):
        ibkr_import.fetch_flex_xml("tok-0000000000001234", "9999999")
    assert calls["submit"] < ibkr_import.FETCH_MAX_ATTEMPTS


def test_the_giving_up_message_says_what_to_do(monkeypatch):
    """A bare timeout invites an immediate retry, which is the thing that
    causes the lockout. The error has to carry the rule."""
    _patch_client(monkeypatch, [5] * 50)
    with pytest.raises(RuntimeError) as err:
        ibkr_import.fetch_flex_xml("tok-0000000000001234", "9999999")
    assert "15 minutes" in str(err.value)


def test_the_token_is_still_masked_when_the_fetch_gives_up(monkeypatch):
    """The 2026-09-29 incident: a live token reached a log file. Whatever new
    failure path exists, it must not reopen that door."""
    token = "tok-0000000000001234"

    def boom(*a, **k):
        raise ConnectionError(f"failed talking to host?t={token}&q=9999999")

    monkeypatch.setattr(ibkr_import.client, "request_statement", boom)
    with pytest.raises(Exception) as err:
        ibkr_import.fetch_flex_xml(token, "9999999")
    assert token not in str(err.value)
    assert "1234" in str(err.value)
