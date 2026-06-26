from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CHAINS = ['ethereum', 'base', 'arbitrum', 'polygon', 'optimism', 'bnb']
TOKENS = {
    'all': 'All tokens',
    'native': 'Native ETH/MATIC/BNB',
    'stables': 'Stablecoins USDT/USDC/DAI',
}
RESULT_FILTERS = {
    'all': 'All wallets',
    'positive': 'Only with balance',
    'min_usd': 'Only above USD amount',
}


def main_menu(is_ready: bool) -> InlineKeyboardMarkup:
    status = 'ready' if is_ready else 'setup needed'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Check addresses', callback_data='check:start')],
        [InlineKeyboardButton(text=f'Admin panel / Settings - {status}', callback_data='admin')],
        [InlineKeyboardButton(text='History', callback_data='jobs:history')],
        [InlineKeyboardButton(text='Railway / Dune guide', callback_data='guide')],
        [InlineKeyboardButton(text='Security', callback_data='security')],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Refresh Dune status', callback_data='admin')],
        [InlineKeyboardButton(text='Set Dune API key', callback_data='set:dune_key')],
        [InlineKeyboardButton(text='Set Dune Query ID', callback_data='set:query_id')],
        [InlineKeyboardButton(text='Set address limit', callback_data='set:max_limit')],
        [InlineKeyboardButton(text='Test Dune query', callback_data='test:dune')],
        [InlineKeyboardButton(text='Reset API key', callback_data='clear:dune_key')],
        [InlineKeyboardButton(text='Reset all settings', callback_data='settings:reset')],
        [InlineKeyboardButton(text='Main menu', callback_data='menu')],
    ])


def settings_keyboard() -> InlineKeyboardMarkup:
    return admin_keyboard()


def chains_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for chain in CHAINS:
        mark = '[x]' if chain in selected else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {chain}', callback_data=f'chain:{chain}')])
    rows.append([
        InlineKeyboardButton(text='All chains', callback_data='chain:all'),
        InlineKeyboardButton(text='Clear', callback_data='chain:none'),
    ])
    rows.append([InlineKeyboardButton(text='Next', callback_data='chains:done')])
    rows.append([InlineKeyboardButton(text='Cancel', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def token_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for key, title in TOKENS.items():
        mark = '[x]' if selected == key else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {title}', callback_data=f'token:{key}')])
    rows.append([InlineKeyboardButton(text='Next', callback_data='token:done')])
    rows.append([InlineKeyboardButton(text='Cancel', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_filter_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for key, title in RESULT_FILTERS.items():
        mark = '[x]' if selected == key else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {title}', callback_data=f'filter:{key}')])
    rows.append([InlineKeyboardButton(text='Next', callback_data='filter:done')])
    rows.append([InlineKeyboardButton(text='Cancel', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def column_keyboard(columns: dict[str, list[str]], selected: str | None) -> InlineKeyboardMarkup:
    rows = []
    for idx, (name, addresses) in enumerate(columns.items()):
        mark = '[x]' if name == selected else '[ ]'
        label = f'{mark} {name} ({len(addresses)})'
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=f'column:{idx}')])
    rows.append([InlineKeyboardButton(text='Use selected column', callback_data='column:done')])
    rows.append([InlineKeyboardButton(text='Cancel', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Run check', callback_data='check:run')],
        [InlineKeyboardButton(text='Upload again', callback_data='check:start')],
        [InlineKeyboardButton(text='Menu', callback_data='menu')],
    ])


def jobs_keyboard(jobs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        job_id = job['id']
        if job.get('result_file'):
            rows.append([
                InlineKeyboardButton(text=f'Download #{job_id}', callback_data=f'job:download:{job_id}'),
                InlineKeyboardButton(text=f'Repeat #{job_id}', callback_data=f'job:repeat:{job_id}'),
            ])
        else:
            rows.append([InlineKeyboardButton(text=f'Repeat #{job_id}', callback_data=f'job:repeat:{job_id}')])
    rows.append([InlineKeyboardButton(text='Main menu', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Back', callback_data='admin')],
        [InlineKeyboardButton(text='Menu', callback_data='menu')],
    ])
