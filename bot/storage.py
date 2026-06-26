import json
import time
from typing import Any

import aiosqlite
import asyncpg

from .config import DATA_DIR


class Storage:
    def __init__(self, database_url: str = ''):
        self.database_url = database_url
        self.pg_pool: asyncpg.Pool | None = None
        self.sqlite: aiosqlite.Connection | None = None
        self.backend = 'sqlite'

    async def connect(self) -> None:
        if self.database_url:
            self.pg_pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=3)
            self.backend = 'postgres'
            await self._pg_execute('''
                CREATE TABLE IF NOT EXISTS bot_kv (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            ''')
            await self._pg_execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    status TEXT NOT NULL,
                    address_count INTEGER DEFAULT 0,
                    chains TEXT DEFAULT '',
                    token_filter TEXT DEFAULT '',
                    result_filter TEXT DEFAULT 'all',
                    min_usd DOUBLE PRECISION DEFAULT 0,
                    addresses_json JSONB DEFAULT '[]'::jsonb,
                    invalid_json JSONB DEFAULT '[]'::jsonb,
                    result_file TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now()
                )
            ''')
            await self._pg_migrate_jobs()
            return

        self.sqlite = await aiosqlite.connect(DATA_DIR / 'bot.sqlite3')
        self.backend = 'sqlite'
        await self.sqlite.execute('CREATE TABLE IF NOT EXISTS bot_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)')
        await self.sqlite.execute('''
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                address_count INTEGER DEFAULT 0,
                chains TEXT DEFAULT '',
                token_filter TEXT DEFAULT '',
                result_filter TEXT DEFAULT 'all',
                min_usd REAL DEFAULT 0,
                addresses_json TEXT DEFAULT '[]',
                invalid_json TEXT DEFAULT '[]',
                result_file TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        await self._sqlite_migrate_jobs()
        await self.sqlite.commit()

    async def _pg_migrate_jobs(self) -> None:
        migrations = [
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS result_filter TEXT DEFAULT 'all'",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS min_usd DOUBLE PRECISION DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS addresses_json JSONB DEFAULT '[]'::jsonb",
            "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS invalid_json JSONB DEFAULT '[]'::jsonb",
        ]
        for query in migrations:
            await self._pg_execute(query)

    async def _sqlite_migrate_jobs(self) -> None:
        assert self.sqlite
        async with self.sqlite.execute('PRAGMA table_info(jobs)') as cur:
            rows = await cur.fetchall()
        existing = {row[1] for row in rows}
        migrations = {
            'result_filter': "ALTER TABLE jobs ADD COLUMN result_filter TEXT DEFAULT 'all'",
            'min_usd': 'ALTER TABLE jobs ADD COLUMN min_usd REAL DEFAULT 0',
            'addresses_json': "ALTER TABLE jobs ADD COLUMN addresses_json TEXT DEFAULT '[]'",
            'invalid_json': "ALTER TABLE jobs ADD COLUMN invalid_json TEXT DEFAULT '[]'",
        }
        for column, query in migrations.items():
            if column not in existing:
                await self.sqlite.execute(query)

    async def close(self) -> None:
        if self.pg_pool:
            await self.pg_pool.close()
        if self.sqlite:
            await self.sqlite.close()

    async def _pg_execute(self, query: str, *args: Any) -> None:
        assert self.pg_pool
        async with self.pg_pool.acquire() as conn:
            await conn.execute(query, *args)

    async def get(self, key: str, default: Any = None) -> Any:
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow('SELECT value FROM bot_kv WHERE key=$1', key)
                return row['value'] if row else default
        assert self.sqlite
        async with self.sqlite.execute('SELECT value FROM bot_kv WHERE key=?', (key,)) as cur:
            row = await cur.fetchone()
            return json.loads(row[0]) if row else default

    async def set(self, key: str, value: Any) -> None:
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO bot_kv(key, value, updated_at) VALUES($1, $2::jsonb, now())
                    ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()
                ''', key, json.dumps(value, ensure_ascii=False))
            return
        assert self.sqlite
        now = int(time.time())
        await self.sqlite.execute('INSERT OR REPLACE INTO bot_kv(key,value,updated_at) VALUES(?,?,?)', (key, json.dumps(value, ensure_ascii=False), now))
        await self.sqlite.commit()

    async def delete_many(self, keys: list[str]) -> None:
        if not keys:
            return
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute('DELETE FROM bot_kv WHERE key = ANY($1::text[])', keys)
            return
        assert self.sqlite
        await self.sqlite.executemany('DELETE FROM bot_kv WHERE key=?', [(key,) for key in keys])
        await self.sqlite.commit()

    async def create_job(
        self,
        user_id: int,
        addresses: list[str],
        invalid_addresses: list[str],
        chains: list[str],
        token_filter: str,
        result_filter: str,
        min_usd: float,
    ) -> int:
        addresses_json = json.dumps(addresses, ensure_ascii=False)
        invalid_json = json.dumps(invalid_addresses, ensure_ascii=False)
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow('''
                    INSERT INTO jobs(user_id,status,address_count,chains,token_filter,result_filter,min_usd,addresses_json,invalid_json)
                    VALUES($1,'queued',$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb) RETURNING id
                ''', user_id, len(addresses), ','.join(chains), token_filter, result_filter, min_usd, addresses_json, invalid_json)
                return int(row['id'])
        assert self.sqlite
        now = int(time.time())
        cur = await self.sqlite.execute('''
            INSERT INTO jobs(user_id,status,address_count,chains,token_filter,result_filter,min_usd,addresses_json,invalid_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ''', (user_id, 'queued', len(addresses), ','.join(chains), token_filter, result_filter, min_usd, addresses_json, invalid_json, now, now))
        await self.sqlite.commit()
        return int(cur.lastrowid)

    async def update_job(self, job_id: int, status: str, result_file: str = '', error: str = '') -> None:
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                await conn.execute('UPDATE jobs SET status=$1,result_file=COALESCE(NULLIF($2, \'\'), result_file),error=$3,updated_at=now() WHERE id=$4', status, result_file, error[:2000], job_id)
            return
        assert self.sqlite
        now = int(time.time())
        if result_file:
            await self.sqlite.execute('UPDATE jobs SET status=?,result_file=?,error=?,updated_at=? WHERE id=?', (status, result_file, error[:2000], now, job_id))
        else:
            await self.sqlite.execute('UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?', (status, error[:2000], now, job_id))
        await self.sqlite.commit()

    async def get_job(self, user_id: int, job_id: int) -> dict[str, Any] | None:
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                row = await conn.fetchrow('SELECT * FROM jobs WHERE user_id=$1 AND id=$2', user_id, job_id)
                return self._decode_job(dict(row)) if row else None
        assert self.sqlite
        async with self.sqlite.execute('SELECT * FROM jobs WHERE user_id=? AND id=?', (user_id, job_id)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in cur.description]
            return self._decode_job(dict(zip(cols, row)))

    async def recent_jobs(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        if self.pg_pool:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch('SELECT * FROM jobs WHERE user_id=$1 ORDER BY id DESC LIMIT $2', user_id, limit)
                return [self._decode_job(dict(r)) for r in rows]
        assert self.sqlite
        async with self.sqlite.execute('SELECT * FROM jobs WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit)) as cur:
            rows = await cur.fetchall()
            cols = [desc[0] for desc in cur.description]
            return [self._decode_job(dict(zip(cols, r))) for r in rows]

    def _decode_job(self, job: dict[str, Any]) -> dict[str, Any]:
        for field in ['addresses_json', 'invalid_json']:
            value = job.get(field)
            if isinstance(value, str):
                try:
                    job[field] = json.loads(value)
                except Exception:
                    job[field] = []
            elif value is None:
                job[field] = []
        return job
