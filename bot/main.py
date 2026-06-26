import asyncio
import logging
import tempfile
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from .address_utils import extract_address_details_from_file, extract_addresses
from .config import RESULTS_DIR, load_settings
from .dune_client import DuneClient, DuneError
from .keyboards import (
    CHAINS,
    admin_keyboard,
    back_keyboard,
    chains_keyboard,
    column_keyboard,
    confirm_keyboard,
    jobs_keyboard,
    main_menu,
    result_filter_keyboard,
    settings_keyboard,
    token_keyboard,
)
from .report import make_excel
from .storage import Storage

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('wallet-checker')

settings = load_settings()
storage = Storage(settings.database_url)
bot = Bot(settings.bot_token)
dp = Dispatcher()
job_sem = asyncio.Semaphore(settings.job_concurrency)
last_job_start: dict[int, float] = {}


class CheckFlow(StatesGroup):
    waiting_addresses = State()
    selecting_column = State()
    selecting_chains = State()
    selecting_token = State()
    selecting_filter = State()
    waiting_min_usd = State()
    ready = State()


class SettingsFlow(StatesGroup):
    waiting_dune_key = State()
    waiting_query_id = State()
    waiting_max_limit = State()


async def get_owner_id() -> int | None:
    saved = await storage.get('owner_user_id')
    if saved:
        try:
            return int(saved)
        except Exception:
            return None
    if settings.owner_user_id:
        await storage.set('owner_user_id', settings.owner_user_id)
        return settings.owner_user_id
    return None


async def allowed(user_id: int) -> bool:
    owner = await get_owner_id()
    if owner:
        return user_id == owner or user_id in settings.allowed_user_ids
    if not settings.allowed_user_ids:
        await storage.set('owner_user_id', user_id)
        return True
    return user_id in settings.allowed_user_ids


async def deny_if_needed(event: Message | CallbackQuery) -> bool:
    user_id = event.from_user.id
    if await allowed(user_id):
        return False
    text = 'Access denied. Ask the owner to add your Telegram ID to OWNER_USER_ID or ALLOWED_USER_IDS.'
    if isinstance(event, Message):
        await event.answer(text)
    else:
        await event.answer(text, show_alert=True)
    return True


async def dune_api_key() -> str:
    return (await storage.get('dune_api_key')) or settings.dune_api_key


async def dune_query_id() -> int | None:
    value = (await storage.get('dune_query_id')) or settings.dune_query_id
    return int(value) if str(value).isdigit() else None


async def max_limit() -> int:
    value = await storage.get('max_addresses_per_job')
    try:
        return int(value or settings.max_addresses_per_job)
    except Exception:
        return settings.max_addresses_per_job


async def is_configured() -> bool:
    return bool(await dune_api_key() and await dune_query_id())


def mask_secret(value: str) -> str:
    if not value:
        return 'not set'
    if len(value) <= 10:
        return value[:2] + '****'
    return f'{value[:6]}******{value[-4:]}'


async def admin_text(refresh_dune: bool = False) -> str:
    owner = await get_owner_id()
    key = await dune_api_key()
    qid = await dune_query_id()
    status = 'not configured'
    credits = 'unknown'
    if key and refresh_dune:
        result = await DuneClient(key, settings.dune_timeout_seconds, settings.dune_poll_seconds).api_status()
        status = 'online' if result.get('ok') else 'error'
        credits = str(result.get('credits') if result.get('credits') is not None else result.get('details') or 'unknown')
    elif key:
        status = 'configured'
    return (
        '<b>Admin panel</b>\n\n'
        f'Dune API status: <code>{status}</code>\n'
        f'Credits / limits: <code>{credits}</code>\n'
        f'Current Query ID: <code>{qid or "not set"}</code>\n'
        f'Bot owner: <code>{owner or "not set"}</code>\n'
        f'Storage: <code>{storage.backend}</code>\n'
        f'Dune API key: <code>{mask_secret(key)}</code>\n'
        f'Address limit: <code>{await max_limit()}</code>\n'
        f'Job concurrency: <code>{settings.job_concurrency}</code>'
    )


@dp.startup()
async def on_startup() -> None:
    await storage.connect()
    if settings.owner_user_id:
        await storage.set('owner_user_id', settings.owner_user_id)
    if settings.dune_api_key and not await storage.get('dune_api_key'):
        await storage.set('dune_api_key', settings.dune_api_key)
    if settings.dune_query_id and not await storage.get('dune_query_id'):
        await storage.set('dune_query_id', settings.dune_query_id)
    log.info('Bot started. Storage backend: %s', storage.backend)


@dp.shutdown()
async def on_shutdown() -> None:
    await storage.close()


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    await state.clear()
    await message.answer(
        '<b>Dune Wallet Checker v3 Pro</b>\n\n'
        'Upload TXT, CSV or XLSX with public EVM addresses. The bot removes duplicates, validates addresses, runs Dune, and returns an Excel report.\n\n'
        'Do not send seed phrases, private keys, passwords or exchange trading keys.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command('help'))
async def cmd_help(message: Message) -> None:
    await message.answer(
        '<b>Commands</b>\n\n'
        '/start - menu\n'
        '/help - help\n\n'
        'Flow: Admin panel -> Dune API key -> Query ID -> upload addresses -> chains -> mode -> Excel.',
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == 'menu')
async def menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text('<b>Main menu</b>', reply_markup=main_menu(await is_configured()), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data.in_({'settings', 'admin'}))
async def cb_admin(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(call):
        return
    await state.clear()
    await call.message.edit_text(await admin_text(refresh_dune=True), reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data == 'guide')
async def cb_guide(call: CallbackQuery) -> None:
    await call.message.edit_text(
        '<b>Railway / VPS deploy</b>\n\n'
        '1. Push this repository to GitHub.\n'
        '2. In Railway create a new project from the GitHub repo.\n'
        '3. Add PostgreSQL and set BOT_TOKEN, OWNER_USER_ID, MAX_ADDRESSES_PER_JOB.\n'
        '4. Open sql/dune_wallet_balances.sql in Dune, save it as a query, then put its Query ID into the bot admin panel.\n'
        '5. Restart the Railway service and open /start in Telegram.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.callback_query(F.data == 'security')
async def cb_security(call: CallbackQuery) -> None:
    await call.message.edit_text(
        '<b>Security</b>\n\n'
        '- The bot accepts only public wallet addresses.\n'
        '- API keys are displayed only as a mask.\n'
        '- Messages with Dune keys are deleted when Telegram allows it.\n'
        '- Access is limited to the owner and allowed users.\n'
        '- Address limits and a short anti-spam cooldown protect the queue.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.callback_query(F.data == 'set:dune_key')
async def cb_set_dune_key(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_dune_key)
    await call.message.edit_text('Send Dune API key in one message. I will try to delete that message after saving.', reply_markup=back_keyboard())
    await call.answer()


@dp.message(SettingsFlow.waiting_dune_key, F.text)
async def receive_dune_key(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    key = (message.text or '').strip()
    if len(key) < 20:
        await message.answer('The key looks too short. Check it and send again.')
        return
    await storage.set('dune_api_key', key)
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer('Dune API key saved. It will be shown only as a mask.', reply_markup=settings_keyboard())


@dp.callback_query(F.data == 'set:query_id')
async def cb_set_query_id(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_query_id)
    await call.message.edit_text('Send Dune Query ID as a number. Example: <code>1234567</code>', reply_markup=back_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.message(SettingsFlow.waiting_query_id, F.text)
async def receive_query_id(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    value = (message.text or '').strip()
    if not value.isdigit():
        await message.answer('Query ID must be a number.')
        return
    await storage.set('dune_query_id', value)
    await state.clear()
    await message.answer(f'Query ID saved: <code>{value}</code>', reply_markup=settings_keyboard(), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'set:max_limit')
async def cb_set_max_limit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_max_limit)
    await call.message.edit_text('Send address limit per job. Recommended: 500-3000. Maximum: 10000.', reply_markup=back_keyboard())
    await call.answer()


@dp.message(SettingsFlow.waiting_max_limit, F.text)
async def receive_max_limit(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    value = (message.text or '').strip()
    if not value.isdigit() or not (1 <= int(value) <= 10000):
        await message.answer('Enter a number from 1 to 10000.')
        return
    await storage.set('max_addresses_per_job', int(value))
    await state.clear()
    await message.answer(f'Limit saved: <code>{value}</code>', reply_markup=settings_keyboard(), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'clear:dune_key')
async def cb_clear_dune_key(call: CallbackQuery) -> None:
    await storage.set('dune_api_key', '')
    await call.message.edit_text('Dune API key removed.', reply_markup=settings_keyboard())
    await call.answer()


@dp.callback_query(F.data == 'settings:reset')
async def cb_reset_settings(call: CallbackQuery) -> None:
    await storage.delete_many(['dune_api_key', 'dune_query_id', 'max_addresses_per_job'])
    await call.message.edit_text('Runtime settings reset. OWNER_USER_ID from environment is kept.', reply_markup=admin_keyboard())
    await call.answer()


@dp.callback_query(F.data == 'test:dune')
async def cb_test_dune(call: CallbackQuery) -> None:
    key = await dune_api_key()
    qid = await dune_query_id()
    if not key or not qid:
        await call.answer('Set Dune API key and Query ID first', show_alert=True)
        return
    await call.message.edit_text('Running a short Dune test with a zero address...', parse_mode=ParseMode.HTML)
    try:
        client = DuneClient(key, timeout_seconds=settings.dune_timeout_seconds, poll_seconds=settings.dune_poll_seconds)
        await client.run_wallet_check(qid, ['0x0000000000000000000000000000000000000000'], ['ethereum'], 'native')
        await call.message.answer('Dune API key and Query ID work.', reply_markup=settings_keyboard())
    except Exception as e:
        await call.message.answer(f'Test failed:\n<code>{str(e)[:1500]}</code>', reply_markup=settings_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data == 'check:start')
async def cb_check_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_configured():
        await call.answer('Set Dune API key and Query ID first', show_alert=True)
        return
    await state.set_state(CheckFlow.waiting_addresses)
    await call.message.edit_text(
        '<b>Upload addresses</b>\n\n'
        'Send addresses as text or upload TXT, CSV, XLSX. I will detect the address column, remove duplicates, and validate EVM addresses.',
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.message(CheckFlow.waiting_addresses)
async def receive_addresses(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    try:
        if message.document:
            suffix = Path(message.document.file_name or 'addresses.txt').suffix.lower()
            if suffix not in {'.txt', '.log', '.csv', '.xlsx', '.xls'}:
                await message.answer('Send TXT, CSV or XLSX file.')
                return
            with tempfile.TemporaryDirectory() as tmp:
                file_path = Path(tmp) / (message.document.file_name or 'addresses.txt')
                await bot.download(message.document, destination=file_path)
                extraction = extract_address_details_from_file(file_path)
        else:
            extraction = extract_addresses(message.text or '')
    except Exception as e:
        await message.answer(f'Could not read file: <code>{str(e)[:1000]}</code>', parse_mode=ParseMode.HTML)
        return

    addresses = extraction.addresses
    if not addresses:
        await message.answer('No valid addresses found. Expected format: <code>0x</code> + 40 hex characters.', parse_mode=ParseMode.HTML)
        return
    limit = await max_limit()
    if len(addresses) > limit:
        await message.answer(f'Found {len(addresses)} addresses, current limit is {limit}. Keeping first {limit}.')
        addresses = addresses[:limit]

    await state.update_data(
        addresses=addresses,
        invalid_addresses=extraction.invalid_addresses,
        duplicates_removed=extraction.duplicates_removed,
        columns=extraction.columns or {},
        selected_column=extraction.selected_column,
        selected_chains={'ethereum', 'base', 'arbitrum'},
        token_filter='all',
        result_filter='all',
        min_usd=0.0,
    )

    if extraction.columns and len(extraction.columns) > 1:
        await state.set_state(CheckFlow.selecting_column)
        await message.answer(
            f'Found {len(addresses)} addresses. Auto-selected column: <b>{extraction.selected_column}</b>.\n'
            f'Duplicates removed: <b>{extraction.duplicates_removed}</b>. Invalid-like values: <b>{len(extraction.invalid_addresses)}</b>.\n\n'
            'Choose another column if needed:',
            reply_markup=column_keyboard(extraction.columns, extraction.selected_column),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.set_state(CheckFlow.selecting_chains)
    await message.answer(
        f'Found addresses: <b>{len(addresses)}</b>\n'
        f'Duplicates removed: <b>{extraction.duplicates_removed}</b>\n'
        f'Invalid-like values: <b>{len(extraction.invalid_addresses)}</b>\n\n'
        'Choose chains:',
        reply_markup=chains_keyboard({'ethereum', 'base', 'arbitrum'}),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data.startswith('column:'))
async def cb_column(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.split(':', 1)[1]
    data = await state.get_data()
    columns = data.get('columns', {})
    if action == 'done':
        await state.set_state(CheckFlow.selecting_chains)
        await call.message.edit_text('Choose chains:', reply_markup=chains_keyboard(set(data.get('selected_chains', {'ethereum'}))))
        await call.answer()
        return
    names = list(columns)
    if action.isdigit() and int(action) < len(names):
        selected = names[int(action)]
        await state.update_data(selected_column=selected, addresses=columns[selected])
        await call.message.edit_reply_markup(reply_markup=column_keyboard(columns, selected))
    await call.answer()


@dp.callback_query(F.data.startswith('chain:'))
async def cb_chain(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get('selected_chains', {'ethereum'}))
    action = call.data.split(':', 1)[1]
    if action == 'all':
        selected = set(CHAINS)
    elif action == 'none':
        selected = set()
    elif action in CHAINS:
        selected.remove(action) if action in selected else selected.add(action)
    await state.update_data(selected_chains=selected)
    await call.message.edit_reply_markup(reply_markup=chains_keyboard(selected))
    await call.answer()


@dp.callback_query(F.data == 'chains:done')
async def cb_chains_done(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected = set(data.get('selected_chains', set()))
    if not selected:
        await call.answer('Choose at least one chain', show_alert=True)
        return
    await state.set_state(CheckFlow.selecting_token)
    await call.message.edit_text('Choose token mode:', reply_markup=token_keyboard(data.get('token_filter', 'all')))
    await call.answer()


@dp.callback_query(F.data.startswith('token:'))
async def cb_token(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.split(':', 1)[1]
    if action == 'done':
        data = await state.get_data()
        await state.set_state(CheckFlow.selecting_filter)
        await call.message.edit_text('Choose result filter:', reply_markup=result_filter_keyboard(data.get('result_filter', 'all')))
        await call.answer()
        return
    await state.update_data(token_filter=action)
    await call.message.edit_reply_markup(reply_markup=token_keyboard(action))
    await call.answer()


@dp.callback_query(F.data.startswith('filter:'))
async def cb_filter(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.split(':', 1)[1]
    if action == 'done':
        data = await state.get_data()
        if data.get('result_filter') == 'min_usd':
            await state.set_state(CheckFlow.waiting_min_usd)
            await call.message.edit_text('Send minimum USD value. Example: <code>10</code>', parse_mode=ParseMode.HTML)
            await call.answer()
            return
        await show_confirmation(call.message, state)
        await call.answer()
        return
    await state.update_data(result_filter=action)
    await call.message.edit_reply_markup(reply_markup=result_filter_keyboard(action))
    await call.answer()


@dp.message(CheckFlow.waiting_min_usd, F.text)
async def receive_min_usd(message: Message, state: FSMContext) -> None:
    raw = (message.text or '').strip().replace(',', '.')
    try:
        value = float(raw)
    except ValueError:
        await message.answer('Send a valid number. Example: <code>10</code>', parse_mode=ParseMode.HTML)
        return
    if value < 0:
        await message.answer('Minimum USD value cannot be negative.')
        return
    await state.update_data(min_usd=value)
    await show_confirmation(message, state)


async def show_confirmation(target: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(CheckFlow.ready)
    await target.answer(
        '<b>Ready to run</b>\n\n'
        f'Addresses: <code>{len(data.get("addresses", []))}</code>\n'
        f'Chains: <code>{", ".join(sorted(data.get("selected_chains", [])))}</code>\n'
        f'Tokens: <code>{data.get("token_filter", "all")}</code>\n'
        f'Result filter: <code>{data.get("result_filter", "all")}</code>\n'
        f'Min USD: <code>{data.get("min_usd", 0)}</code>',
        reply_markup=confirm_keyboard(),
        parse_mode=ParseMode.HTML,
    )


def anti_spam_ok(user_id: int, seconds: int = 5) -> bool:
    now = time.monotonic()
    prev = last_job_start.get(user_id, 0)
    if now - prev < seconds:
        return False
    last_job_start[user_id] = now
    return True


@dp.callback_query(F.data == 'check:run')
async def cb_run_check(call: CallbackQuery, state: FSMContext) -> None:
    if not anti_spam_ok(call.from_user.id):
        await call.answer('Wait a few seconds before starting another job.', show_alert=True)
        return
    data = await state.get_data()
    await start_job_from_data(call, state, data)


async def start_job_from_data(call: CallbackQuery, state: FSMContext, data: dict) -> None:
    addresses = list(data.get('addresses', []))
    invalid_addresses = list(data.get('invalid_addresses', []))
    chains = sorted(list(data.get('selected_chains', [])))
    token_filter = data.get('token_filter', 'all')
    result_filter = data.get('result_filter', 'all')
    min_usd = float(data.get('min_usd') or 0)
    key = await dune_api_key()
    qid = await dune_query_id()
    if not addresses or not chains or not key or not qid:
        await call.answer('Missing addresses, chains or API settings', show_alert=True)
        return
    job_id = await storage.create_job(call.from_user.id, addresses, invalid_addresses, chains, token_filter, result_filter, min_usd)
    await state.clear()
    await call.message.edit_text(f'Job <b>#{job_id}</b> queued. Status: waiting -> running -> ready/error.', parse_mode=ParseMode.HTML)
    await call.answer()
    asyncio.create_task(run_job(call.from_user.id, call.message.chat.id, job_id, addresses, invalid_addresses, chains, token_filter, result_filter, min_usd, key, qid))


async def run_job(
    user_id: int,
    chat_id: int,
    job_id: int,
    addresses: list[str],
    invalid_addresses: list[str],
    chains: list[str],
    token_filter: str,
    result_filter: str,
    min_usd: float,
    key: str,
    qid: int,
) -> None:
    async with job_sem:
        try:
            await storage.update_job(job_id, 'running')
            await bot.send_message(chat_id, f'Job #{job_id}: request sent to Dune. Large lists can take a while.')
            client = DuneClient(key, timeout_seconds=settings.dune_timeout_seconds, poll_seconds=settings.dune_poll_seconds)
            rows = await client.run_wallet_check(qid, addresses, chains, token_filter)
            result_path = make_excel(rows, addresses, invalid_addresses, RESULTS_DIR, job_id, result_filter, min_usd)
            await storage.update_job(job_id, 'done', result_file=str(result_path))
            await bot.send_document(chat_id, FSInputFile(result_path), caption=f'Job #{job_id} ready. Result rows: {len(rows)}')
        except DuneError as e:
            await storage.update_job(job_id, 'error', error=str(e))
            await bot.send_message(chat_id, f'Job #{job_id} failed with Dune error:\n<code>{str(e)[:1500]}</code>', parse_mode=ParseMode.HTML)
        except Exception as e:
            log.exception('Job %s failed for user %s', job_id, user_id)
            await storage.update_job(job_id, 'error', error=str(e))
            await bot.send_message(chat_id, f'Job #{job_id} failed:\n<code>{str(e)[:1500]}</code>', parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'jobs:history')
async def cb_jobs_history(call: CallbackQuery) -> None:
    jobs = await storage.recent_jobs(call.from_user.id, 10)
    if not jobs:
        await call.message.edit_text('History is empty.', reply_markup=main_menu(await is_configured()))
        await call.answer()
        return
    lines = ['<b>Recent jobs</b>\n']
    for job in jobs:
        err = f"\n   Error: <code>{str(job.get('error') or '')[:300]}</code>" if job.get('error') else ''
        lines.append(
            f"#{job['id']} - <b>{job['status']}</b> - addresses: {job.get('address_count')} - "
            f"chains: <code>{job.get('chains')}</code> - tokens: <code>{job.get('token_filter')}</code> - "
            f"filter: <code>{job.get('result_filter')}</code>{err}"
        )
    await call.message.edit_text('\n\n'.join(lines), reply_markup=jobs_keyboard(jobs), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data.startswith('job:download:'))
async def cb_job_download(call: CallbackQuery) -> None:
    job_id = int(call.data.rsplit(':', 1)[1])
    job = await storage.get_job(call.from_user.id, job_id)
    if not job or not job.get('result_file'):
        await call.answer('Result file is not available.', show_alert=True)
        return
    path = Path(job['result_file'])
    if not path.exists():
        await call.answer('Result file is missing on this server.', show_alert=True)
        return
    await call.message.answer_document(FSInputFile(path), caption=f'Result for job #{job_id}')
    await call.answer()


@dp.callback_query(F.data.startswith('job:repeat:'))
async def cb_job_repeat(call: CallbackQuery, state: FSMContext) -> None:
    if not anti_spam_ok(call.from_user.id):
        await call.answer('Wait a few seconds before starting another job.', show_alert=True)
        return
    job_id = int(call.data.rsplit(':', 1)[1])
    job = await storage.get_job(call.from_user.id, job_id)
    if not job:
        await call.answer('Job not found.', show_alert=True)
        return
    data = {
        'addresses': job.get('addresses_json') or [],
        'invalid_addresses': job.get('invalid_json') or [],
        'selected_chains': set(str(job.get('chains') or '').split(',')) - {''},
        'token_filter': job.get('token_filter') or 'all',
        'result_filter': job.get('result_filter') or 'all',
        'min_usd': float(job.get('min_usd') or 0),
    }
    await start_job_from_data(call, state, data)


@dp.errors()
async def errors_handler(event) -> bool:
    log.exception('Unhandled update error: %s', event.exception)
    return True


async def main() -> None:
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == '__main__':
    asyncio.run(main())
