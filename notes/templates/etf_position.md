# TICKER — Fund Name

<!--
An ETF is a way to own an exposure, not a company. Writing a bear case for a
basket of 2,000 names is filler, so this form is shorter than the stock one
(Yaron's decision, 2026-08-10).

Three of the four questions that matter are claim-row fields, not sections:

    what exposure this gives me   -> the `claim` column
    what would kill that exposure -> the `wrong_if` column
    when I'll look again          -> the `check_date` column

Which usually leaves only the section below. Write it with: /note TICKER

An ETF gets no `sector` in instruments.csv — its exposure is decomposed via
etf_compositions.csv (docs/data-conventions.md).
-->

## Bear case

What breaks this exposure — not the fund, the exposure itself. "Small caps
underperform for another two years because ___", not "the fund could go down".

___

## Catalysts

What you are waiting for. Leave empty if you are holding this as structural
beta rather than a bet on something specific — but say so, because "I hold it
for beta" and "I forgot why I hold it" look identical in a blank section.

- [ ] …

<!--
Optional, and often the only thing worth adding for a wrapper:

  ## Why this wrapper and not another
      the peer you compared it against, and on what — fee, liquidity, holdings
  ## When I'd rotate out of it
-->
