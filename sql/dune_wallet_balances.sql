-- DuneSQL для Telegram Dune Wallet Checker v3 Pro.
-- Query Parameters в Dune:
-- addresses_text  Text  пример: 0xabc...,0xdef...
-- chains_text     Text  пример: ethereum,base,arbitrum,polygon,optimism,bnb
-- token_filter    Text  значения: all / native / stables
--
-- Важно: названия curated таблиц Dune могут отличаться в зависимости от доступов/версии Dune.
-- Если конкретная сеть не компилируется, временно убери её UNION-блок.

WITH input_addresses AS (
    SELECT lower(trim(x)) AS address
    FROM unnest(split('{{addresses_text}}', ',')) AS t(x)
    WHERE trim(x) <> ''
),
selected_chains AS (
    SELECT lower(trim(x)) AS blockchain
    FROM unnest(split('{{chains_text}}', ',')) AS t(x)
),
all_balances AS (
    SELECT 'ethereum' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_ethereum.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'ethereum' IN (SELECT blockchain FROM selected_chains)

    UNION ALL
    SELECT 'base' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_base.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'base' IN (SELECT blockchain FROM selected_chains)

    UNION ALL
    SELECT 'arbitrum' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_arbitrum.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'arbitrum' IN (SELECT blockchain FROM selected_chains)

    UNION ALL
    SELECT 'polygon' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_polygon.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'polygon' IN (SELECT blockchain FROM selected_chains)

    UNION ALL
    SELECT 'optimism' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_optimism.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'optimism' IN (SELECT blockchain FROM selected_chains)

    UNION ALL
    SELECT 'bnb' AS blockchain, address, token_address, token_symbol, balance, balance_usd
    FROM balances_bnb.latest
    WHERE lower(cast(address AS varchar)) IN (SELECT address FROM input_addresses)
      AND 'bnb' IN (SELECT blockchain FROM selected_chains)
)
SELECT
    lower(cast(address AS varchar)) AS address,
    blockchain,
    token_symbol,
    lower(cast(token_address AS varchar)) AS token_address,
    balance,
    balance_usd
FROM all_balances
WHERE coalesce(balance, 0) > 0
  AND (
    '{{token_filter}}' = 'all'
    OR ('{{token_filter}}' = 'native' AND lower(token_symbol) IN ('eth','matic','bnb'))
    OR ('{{token_filter}}' = 'stables' AND upper(token_symbol) IN ('USDT','USDC','DAI','USDE','FDUSD','TUSD'))
  )
ORDER BY balance_usd DESC NULLS LAST, blockchain, token_symbol;
