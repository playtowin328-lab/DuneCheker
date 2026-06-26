from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CHAINS = ['ethereum', 'base', 'arbitrum', 'polygon', 'optimism', 'bnb']
TOKENS = {
    'all': 'Все токены',
    'native': 'Нативный баланс ETH/MATIC/BNB',
    'stables': 'Стейблкоины USDT/USDC/DAI',
}
RESULT_FILTERS = {
    'all': 'Все кошельки',
    'positive': 'Только с балансом',
    'min_usd': 'Только выше суммы в USD',
}


def main_menu(is_ready: bool) -> InlineKeyboardMarkup:
    status = 'готово' if is_ready else 'нужна настройка'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Проверить адреса', callback_data='check:start')],
        [InlineKeyboardButton(text=f'Админ-панель / настройки - {status}', callback_data='admin')],
        [InlineKeyboardButton(text='История проверок', callback_data='jobs:history')],
        [InlineKeyboardButton(text='Инструкция Railway / Dune', callback_data='guide')],
        [InlineKeyboardButton(text='Безопасность', callback_data='security')],
    ])


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Обновить статус Dune', callback_data='admin')],
        [InlineKeyboardButton(text='Установить Dune API key', callback_data='set:dune_key')],
        [InlineKeyboardButton(text='Установить Dune Query ID', callback_data='set:query_id')],
        [InlineKeyboardButton(text='Установить лимит адресов', callback_data='set:max_limit')],
        [InlineKeyboardButton(text='Тестовый запуск Dune', callback_data='test:dune')],
        [InlineKeyboardButton(text='Сбросить API key', callback_data='clear:dune_key')],
        [InlineKeyboardButton(text='Сбросить все настройки', callback_data='settings:reset')],
        [InlineKeyboardButton(text='Главное меню', callback_data='menu')],
    ])


def settings_keyboard() -> InlineKeyboardMarkup:
    return admin_keyboard()


def chains_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for chain in CHAINS:
        mark = '[x]' if chain in selected else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {chain}', callback_data=f'chain:{chain}')])
    rows.append([
        InlineKeyboardButton(text='Все сети', callback_data='chain:all'),
        InlineKeyboardButton(text='Очистить', callback_data='chain:none'),
    ])
    rows.append([InlineKeyboardButton(text='Далее', callback_data='chains:done')])
    rows.append([InlineKeyboardButton(text='Отмена', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def token_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for key, title in TOKENS.items():
        mark = '[x]' if selected == key else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {title}', callback_data=f'token:{key}')])
    rows.append([InlineKeyboardButton(text='Далее', callback_data='token:done')])
    rows.append([InlineKeyboardButton(text='Отмена', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_filter_keyboard(selected: str) -> InlineKeyboardMarkup:
    rows = []
    for key, title in RESULT_FILTERS.items():
        mark = '[x]' if selected == key else '[ ]'
        rows.append([InlineKeyboardButton(text=f'{mark} {title}', callback_data=f'filter:{key}')])
    rows.append([InlineKeyboardButton(text='Далее', callback_data='filter:done')])
    rows.append([InlineKeyboardButton(text='Отмена', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def column_keyboard(columns: dict[str, list[str]], selected: str | None) -> InlineKeyboardMarkup:
    rows = []
    for idx, (name, addresses) in enumerate(columns.items()):
        mark = '[x]' if name == selected else '[ ]'
        label = f'{mark} {name} ({len(addresses)})'
        rows.append([InlineKeyboardButton(text=label[:64], callback_data=f'column:{idx}')])
    rows.append([InlineKeyboardButton(text='Использовать выбранную колонку', callback_data='column:done')])
    rows.append([InlineKeyboardButton(text='Отмена', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Запустить проверку', callback_data='check:run')],
        [InlineKeyboardButton(text='Загрузить заново', callback_data='check:start')],
        [InlineKeyboardButton(text='Меню', callback_data='menu')],
    ])


def jobs_keyboard(jobs: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for job in jobs:
        job_id = job['id']
        if job.get('result_file'):
            rows.append([
                InlineKeyboardButton(text=f'Скачать #{job_id}', callback_data=f'job:download:{job_id}'),
                InlineKeyboardButton(text=f'Повторить #{job_id}', callback_data=f'job:repeat:{job_id}'),
            ])
        else:
            rows.append([InlineKeyboardButton(text=f'Повторить #{job_id}', callback_data=f'job:repeat:{job_id}')])
    rows.append([InlineKeyboardButton(text='Главное меню', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Назад', callback_data='admin')],
        [InlineKeyboardButton(text='Меню', callback_data='menu')],
    ])
