import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / 'data'
RESULTS_DIR = ROOT_DIR / 'results'
DATA_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)


def _parse_int_set(value: str) -> set[int]:
    result: set[int] = set()
    for part in (value or '').split(','):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_user_id: int | None
    allowed_user_ids: set[int]
    database_url: str
    dune_api_key: str
    dune_query_id: str
    max_addresses_per_job: int
    dune_timeout_seconds: int
    dune_poll_seconds: int
    job_concurrency: int
    dune_batch_size: int
    dune_max_retries: int
    dune_retry_delay_seconds: int


def load_settings() -> Settings:
    bot_token = os.getenv('BOT_TOKEN', '').strip()
    if not bot_token:
        raise RuntimeError('BOT_TOKEN is empty. Add it to Railway Variables or .env')
    owner_raw = os.getenv('OWNER_USER_ID', '').strip()
    return Settings(
        bot_token=bot_token,
        owner_user_id=int(owner_raw) if owner_raw.isdigit() else None,
        allowed_user_ids=_parse_int_set(os.getenv('ALLOWED_USER_IDS', '')),
        database_url=os.getenv('DATABASE_URL', '').strip(),
        dune_api_key=os.getenv('DUNE_API_KEY', '').strip(),
        dune_query_id=os.getenv('DUNE_QUERY_ID', '').strip(),
        max_addresses_per_job=int(os.getenv('MAX_ADDRESSES_PER_JOB', '3000')),
        dune_timeout_seconds=int(os.getenv('DUNE_TIMEOUT_SECONDS', '900')),
        dune_poll_seconds=int(os.getenv('DUNE_POLL_SECONDS', '3')),
        job_concurrency=max(1, int(os.getenv('JOB_CONCURRENCY', '1'))),
        dune_batch_size=max(1, int(os.getenv('DUNE_BATCH_SIZE', '500'))),
        dune_max_retries=max(1, int(os.getenv('DUNE_MAX_RETRIES', '3'))),
        dune_retry_delay_seconds=max(1, int(os.getenv('DUNE_RETRY_DELAY_SECONDS', '10'))),
    )
