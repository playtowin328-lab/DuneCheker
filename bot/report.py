from datetime import datetime
from pathlib import Path

import pandas as pd

EXPECTED_COLUMNS = ['address', 'blockchain', 'token_symbol', 'token_address', 'balance', 'balance_usd']


def _prepare_rows(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=EXPECTED_COLUMNS)
    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[EXPECTED_COLUMNS + [c for c in df.columns if c not in EXPECTED_COLUMNS]]
    for col in ['balance', 'balance_usd']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['address'] = df['address'].astype(str).str.lower()
    return df.sort_values('balance_usd', ascending=False, na_position='last')


def _filtered_balance_rows(df: pd.DataFrame, result_filter: str, min_usd: float) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    filtered = df[df['balance'].fillna(0) > 0].copy()
    if result_filter == 'min_usd':
        filtered = filtered[filtered['balance_usd'].fillna(0) >= min_usd].copy()
    return filtered.sort_values('balance_usd', ascending=False, na_position='last')


def _by_wallet(df: pd.DataFrame, requested_addresses: list[str]) -> pd.DataFrame:
    base = pd.DataFrame({'address': [address.lower() for address in requested_addresses]})
    if df.empty:
        base['total_usd'] = 0.0
        base['token_rows'] = 0
        base['chains'] = ''
        base['top_token'] = ''
        return base

    work = df.copy()
    work['balance_usd'] = work['balance_usd'].fillna(0)
    grouped = work.groupby('address', as_index=False).agg(
        total_usd=('balance_usd', 'sum'),
        token_rows=('token_symbol', 'count'),
        chains=('blockchain', lambda values: ','.join(sorted({str(v) for v in values if str(v)}))),
    )
    top_rows = work.sort_values('balance_usd', ascending=False).drop_duplicates('address')
    top_rows = top_rows[['address', 'token_symbol']].rename(columns={'token_symbol': 'top_token'})
    grouped = grouped.merge(top_rows, on='address', how='left')
    result = base.merge(grouped, on='address', how='left')
    result['total_usd'] = result['total_usd'].fillna(0)
    result['token_rows'] = result['token_rows'].fillna(0).astype(int)
    result['chains'] = result['chains'].fillna('')
    result['top_token'] = result['top_token'].fillna('')
    return result.sort_values('total_usd', ascending=False, na_position='last')


def _by_chain(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=['blockchain', 'total_usd', 'wallets', 'token_rows'])
    work = df.copy()
    work['balance_usd'] = work['balance_usd'].fillna(0)
    return work.groupby('blockchain', as_index=False).agg(
        total_usd=('balance_usd', 'sum'),
        wallets=('address', 'nunique'),
        token_rows=('token_symbol', 'count'),
    ).sort_values('total_usd', ascending=False)


def make_excel(
    rows: list[dict],
    requested_addresses: list[str],
    invalid_addresses: list[str],
    out_dir: Path,
    job_id: int,
    result_filter: str = 'all',
    min_usd: float = 0.0,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    path = out_dir / f'wallet_check_job_{job_id}_{ts}.xlsx'

    df = _prepare_rows(rows)
    with_balance = _filtered_balance_rows(df, result_filter, min_usd)
    by_wallet = _by_wallet(df, requested_addresses)
    by_chain = _by_chain(df)
    top_wallets = by_wallet[by_wallet['total_usd'].fillna(0) > 0].head(100).copy()
    positive_addresses = set(df.loc[df['balance'].fillna(0) > 0, 'address'].str.lower()) if not df.empty else set()
    empty_addresses = [a for a in requested_addresses if a.lower() not in positive_addresses]

    summary = pd.DataFrame([{
        'job_id': job_id,
        'checked_addresses': len(requested_addresses),
        'addresses_with_balance': len(positive_addresses),
        'empty_addresses': len(empty_addresses),
        'invalid_addresses': len(invalid_addresses),
        'rows_all_results': len(df),
        'rows_with_balance_sheet': len(with_balance),
        'wallet_rows': len(by_wallet),
        'top_wallets': len(top_wallets),
        'result_filter': result_filter,
        'min_usd': min_usd,
        'total_usd_all_results': float(df['balance_usd'].fillna(0).sum()) if not df.empty else 0.0,
        'total_usd_with_balance_sheet': float(with_balance['balance_usd'].fillna(0).sum()) if not with_balance.empty else 0.0,
        'generated_utc': datetime.utcnow().isoformat(timespec='seconds'),
    }])

    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All results')
        with_balance.to_excel(writer, index=False, sheet_name='With balance')
        by_wallet.to_excel(writer, index=False, sheet_name='By wallet')
        by_chain.to_excel(writer, index=False, sheet_name='By chain')
        top_wallets.to_excel(writer, index=False, sheet_name='Top wallets')
        pd.DataFrame({'address': empty_addresses}).to_excel(writer, index=False, sheet_name='Empty')
        pd.DataFrame({'address': invalid_addresses}).to_excel(writer, index=False, sheet_name='Invalid addresses')
        summary.to_excel(writer, index=False, sheet_name='Summary')
        for sheet in writer.book.worksheets:
            sheet.freeze_panes = 'A2'
            for column_cells in sheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in column_cells)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 55)
    return path
