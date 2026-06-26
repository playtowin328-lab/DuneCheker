# Dune Wallet Checker Bot v3 Pro

Telegram bot for checking public EVM wallet balances through Dune API.

The bot is designed for owner-only use: upload TXT/CSV/XLSX files, select chains and token mode, queue a Dune job, then download a formatted Excel report.

## v3 Pro features

- Admin panel with Dune API status, masked API key, current Query ID, owner ID, storage backend, limits, reset actions.
- Smart upload for TXT, CSV, XLSX and XLS files.
- Auto-detect address column in spreadsheets and allow manual column selection.
- Deduplicate addresses and collect invalid `0x...`-like values.
- Modes: native balances, stablecoins, all tokens.
- Result filters: all wallets, only with balance, only above a USD amount.
- Excel report with sheets: `All results`, `With balance`, `Empty`, `Invalid addresses`, `Summary`.
- Job queue with statuses: `queued`, `running`, `done`, `error`.
- History with repeat and download actions.
- Owner access, key masking, key-message deletion attempt, address limit, anti-spam cooldown.
- SQLite locally, PostgreSQL on Railway/VPS.
- Dockerfile and docker-compose included.

## Local Windows run

```bat
copy .env.example .env
notepad .env
start_windows.bat
```

Required variables:

```env
BOT_TOKEN=your_telegram_bot_token
OWNER_USER_ID=your_telegram_id
```

You can set `DUNE_API_KEY` and `DUNE_QUERY_ID` in `.env`, or set them later from the Telegram admin panel.

## Local Linux / VPS run

```bash
cp .env.example .env
nano .env
chmod +x start_linux.sh
./start_linux.sh
```

## Docker Compose

```bash
cp .env.example .env
nano .env
docker compose up -d --build
```

Compose starts:

- `bot`
- `postgres`

Results are stored in `./results`.

## Railway deploy

1. Push this repository to GitHub.
2. Railway -> New Project -> Deploy from GitHub repo.
3. Add a PostgreSQL service.
4. Set variables in the bot service:

```env
BOT_TOKEN=your_telegram_bot_token
OWNER_USER_ID=your_telegram_id
MAX_ADDRESSES_PER_JOB=3000
DUNE_TIMEOUT_SECONDS=900
DUNE_POLL_SECONDS=3
JOB_CONCURRENCY=1
```

5. Railway should provide `DATABASE_URL` from PostgreSQL. If it does not, link it manually from PostgreSQL variables.
6. Deploy/restart.
7. Open Telegram and send `/start`.

## Dune setup

1. Open `sql/dune_wallet_balances.sql`.
2. Create a new Dune query with that SQL.
3. Add query parameters:
   - `addresses_text` as Text
   - `chains_text` as Text
   - `token_filter` as Text
4. Save the query.
5. Copy the Query ID from the URL, for example `https://dune.com/queries/1234567/...`.
6. Set the Query ID in the Telegram admin panel.

## Usage

1. `/start`
2. Open Admin panel and set Dune API key plus Query ID.
3. Choose `Check addresses`.
4. Send text or upload TXT/CSV/XLSX.
5. If multiple columns contain addresses, choose the right column.
6. Choose chains.
7. Choose token mode.
8. Choose result filter.
9. Run the check.
10. Download the Excel report.

## Important notes

- Send only public wallet addresses. Never send seed phrases, private keys, passwords or exchange API keys.
- Dune curated table names can change. If a specific network table fails, update or remove that `UNION ALL` block in `sql/dune_wallet_balances.sql`.
- `All results` contains rows returned by Dune. The provided SQL returns positive balances only, so empty wallets are inferred from missing addresses.
- On Railway/VPS use PostgreSQL so settings and history survive restarts.

## Project structure

```text
bot/
  address_utils.py
  config.py
  dune_client.py
  keyboards.py
  main.py
  report.py
  storage.py
sql/
  dune_wallet_balances.sql
Dockerfile
docker-compose.yml
requirements.txt
start_linux.sh
start_windows.bat
```
