from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Подобрать по маршруту"), KeyboardButton(text="Мои точки")],
            [KeyboardButton(text="Добавить точку"), KeyboardButton(text="Лимиты и погода")],
            [KeyboardButton(text="Экспорт JSON")],
        ],
        resize_keyboard=True,
    )


def weather_preset_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Без дождя (до 0.2 мм/ч)"), KeyboardButton(text="Мягкий дождь (до 1.5 мм/ч)")],
            [KeyboardButton(text="Любая погода"), KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )