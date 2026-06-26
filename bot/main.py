import asyncio
import logging
import re
import tempfile
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, Message

from .address_utils import extract_address_details_from_file, extract_addresses, split_batches
from .config import RESULTS_DIR, load_settings
from .dune_client import DuneClient, DuneError, DuneTransientError
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

STATUS_LABELS = {
    'queued': 'ожидает',
    'running': 'выполняется',
    'done': 'готово',
    'error': 'ошибка',
}
TOKEN_LABELS = {
    'all': 'все токены',
    'native': 'нативный баланс',
    'stables': 'стейблкоины',
}
FILTER_LABELS = {
    'all': 'все кошельки',
    'positive': 'только с балансом',
    'min_usd': 'выше суммы в USD',
}
QUERY_ID_RE = re.compile(r'(\d{4,})')


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
    text = 'Доступ закрыт. Попроси владельца добавить твой Telegram ID в OWNER_USER_ID или ALLOWED_USER_IDS.'
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


async def config_status() -> tuple[bool, str]:
    has_key = bool(await dune_api_key())
    has_qid = bool(await dune_query_id())
    if has_key and has_qid:
        return True, 'готово'
    missing = []
    if not has_key:
        missing.append('Dune API key')
    if not has_qid:
        missing.append('Query ID')
    return False, 'не хватает: ' + ', '.join(missing)


def mask_secret(value: str) -> str:
    if not value:
        return 'не задан'
    if len(value) <= 10:
        return value[:2] + '****'
    return f'{value[:6]}******{value[-4:]}'


def label(mapping: dict[str, str], value: object) -> str:
    raw = str(value or '')
    return mapping.get(raw, raw or 'не задано')


def parse_dune_key(text: str) -> str:
    value = (text or '').strip()
    if '=' in value:
        value = value.split('=', 1)[1].strip()
    return value.strip(' "\'`')


def parse_query_id(text: str) -> str:
    value = (text or '').strip()
    if value.isdigit():
        return value
    match = QUERY_ID_RE.search(value)
    return match.group(1) if match else ''


async def safe_edit_text(message: Message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc):
            return
        raise


async def admin_text(refresh_dune: bool = False) -> str:
    owner = await get_owner_id()
    key = await dune_api_key()
    qid = await dune_query_id()
    ready, ready_text = await config_status()
    status = 'не настроен'
    credits = 'неизвестно'
    if key and refresh_dune:
        result = await DuneClient(key, settings.dune_timeout_seconds, settings.dune_poll_seconds).api_status()
        status = 'онлайн' if result.get('ok') else 'ошибка'
        credits = str(result.get('credits') if result.get('credits') is not None else result.get('details') or 'неизвестно')
    elif key:
        status = 'настроен'
    return (
        '<b>Админ-панель</b>\n\n'
        f'Готовность: <code>{ready_text}</code>\n'
        f'Статус Dune API: <code>{status}</code>\n'
        f'Credits / лимиты: <code>{credits}</code>\n'
        f'Текущий Query ID: <code>{qid or "не задан"}</code>\n'
        f'Владелец бота: <code>{owner or "не задан"}</code>\n'
        f'Хранилище: <code>{storage.backend}</code>\n'
        f'Dune API key: <code>{mask_secret(key)}</code>\n'
        f'Лимит адресов: <code>{await max_limit()}</code>\n'
        f'Параллельных задач: <code>{settings.job_concurrency}</code>\n'
        f'Размер батча: <code>{settings.dune_batch_size}</code>\n'
        f'Повторов на батч: <code>{settings.dune_max_retries}</code>'
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
    asyncio.create_task(resume_pending_jobs())


@dp.shutdown()
async def on_shutdown() -> None:
    await storage.close()


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    await state.clear()
    await message.answer(
        '<b>Dune Проверка кошельков v3 Pro</b>\n\n'
        'Загрузи TXT, CSV или XLSX с публичными EVM-адресами. Бот удалит дубли, проверит адреса, запустит Dune и вернёт Excel-отчёт.\n\n'
        'Не отправляй seed-фразы, private key, пароли и биржевые ключи с торговыми правами.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command('help'))
async def cmd_help(message: Message) -> None:
    await message.answer(
        '<b>Команды</b>\n\n'
        '/start - меню\n'
        '/help - помощь\n\n'
        'Сценарий: админ-панель -> Dune API key -> Query ID -> загрузка адресов -> сети -> режим -> Excel.',
        parse_mode=ParseMode.HTML,
    )


@dp.message(Command('status'))
async def cmd_status(message: Message) -> None:
    if await deny_if_needed(message):
        return
    await message.answer(await admin_text(refresh_dune=False), reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'menu')
async def menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text('<b>Главное меню</b>', reply_markup=main_menu(await is_configured()), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data.in_({'settings', 'admin'}))
async def cb_admin(call: CallbackQuery, state: FSMContext) -> None:
    if await deny_if_needed(call):
        return
    await state.clear()
    await safe_edit_text(call.message, await admin_text(refresh_dune=True), reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data == 'guide')
async def cb_guide(call: CallbackQuery) -> None:
    await call.message.edit_text(
        '<b>Деплой Railway / VPS</b>\n\n'
        '1. Запушь этот репозиторий на GitHub.\n'
        '2. В Railway создай проект из GitHub-репозитория.\n'
        '3. Добавь PostgreSQL и переменные BOT_TOKEN, OWNER_USER_ID, MAX_ADDRESSES_PER_JOB.\n'
        '4. Открой sql/dune_wallet_balances.sql в Dune, сохрани как Query и вставь Query ID в админ-панель бота.\n'
        '5. Перезапусти Railway-сервис и открой /start в Telegram.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.callback_query(F.data == 'security')
async def cb_security(call: CallbackQuery) -> None:
    await call.message.edit_text(
        '<b>Безопасность</b>\n\n'
        '- Бот принимает только публичные адреса кошельков.\n'
        '- API-ключи показываются только маской.\n'
        '- Сообщения с Dune key удаляются, если Telegram разрешает это сделать.\n'
        '- Доступ ограничен владельцем и разрешёнными пользователями.\n'
        '- Лимит адресов и короткий антиспам защищают очередь.',
        reply_markup=main_menu(await is_configured()),
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.callback_query(F.data == 'set:dune_key')
async def cb_set_dune_key(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_dune_key)
    await call.message.edit_text('Отправь Dune API key одним сообщением. После сохранения я попробую удалить сообщение с ключом.', reply_markup=back_keyboard())
    await call.answer()


@dp.message(SettingsFlow.waiting_dune_key, F.text)
async def receive_dune_key(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    key = parse_dune_key(message.text or '')
    if len(key) < 12:
        await message.answer('Ключ выглядит слишком коротким. Проверь его и отправь ещё раз.')
        return
    await storage.set('dune_api_key', key)
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        'Dune API key сохранён. В интерфейсе он будет показываться только маской.\n\n' + await admin_text(refresh_dune=False),
        reply_markup=settings_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == 'set:query_id')
async def cb_set_query_id(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_query_id)
    await call.message.edit_text(
        'Отправь Dune Query ID числом или ссылкой на query. Пример: <code>1234567</code> или <code>https://dune.com/queries/1234567/...</code>',
        reply_markup=back_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    await call.answer()


@dp.message(SettingsFlow.waiting_query_id, F.text)
async def receive_query_id(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    value = parse_query_id(message.text or '')
    if not value:
        await message.answer('Не смог найти Query ID. Отправь число из ссылки Dune, например: <code>1234567</code>.', parse_mode=ParseMode.HTML)
        return
    await storage.set('dune_query_id', value)
    await state.clear()
    await message.answer(
        f'Query ID сохранён: <code>{value}</code>\n\n' + await admin_text(refresh_dune=False),
        reply_markup=settings_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@dp.callback_query(F.data == 'set:max_limit')
async def cb_set_max_limit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.waiting_max_limit)
    await call.message.edit_text('Отправь лимит адресов на одну задачу. Рекомендую 500-3000. Максимум: 10000.', reply_markup=back_keyboard())
    await call.answer()


@dp.message(SettingsFlow.waiting_max_limit, F.text)
async def receive_max_limit(message: Message, state: FSMContext) -> None:
    if await deny_if_needed(message):
        return
    value = (message.text or '').strip()
    if not value.isdigit() or not (1 <= int(value) <= 10000):
        await message.answer('Введи число от 1 до 10000.')
        return
    await storage.set('max_addresses_per_job', int(value))
    await state.clear()
    await message.answer(f'Лимит сохранён: <code>{value}</code>', reply_markup=settings_keyboard(), parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'clear:dune_key')
async def cb_clear_dune_key(call: CallbackQuery) -> None:
    await storage.set('dune_api_key', '')
    await call.message.edit_text('Dune API key удалён.', reply_markup=settings_keyboard())
    await call.answer()


@dp.callback_query(F.data == 'settings:reset')
async def cb_reset_settings(call: CallbackQuery) -> None:
    await storage.delete_many(['dune_api_key', 'dune_query_id', 'max_addresses_per_job'])
    await call.message.edit_text('Настройки сброшены. OWNER_USER_ID из переменных окружения сохранён.', reply_markup=admin_keyboard())
    await call.answer()


@dp.callback_query(F.data == 'test:dune')
async def cb_test_dune(call: CallbackQuery) -> None:
    key = await dune_api_key()
    qid = await dune_query_id()
    if not key or not qid:
        await call.answer('Сначала задай Dune API key и Query ID', show_alert=True)
        return
    await call.message.edit_text('Запускаю короткий тест Dune с нулевым адресом...', parse_mode=ParseMode.HTML)
    try:
        client = DuneClient(key, timeout_seconds=settings.dune_timeout_seconds, poll_seconds=settings.dune_poll_seconds)
        await client.run_wallet_check(qid, ['0x0000000000000000000000000000000000000000'], ['ethereum'], 'native')
        await call.message.answer('Dune API key и Query ID работают.', reply_markup=settings_keyboard())
    except Exception as e:
        await call.message.answer(f'Тест не прошёл:\n<code>{str(e)[:1500]}</code>', reply_markup=settings_keyboard(), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data == 'check:start')
async def cb_check_start(call: CallbackQuery, state: FSMContext) -> None:
    if not await is_configured():
        await call.answer('Сначала задай Dune API key и Query ID', show_alert=True)
        return
    await state.set_state(CheckFlow.waiting_addresses)
    await call.message.edit_text(
        '<b>Загрузка адресов</b>\n\n'
        'Отправь адреса текстом или загрузи TXT, CSV, XLSX. Я найду колонку с адресами, удалю дубли и проверю EVM-адреса.',
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
                await message.answer('Отправь файл TXT, CSV или XLSX.')
                return
            with tempfile.TemporaryDirectory() as tmp:
                file_path = Path(tmp) / (message.document.file_name or 'addresses.txt')
                await bot.download(message.document, destination=file_path)
                extraction = extract_address_details_from_file(file_path)
        else:
            extraction = extract_addresses(message.text or '')
    except Exception as e:
        await message.answer(f'Не смог прочитать файл: <code>{str(e)[:1000]}</code>', parse_mode=ParseMode.HTML)
        return

    addresses = extraction.addresses
    if not addresses:
        await message.answer('Валидные адреса не найдены. Нужен формат: <code>0x</code> + 40 hex-символов.', parse_mode=ParseMode.HTML)
        return
    limit = await max_limit()
    if len(addresses) > limit:
        await message.answer(f'Найдено {len(addresses)} адресов, текущий лимит: {limit}. Оставляю первые {limit}.')
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
            f'Найдено адресов: <b>{len(addresses)}</b>. Автоматически выбрана колонка: <b>{extraction.selected_column}</b>.\n'
            f'Удалено дублей: <b>{extraction.duplicates_removed}</b>. Похожих на невалидные адреса: <b>{len(extraction.invalid_addresses)}</b>.\n\n'
            'Если нужно, выбери другую колонку:',
            reply_markup=column_keyboard(extraction.columns, extraction.selected_column),
            parse_mode=ParseMode.HTML,
        )
        return

    await state.set_state(CheckFlow.selecting_chains)
    await message.answer(
        f'Найдено адресов: <b>{len(addresses)}</b>\n'
        f'Удалено дублей: <b>{extraction.duplicates_removed}</b>\n'
        f'Похожих на невалидные адреса: <b>{len(extraction.invalid_addresses)}</b>\n\n'
        'Выбери сети:',
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
        await call.message.edit_text('Выбери сети:', reply_markup=chains_keyboard(set(data.get('selected_chains', {'ethereum'}))))
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
        await call.answer('Выбери хотя бы одну сеть', show_alert=True)
        return
    await state.set_state(CheckFlow.selecting_token)
    await call.message.edit_text('Выбери режим токенов:', reply_markup=token_keyboard(data.get('token_filter', 'all')))
    await call.answer()


@dp.callback_query(F.data.startswith('token:'))
async def cb_token(call: CallbackQuery, state: FSMContext) -> None:
    action = call.data.split(':', 1)[1]
    if action == 'done':
        data = await state.get_data()
        await state.set_state(CheckFlow.selecting_filter)
        await call.message.edit_text('Выбери фильтр результата:', reply_markup=result_filter_keyboard(data.get('result_filter', 'all')))
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
            await call.message.edit_text('Отправь минимальную сумму в USD. Пример: <code>10</code>', parse_mode=ParseMode.HTML)
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
        await message.answer('Отправь корректное число. Пример: <code>10</code>', parse_mode=ParseMode.HTML)
        return
    if value < 0:
        await message.answer('Минимальная сумма в USD не может быть отрицательной.')
        return
    await state.update_data(min_usd=value)
    await show_confirmation(message, state)


async def show_confirmation(target: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(CheckFlow.ready)
    await target.answer(
        '<b>Готово к запуску</b>\n\n'
        f'Адресов: <code>{len(data.get("addresses", []))}</code>\n'
        f'Сети: <code>{", ".join(sorted(data.get("selected_chains", [])))}</code>\n'
        f'Токены: <code>{label(TOKEN_LABELS, data.get("token_filter", "all"))}</code>\n'
        f'Фильтр результата: <code>{label(FILTER_LABELS, data.get("result_filter", "all"))}</code>\n'
        f'Минимум USD: <code>{data.get("min_usd", 0)}</code>',
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
        await call.answer('Подожди несколько секунд перед запуском новой задачи.', show_alert=True)
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
        await call.answer('Не хватает адресов, сетей или API-настроек', show_alert=True)
        return
    job_id = await storage.create_job(call.from_user.id, call.message.chat.id, addresses, invalid_addresses, chains, token_filter, result_filter, min_usd)
    await state.clear()
    await call.message.edit_text(f'Задача <b>#{job_id}</b> поставлена в очередь. Статус: ожидает -> выполняется -> готово/ошибка.', parse_mode=ParseMode.HTML)
    await call.answer()
    asyncio.create_task(run_job(call.from_user.id, call.message.chat.id, job_id, addresses, invalid_addresses, chains, token_filter, result_filter, min_usd, key, qid))


async def resume_pending_jobs() -> None:
    await asyncio.sleep(2)
    key = await dune_api_key()
    qid = await dune_query_id()
    jobs = await storage.resumable_jobs()
    if not jobs:
        return
    if not key or not qid:
        for job in jobs:
            await storage.update_job(job['id'], 'error', error='После перезапуска бота нет Dune API настроек')
        log.warning('Marked %s resumable jobs as failed because Dune settings are missing', len(jobs))
        return
    for job in jobs:
        addresses = job.get('addresses_json') or []
        chains = sorted(set(str(job.get('chains') or '').split(',')) - {''})
        if not addresses or not chains:
            await storage.update_job(job['id'], 'error', error='Задачу нельзя возобновить: нет адресов или сетей')
            continue
        chat_id = int(job.get('chat_id') or 0)
        await bot.send_message(chat_id, f'Возобновляю задачу #{job["id"]} после перезапуска.')
        asyncio.create_task(run_job(
            int(job['user_id']),
            chat_id,
            int(job['id']),
            addresses,
            job.get('invalid_json') or [],
            chains,
            job.get('token_filter') or 'all',
            job.get('result_filter') or 'all',
            float(job.get('min_usd') or 0),
            key,
            qid,
        ))


async def run_dune_batch_with_retry(
    client: DuneClient,
    qid: int,
    batch: list[str],
    chains: list[str],
    token_filter: str,
    job_id: int,
    batch_number: int,
) -> list[dict]:
    last_error: Exception | None = None
    for attempt in range(1, settings.dune_max_retries + 1):
        try:
            return await client.run_wallet_check(qid, batch, chains, token_filter)
        except DuneTransientError as exc:
            last_error = exc
            if attempt >= settings.dune_max_retries:
                break
            delay = settings.dune_retry_delay_seconds * attempt
            log.warning('Job %s batch %s transient Dune error on attempt %s/%s: %s', job_id, batch_number, attempt, settings.dune_max_retries, exc)
            await asyncio.sleep(delay)
    if last_error:
        raise last_error
    return []


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
            batches = split_batches(addresses, settings.dune_batch_size)
            total_batches = len(batches)
            await storage.update_job_progress(job_id, 0, total_batches)
            progress_message = await bot.send_message(chat_id, f'Задача #{job_id}: выполняю батч 0/{total_batches}.')
            client = DuneClient(key, timeout_seconds=settings.dune_timeout_seconds, poll_seconds=settings.dune_poll_seconds)
            rows: list[dict] = []
            for index, batch in enumerate(batches, start=1):
                await storage.update_job_progress(job_id, index - 1, total_batches)
                try:
                    await progress_message.edit_text(
                        f'Задача #{job_id}: выполняю батч {index}/{total_batches} '
                        f'({len(batch)} адресов, собрано строк: {len(rows)}).'
                    )
                except Exception:
                    pass
                rows.extend(await run_dune_batch_with_retry(client, qid, batch, chains, token_filter, job_id, index))
                await storage.update_job_progress(job_id, index, total_batches)

            result_path = make_excel(rows, addresses, invalid_addresses, RESULTS_DIR, job_id, result_filter, min_usd)
            await storage.update_job(job_id, 'done', result_file=str(result_path))
            try:
                await progress_message.edit_text(f'Задача #{job_id}: готово. Батчи: {total_batches}/{total_batches}. Строк: {len(rows)}.')
            except Exception:
                pass
            await bot.send_document(chat_id, FSInputFile(result_path), caption=f'Задача #{job_id} готова. Строк результата: {len(rows)}')
        except DuneError as e:
            await storage.update_job(job_id, 'error', error=str(e))
            await bot.send_message(chat_id, f'Задача #{job_id} завершилась ошибкой Dune:\n<code>{str(e)[:1500]}</code>', parse_mode=ParseMode.HTML)
        except Exception as e:
            log.exception('Job %s failed for user %s', job_id, user_id)
            await storage.update_job(job_id, 'error', error=str(e))
            await bot.send_message(chat_id, f'Задача #{job_id} завершилась ошибкой:\n<code>{str(e)[:1500]}</code>', parse_mode=ParseMode.HTML)


@dp.callback_query(F.data == 'jobs:history')
async def cb_jobs_history(call: CallbackQuery) -> None:
    jobs = await storage.recent_jobs(call.from_user.id, 10)
    if not jobs:
        await call.message.edit_text('История пока пустая.', reply_markup=main_menu(await is_configured()))
        await call.answer()
        return
    lines = ['<b>Последние задачи</b>\n']
    for job in jobs:
        err = f"\n   Ошибка: <code>{str(job.get('error') or '')[:300]}</code>" if job.get('error') else ''
        lines.append(
            f"#{job['id']} - <b>{label(STATUS_LABELS, job.get('status'))}</b> - адресов: {job.get('address_count')} - "
            f"сети: <code>{job.get('chains')}</code> - токены: <code>{label(TOKEN_LABELS, job.get('token_filter'))}</code> - "
            f"фильтр: <code>{label(FILTER_LABELS, job.get('result_filter'))}</code> - "
            f"прогресс: <code>{job.get('batch_done', 0)}/{job.get('batch_total', 0)}</code>{err}"
        )
    await call.message.edit_text('\n\n'.join(lines), reply_markup=jobs_keyboard(jobs), parse_mode=ParseMode.HTML)
    await call.answer()


@dp.callback_query(F.data.startswith('job:download:'))
async def cb_job_download(call: CallbackQuery) -> None:
    job_id = int(call.data.rsplit(':', 1)[1])
    job = await storage.get_job(call.from_user.id, job_id)
    if not job or not job.get('result_file'):
        await call.answer('Файл результата недоступен.', show_alert=True)
        return
    path = Path(job['result_file'])
    if not path.exists():
        await call.answer('Файл результата отсутствует на этом сервере.', show_alert=True)
        return
    await call.message.answer_document(FSInputFile(path), caption=f'Результат задачи #{job_id}')
    await call.answer()


@dp.callback_query(F.data.startswith('job:repeat:'))
async def cb_job_repeat(call: CallbackQuery, state: FSMContext) -> None:
    if not anti_spam_ok(call.from_user.id):
        await call.answer('Подожди несколько секунд перед запуском новой задачи.', show_alert=True)
        return
    job_id = int(call.data.rsplit(':', 1)[1])
    job = await storage.get_job(call.from_user.id, job_id)
    if not job:
        await call.answer('Задача не найдена.', show_alert=True)
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
