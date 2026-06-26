import asyncio
import time
from typing import Any
import aiohttp


class DuneError(RuntimeError):
    pass


class DuneTransientError(DuneError):
    pass


class DuneClient:
    def __init__(self, api_key: str, timeout_seconds: int = 900, poll_seconds: int = 3):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.base = 'https://api.dune.com/api/v1'

    @property
    def headers(self) -> dict[str, str]:
        return {'X-Dune-API-Key': self.api_key, 'Content-Type': 'application/json'}

    async def execute_query(self, query_id: int, parameters: dict[str, Any]) -> str:
        url = f'{self.base}/query/{query_id}/execute'
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(url, headers=self.headers, json={'query_parameters': parameters}) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    error_cls = DuneTransientError if resp.status in {408, 409, 425, 429, 500, 502, 503, 504} else DuneError
                    raise error_cls(f'Ошибка Execute Query {resp.status}: {data}')
                execution_id = data.get('execution_id')
                if not execution_id:
                    raise DuneError(f'Dune не вернул execution_id: {data}')
                return str(execution_id)

    async def wait_completed(self, execution_id: str) -> None:
        url = f'{self.base}/execution/{execution_id}/status'
        started = time.monotonic()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            while True:
                async with session.get(url, headers=self.headers) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status >= 400:
                        error_cls = DuneTransientError if resp.status in {408, 409, 425, 429, 500, 502, 503, 504} else DuneError
                        raise error_cls(f'Ошибка статуса Dune {resp.status}: {data}')
                    state = data.get('state')
                    if state in {'QUERY_STATE_COMPLETED', 'QUERY_STATE_COMPLETED_PARTIAL'}:
                        return
                    if state in {'QUERY_STATE_FAILED', 'QUERY_STATE_CANCELED', 'QUERY_STATE_EXPIRED'}:
                        raise DuneError(f'Выполнение Dune завершилось состоянием {state}: {data.get("error") or data}')
                if time.monotonic() - started > self.timeout_seconds:
                    raise DuneError('Таймаут Dune: запрос выполнялся слишком долго')
                await asyncio.sleep(self.poll_seconds)

    async def get_results(self, execution_id: str, limit: int = 10000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as session:
            while True:
                url = f'{self.base}/execution/{execution_id}/results?limit={limit}&offset={offset}'
                async with session.get(url, headers=self.headers) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status >= 400:
                        error_cls = DuneTransientError if resp.status in {408, 409, 425, 429, 500, 502, 503, 504} else DuneError
                        raise error_cls(f'Ошибка получения результатов {resp.status}: {data}')
                    batch = data.get('result', {}).get('rows', [])
                    rows.extend(batch)
                    if len(batch) < limit:
                        return rows
                    offset += limit

    async def api_status(self) -> dict[str, Any]:
        status: dict[str, Any] = {'ok': False, 'credits': None, 'details': ''}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            for path in ['/user', '/credits', '/balance', '/usage']:
                url = f'{self.base}{path}'
                try:
                    async with session.get(url, headers=self.headers) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status < 400:
                            status['ok'] = True
                            status['details'] = f'{path}: HTTP {resp.status}'
                            for key in ['credits', 'credit_balance', 'remaining_credits', 'balance']:
                                if isinstance(data, dict) and key in data:
                                    status['credits'] = data[key]
                                    break
                            return status
                        if resp.status in {401, 403}:
                            status['details'] = f'{path}: HTTP {resp.status}'
                            return status
                except Exception as exc:
                    status['details'] = str(exc)
            status['ok'] = True
            status['details'] = 'API key принят, но подробный статус недоступен'
            return status

    async def run_wallet_check(self, query_id: int, addresses: list[str], chains: list[str], token_filter: str) -> list[dict[str, Any]]:
        execution_id = await self.execute_query(query_id, {
            'addresses_text': ','.join(addresses),
            'chains_text': ','.join(chains),
            'token_filter': token_filter,
        })
        await self.wait_completed(execution_id)
        return await self.get_results(execution_id)
