import asyncio
import io
import re
import typing

def _safe_deduplicate(params):
    seen = []
    for item in params:
        if not any(item == existing for existing in seen):
            seen.append(item)
    return tuple(seen)

if hasattr(typing, "_deduplicate"):
    typing._deduplicate = _safe_deduplicate

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message
from api_clients import OsrmClient, WeatherClient
from config import load_config
from geocoding import GeocodingClient
from keyboards import cancel_kb, main_menu_kb, weather_preset_kb
from models import EvaluatedStop, RoutePoint, WeatherSnapshot
from scorer import (
    detour_from_road_distances_km,
    estimate_detour_haversine_km,
    evaluate_stop,
)
from storage import BotStorage


class AddPointFlow(StatesGroup):
    name = State()
    location = State()
    attractiveness = State()
    notes = State()


class PrefsFlow(StatesGroup):
    detour_km = State()
    weather_mode = State()
    target_temp = State()


class PlanRouteFlow(StatesGroup):
    route = State()


class OwnerOnlyMiddleware(BaseMiddleware):
    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user or user.id != self.owner_id:
            if isinstance(event, Message):
                await event.answer("Доступ ограничен: персональный бот.")
            return None
        return await handler(event, data)


def _parse_lat_lon(text: str) -> tuple[float, float] | None:
    nums = re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", "."))
    if len(nums) < 2:
        return None
    lat, lon = float(nums[0]), float(nums[1])
    if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
        return lat, lon
    return None


async def _resolve_endpoint(
    raw: str,
    geocoder: GeocodingClient,
) -> tuple[float, float, str] | None:
    coords = _parse_lat_lon(raw)
    if coords is not None:
        return coords[0], coords[1], f"{coords[0]:.4f}, {coords[1]:.4f}"
    try:
        return await geocoder.resolve_place(raw)
    except Exception:
        return None


async def _get_road_distance_km(
    coords: list[tuple[float, float]],
    storage: BotStorage,
    osrm_client: OsrmClient,
) -> float:
    cached = storage.get_cached_route_km(coords)
    if cached is not None:
        return cached
    distance_km = await osrm_client.route_distance_km(coords)
    storage.save_cached_route_km(coords, distance_km)
    return distance_km


async def _resolve_detour(
    point: RoutePoint,
    s_lat: float,
    s_lon: float,
    e_lat: float,
    e_lon: float,
    direct_road_km: float | None,
    storage: BotStorage,
    osrm_client: OsrmClient,
) -> tuple[float, str, bool]:
    if direct_road_km is not None:
        try:
            via_km = await _get_road_distance_km(
                [(s_lat, s_lon), (point.lat, point.lon), (e_lat, e_lon)],
                storage,
                osrm_client,
            )
            return detour_from_road_distances_km(direct_road_km, via_km), "osrm", False
        except Exception:
            pass

    fallback_km = estimate_detour_haversine_km(s_lat, s_lon, e_lat, e_lon, point)
    return fallback_km, "haversine_fallback", True


def _format_evaluated(stop: EvaluatedStop) -> str:
    p = stop.point
    w = stop.weather
    lines = [
        f"• [{stop.total_score}/100 — {stop.verdict}] #{p.id} {p.name}",
        f"  Погода: {w.temp_c:+.1f}°C, осадки {w.precip_mm:.1f} мм/ч, ветер {w.wind_mps:.1f} м/с",
        f"  Почему так: {', '.join(stop.reasons)}",
    ]
    if p.notes:
        lines.append(f"  Заметка: {p.notes}")
    return "\n".join(lines)


async def main() -> None:
    cfg = load_config()
    storage = BotStorage(
        db_path=cfg.db_path,
        weather_cache_ttl_minutes=cfg.weather_cache_ttl_minutes,
        route_cache_ttl_minutes=cfg.route_cache_ttl_minutes,
    )
    weather_client = WeatherClient()
    osrm_client = OsrmClient(base_url=cfg.osrm_base_url)
    geocoder = GeocodingClient()

    bot = Bot(token=cfg.bot_token)
    dp = Dispatcher()
    dp.message.middleware(OwnerOnlyMiddleware(cfg.owner_id))

    @dp.message(CommandStart())
    @dp.message(F.text == "Отмена")
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Подбор остановок по маршруту с учётом дорожного крюка и погоды.\n"
            "Выбери действие на клавиатуре ниже.",
            reply_markup=main_menu_kb(),
        )

    @dp.message(F.text == "Мои точки")
    @dp.message(Command("points"))
    async def cmd_points(message: Message) -> None:
        pts = storage.list_points()
        if not pts:
            await message.answer(
                "Список пуст. Нажми «Добавить точку», чтобы сохранить место для проверки.",
                reply_markup=main_menu_kb(),
            )
            return
        lines = [
            f"#{p.id} — {p.name} (интерес {p.attractiveness}/10)"
            + (f" · {p.notes}" if p.notes else "")
            for p in pts
        ]
        await message.answer(
            "Сохранённые точки (удалить: /del_point <номер>):\n\n" + "\n".join(lines),
            reply_markup=main_menu_kb(),
        )

    @dp.message(Command("del_point"))
    async def cmd_del_point(message: Message) -> None:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("Пример: /del_point 1", reply_markup=main_menu_kb())
            return
        point_id = int(parts[1].strip())
        if storage.delete_point(point_id):
            await message.answer(f"Точка #{point_id} удалена.", reply_markup=main_menu_kb())
        else:
            await message.answer(f"Точка #{point_id} не найдена.", reply_markup=main_menu_kb())

    # --- Пошаговое добавление точки ---
    @dp.message(F.text == "Добавить точку")
    async def start_add_point(message: Message, state: FSMContext) -> None:
        await state.set_state(AddPointFlow.name)
        await message.answer(
            "Шаг 1/4. Напиши название точки (например: Воронеж или Смотровая у трассы).",
            reply_markup=cancel_kb(),
        )

    @dp.message(AddPointFlow.name)
    async def step_point_name(message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip()
        if not name:
            await message.answer("Название не должно быть пустым. Напиши ещё раз:")
            return
        await state.update_data(name=name)
        await state.set_state(AddPointFlow.location)
        await message.answer(
            "Шаг 2/4. Напиши город/место или координаты «широта долгота» (например: Тамбов или 52.7212 41.4527)."
        )

    @dp.message(AddPointFlow.location)
    async def step_point_location(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        resolved = await _resolve_endpoint(raw, geocoder)
        if resolved is None:
            await message.answer(
                "Не удалось найти место или распознать координаты. Попробуй написать иначе (например: 52.7212 41.4527):"
            )
            return
        lat, lon, label = resolved
        await state.update_data(lat=lat, lon=lon, place_label=label)
        await state.set_state(AddPointFlow.attractiveness)
        await message.answer("Шаг 3/4. Насколько интересна точка от 1 до 10? (число)")

    @dp.message(AddPointFlow.attractiveness)
    async def step_point_attr(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= 10):
            await message.answer("Введи целое число от 1 до 10:")
            return
        await state.update_data(attractiveness=int(raw))
        await state.set_state(AddPointFlow.notes)
        await message.answer("Шаг 4/4. Короткая заметка (или напиши «-», если без заметки):")

    @dp.message(AddPointFlow.notes)
    async def step_point_notes(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        notes = "" if raw in {"", "-"} else raw
        data = await state.get_data()
        point = storage.add_point(
            name=data["name"],
            lat=data["lat"],
            lon=data["lon"],
            attractiveness=data["attractiveness"],
            category="stop",
            notes=notes,
        )
        await state.clear()
        await message.answer(
            f"Сохранено #{point.id}: {point.name} ({data['place_label']}), интерес {point.attractiveness}/10.",
            reply_markup=main_menu_kb(),
        )

    # --- Упрощённая настройка лимитов и погоды ---
    @dp.message(F.text == "Лимиты и погода")
    async def start_prefs(message: Message, state: FSMContext) -> None:
        p = storage.get_preferences()
        await state.set_state(PrefsFlow.detour_km)
        await message.answer(
            "Текущие лимиты:\n"
            f"• Допустимый крюк: {p.max_detour_km:.0f} км\n"
            f"• Макс. осадки: {p.max_precip_mm:.1f} мм/ч\n"
            f"• Целевая температура: {p.preferred_temp_c:+.0f}°C\n\n"
            "Шаг 1/3. Какой максимальный крюк в километрах готов сделать? (введи число, например: 60)",
            reply_markup=cancel_kb(),
        )

    @dp.message(PrefsFlow.detour_km)
    async def step_prefs_detour(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip().replace(",", ".")
        try:
            val = float(raw)
            if val < 0:
                raise ValueError
        except ValueError:
            await message.answer("Введи неотрицательное число (например: 50):")
            return
        await state.update_data(max_detour_km=val)
        await state.set_state(PrefsFlow.weather_mode)
        await message.answer(
            "Шаг 2/3. Выбери допустимый режим по осадкам:",
            reply_markup=weather_preset_kb(),
        )

    @dp.message(PrefsFlow.weather_mode)
    async def step_prefs_weather(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        presets = {
            "Без дождя (до 0.2 мм/ч)": 0.2,
            "Мягкий дождь (до 1.5 мм/ч)": 1.5,
            "Любая погода": 999.0,
        }
        if text not in presets:
            await message.answer("Выбери один из вариантов на кнопках ниже:", reply_markup=weather_preset_kb())
            return
        await state.update_data(max_precip_mm=presets[text])
        await state.set_state(PrefsFlow.target_temp)
        await message.answer(
            "Шаг 3/3. Какая температура для тебя комфортна? (например: 15)",
            reply_markup=cancel_kb(),
        )

    @dp.message(PrefsFlow.target_temp)
    async def step_prefs_temp(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip().replace(",", ".")
        try:
            temp_c = float(raw)
        except ValueError:
            await message.answer("Введи число градусов (например: 15 или -2):")
            return
        data = await state.get_data()
        updated = storage.update_preferences(
            max_detour_km=data["max_detour_km"],
            max_precip_mm=data["max_precip_mm"],
            preferred_temp_c=temp_c,
        )
        await state.clear()
        precip_label = "без ограничений" if updated.max_precip_mm >= 500 else f"до {updated.max_precip_mm:.1f} мм/ч"
        await message.answer(
            "Готово, критерии обновлены:\n"
            f"• Крюк: до {updated.max_detour_km:.0f} км\n"
            f"• Осадки: {precip_label}\n"
            f"• Комфортная температура: {updated.preferred_temp_c:+.0f}°C",
            reply_markup=main_menu_kb(),
        )

    # --- Подбор точек по маршруту ---
    @dp.message(F.text == "Подобрать по маршруту")
    async def start_plan(message: Message, state: FSMContext) -> None:
        if not storage.list_points():
            await message.answer(
                "Сначала добавь хотя бы одну точку через «Добавить точку».",
                reply_markup=main_menu_kb(),
            )
            return
        await state.set_state(PlanRouteFlow.route)
        await message.answer(
            "Напиши маршрут от старта до финиша через тире или стрелку.\n"
            "Например: Белгород - Казань\n"
            "Или координатами: 50.5956 36.5872 -> 55.7961 49.1064",
            reply_markup=cancel_kb(),
        )

    @dp.message(PlanRouteFlow.route)
    async def step_plan_route(message: Message, state: FSMContext) -> None:
        raw = (message.text or "").strip()
        parts = re.split(r"\s*(?:->|→|—|–|\s-\s)\s*", raw, maxsplit=1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            await message.answer("Напиши две точки маршрута через « - », например: Белгород - Казань")
            return

        start_res = await _resolve_endpoint(parts[0], geocoder)
        end_res = await _resolve_endpoint(parts[1], geocoder)
        if start_res is None or end_res is None:
            await message.answer("Не удалось определить старт или финиш. Проверь названия городов или координаты.")
            return

        await state.clear()
        s_lat, s_lon, s_label = start_res
        e_lat, e_lon, e_label = end_res

        points = storage.list_points()
        prefs = storage.get_preferences()

        direct_road_km: float | None = None
        try:
            direct_road_km = await _get_road_distance_km([(s_lat, s_lon), (e_lat, e_lon)], storage, osrm_client)
        except Exception:
            direct_road_km = None

        evaluated: list[EvaluatedStop] = []
        for pt in points:
            detour_km, route_source, _ = await _resolve_detour(
                pt, s_lat, s_lon, e_lat, e_lon, direct_road_km, storage, osrm_client
            )
            weather = storage.get_cached_weather(pt.lat, pt.lon)
            if weather is None:
                try:
                    weather = await weather_client.fetch_current_weather(pt.lat, pt.lon)
                    storage.save_cached_weather(pt.lat, pt.lon, weather)
                except Exception:
                    weather = WeatherSnapshot(
                        temp_c=prefs.preferred_temp_c,
                        precip_mm=0.0,
                        wind_mps=0.0,
                        weather_code=-1,
                        fetched_at="fallback",
                    )
            evaluated.append(evaluate_stop(pt, detour_km, route_source, weather, prefs))

        evaluated.sort(key=lambda item: item.total_score, reverse=True)
        dist_line = f"Дорога: ~{direct_road_km:.0f} км\n" if direct_road_km is not None else ""
        header = f"Маршрут: {s_label} → {e_label}\n{dist_line}\n"
        body = "\n\n".join(_format_evaluated(item) for item in evaluated)
        await message.answer(header + body, reply_markup=main_menu_kb())

    @dp.message(F.text == "Экспорт JSON")
    @dp.message(Command("export"))
    async def cmd_export(message: Message) -> None:
        raw_bytes = storage.export_json().encode("utf-8")
        doc = BufferedInputFile(io.BytesIO(raw_bytes).read(), filename="route_stops_export.json")
        await message.answer_document(doc, caption="Экспорт точек и настроек.", reply_markup=main_menu_kb())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())