# IBKR Flex Query setup

A one-time setup that lets the project pull your positions and trades from Interactive Brokers automatically. After this is done, you'll never edit `data/positions.csv` by hand again.

The command you actually run afterwards is `venv/bin/python -m src.ibkr_sync`. (`src/ibkr_import.py` is the layer underneath it that talks to IBKR — this page used to send you to that one by mistake.)

You need to set up **two things** in IBKR's web portal:

1. A **Flex Query template** — tells IBKR what fields you want (positions, trades, cash). Gives you a **Query ID** (a number, safe to share).
2. A **Flex Web Service token** — gives the script read-only access to fetch that query. Stays in your local `.env` file, never shared.

Then you put both into your `.env` file on your Mac, and the importer can talk to IBKR.

---

## Part A — Create the Flex Query

### A.1. Log into Client Portal

- Go to <https://www.interactivebrokers.com>
- Click **Login** (top right)
- Choose **Client Portal** (the web portal, not TWS)
- Log in with your usual IBKR credentials and 2FA

### A.2. Find Flex Queries

The exact menu path has changed over IBKR's UI updates. Look in this order:

1. Click the **hamburger menu** (≡) or your **user icon** (top right)
2. Look for **Performance & Reports** or **Reports**
3. Then **Flex Queries** (or "Statements" → "Flex Queries")

If you can't find it, search "Flex Queries" in the portal's search bar.

### A.3. Create an Activity Flex Query

- You'll see tabs for "Activity Flex Query" and "Trade Confirmation Flex Query." Pick **Activity Flex Query** (more complete).
- Click the **+** or **New** button.
- Give it a name like `Portfolio Tracker`.
- Format: **XML** (the importer parses XML, not CSV).
- Period: pick **Last 365 Calendar Days** (or **Year to Date**) — gives us trade history. We can change this later.

### A.4. Select sections and fields

You'll see a long list of "sections" (Account Information, Open Positions, Trades, etc.). Check at least:

| Section | Why we need it |
|---------|----------------|
| Account Information | Account metadata, base currency |
| **Open Positions** | Current holdings — overwrites `data/positions.csv` |
| **Trades** | Buy/sell history — feeds `data/transactions.csv` |
| Cash Report | Your cash balances per currency |
| NAV in Base Currency | Daily NAV history for `data/nav_history.csv` |

For each section, click into it and select fields. Helpful fields per section:

**Open Positions:**
- Symbol, Description, Currency, AssetCategory
- Position (shares), MarkPrice, PositionValue, CostBasisPrice

**Trades:**
- Symbol, CurrencyPrimary, AssetCategory
- DateTime (or TradeDate), Buy/Sell, Quantity, TradePrice, TradeMoney, IBCommission

**Cash Report:**
- CurrencyPrimary, EndingCash, EndingSettledCash

If you're unsure, just check **all available fields** for each section — extra fields don't hurt; we'll ignore what we don't use.

### A.5. Save and copy the Query ID

- Click **Save** at the bottom.
- IBKR will show you the saved query in a list with a number next to it. **That number is your Query ID.** Copy it.
- (You can share this with me — it's not sensitive.)

---

## Part B — Generate the Flex Web Service token

The token is what authorizes the importer to actually pull the query data.

### B.1. Find the Flex Web Service settings

Again, navigation varies, but look in this order:

1. **Settings** (gear icon, top right)
2. Or **Account Settings** → **Configuration** → **API**
3. Look for **Flex Web Service**

### B.2. Enable and generate

- Toggle Flex Web Service to **enabled**.
- Click **Generate Token** (or "Request Token").
- IBKR will show you a long token string — **copy it immediately**. It's typically shown only once.
- (Treat it like a password. Never paste it in chat. We'll put it in your local `.env` only.)

---

## Part C — Save credentials in `.env`

On your Mac, in the project root (`~/Documents/portfolio-tracker`):

1. Create a file called `.env` in the project root. In Terminal:
   ```
   touch .env
   ```
2. Open `.env` in your editor (it's just text).
3. Add these three lines, filling in your own values:
   ```
   IBKR_FLEX_TOKEN=<the long token from Part B>
   IBKR_FLEX_QUERY_ID=<the Query ID from Part A>
   ALPHA_VANTAGE_API_KEY=<free key from alphavantage.co/support/#api-key>
   ```
4. Save and close.

That is the complete list — every key the live code reads:

| Variable | Used by |
|---|---|
| `IBKR_FLEX_TOKEN`, `IBKR_FLEX_QUERY_ID` | `src/ibkr_import.py` — fetching the broker statement |
| `ALPHA_VANTAGE_API_KEY` | `src/positions_fundamentals.py` — company fundamentals |

Leave `ALPHA_VANTAGE_API_KEY` out and the broker sync still works; only
`positions_fundamentals` will stop, with a message saying which key is missing.

`.env` is gitignored — your keys never leave your machine. **Never paste a key
into chat.** If one is ever pasted somewhere it shouldn't be, revoke it at the
provider and generate a new one.

---

## What success looks like

After you complete all three parts, you should have:

- ✅ A saved Flex Query in IBKR's portal with a Query ID
- ✅ A Flex Web Service token (kept private)
- ✅ A `.env` file on your machine with both values filled in

Tell me the Query ID — **only that; keep the token to yourself** — and I'll check the connection.

(This page used to end by offering to *build* the importer. It was built on 2026-05-17 and has run hundreds of times since.)

---

## Troubleshooting

**Can't find Flex Queries in the menu?**
Take a screenshot of your portal home page and paste it in chat. I'll help you navigate.

**"Permission denied" or section grayed out?**
Some Flex sections require account-level permissions. The basic ones we need (Open Positions, Trades) should be available to any account.

**Token shown only once and I missed it?**
You can regenerate. Each new token invalidates the previous one — so do this once you're ready to save it.
