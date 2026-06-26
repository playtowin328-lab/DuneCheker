import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ADDRESS_RE = re.compile(r'0x[a-fA-F0-9]{40}')
ADDRESS_LIKE_RE = re.compile(r'0x[a-zA-Z0-9]{20,80}')


@dataclass
class AddressExtraction:
    addresses: list[str]
    invalid_addresses: list[str]
    duplicates_removed: int = 0
    columns: dict[str, list[str]] | None = None
    selected_column: str | None = None


def normalize_address(addr: str) -> str:
    return addr.strip().lower()


def extract_addresses(text: str) -> AddressExtraction:
    seen: set[str] = set()
    result: list[str] = []
    duplicates = 0

    for match in ADDRESS_RE.finditer(text or ''):
        addr = normalize_address(match.group(0))
        if addr in seen:
            duplicates += 1
            continue
        seen.add(addr)
        result.append(addr)

    invalid_seen: set[str] = set()
    invalid: list[str] = []
    for match in ADDRESS_LIKE_RE.finditer(text or ''):
        raw = normalize_address(match.group(0))
        if ADDRESS_RE.fullmatch(raw) or raw in invalid_seen:
            continue
        invalid_seen.add(raw)
        invalid.append(raw)

    return AddressExtraction(result, invalid, duplicates)


def extract_addresses_from_text(text: str) -> list[str]:
    return extract_addresses(text).addresses


def _extract_from_dataframe(df: pd.DataFrame) -> AddressExtraction:
    df = df.fillna('').astype(str)
    columns: dict[str, list[str]] = {}
    invalid: list[str] = []
    duplicates = 0

    for col in df.columns:
        extracted = extract_addresses('\n'.join(df[col].tolist()))
        if extracted.addresses:
            columns[str(col)] = extracted.addresses
        invalid.extend(extracted.invalid_addresses)
        duplicates += extracted.duplicates_removed

    if columns:
        selected_column = max(columns, key=lambda name: len(columns[name]))
        addresses = columns[selected_column]
    else:
        selected_column = None
        full = extract_addresses('\n'.join(df.agg(' '.join, axis=1).tolist()))
        addresses = full.addresses
        invalid.extend(full.invalid_addresses)
        duplicates += full.duplicates_removed

    return AddressExtraction(
        addresses=addresses,
        invalid_addresses=list(dict.fromkeys(invalid)),
        duplicates_removed=duplicates,
        columns=columns,
        selected_column=selected_column,
    )


def extract_address_details_from_file(path: Path) -> AddressExtraction:
    suffix = path.suffix.lower()
    if suffix in {'.txt', '.log'}:
        return extract_addresses(path.read_text(encoding='utf-8', errors='ignore'))
    if suffix == '.csv':
        return _extract_from_dataframe(pd.read_csv(path, dtype=str, keep_default_na=False, encoding_errors='ignore'))
    if suffix in {'.xlsx', '.xls'}:
        return _extract_from_dataframe(pd.read_excel(path, dtype=str).fillna(''))
    raise ValueError('Supported file formats: TXT, CSV, XLSX')


def extract_addresses_from_file(path: Path) -> list[str]:
    return extract_address_details_from_file(path).addresses


def split_batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]
