import math
from models import EvaluatedStop, RoutePoint, UserPreferences, WeatherSnapshot


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * earth_radius_km * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def estimate_detour_haversine_km(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    point: RoutePoint,
) -> float:
    direct_km = haversine_km(start_lat, start_lon, end_lat, end_lon)
    via_point_km = (
        haversine_km(start_lat, start_lon, point.lat, point.lon)
        + haversine_km(point.lat, point.lon, end_lat, end_lon)
    )
    return round(max(0.0, (via_point_km - direct_km) * 1.18), 1)


def detour_from_road_distances_km(direct_road_km: float, via_point_road_km: float) -> float:
    return round(max(0.0, via_point_road_km - direct_road_km), 1)


def score_weather(weather: WeatherSnapshot, prefs: UserPreferences) -> tuple[float, list[str]]:
    score = 100.0
    reasons: list[str] = []

    temp_diff = abs(weather.temp_c - prefs.preferred_temp_c)
    if temp_diff > 3.0:
        temp_penalty = min(35.0, (temp_diff - 3.0) * 2.2)
        score -= temp_penalty
        reasons.append(f"температура {weather.temp_c:+.1f}°C (цель {prefs.preferred_temp_c:+.1f}°C)")

    if weather.precip_mm > prefs.max_precip_mm:
        precip_penalty = min(50.0, (weather.precip_mm - prefs.max_precip_mm) * 18.0 + 15.0)
        score -= precip_penalty
        reasons.append(f"осадки {weather.precip_mm:.1f} мм/ч (лимит {prefs.max_precip_mm:.1f})")

    if weather.wind_mps > prefs.max_wind_mps:
        wind_penalty = min(30.0, (weather.wind_mps - prefs.max_wind_mps) * 4.0)
        score -= wind_penalty
        reasons.append(f"ветер {weather.wind_mps:.1f} м/с (лимит {prefs.max_wind_mps:.1f})")

    return max(0.0, round(score, 1)), reasons


def score_detour(
    detour_km: float,
    prefs: UserPreferences,
    route_source: str,
) -> tuple[float, list[str]]:
    limit = max(1.0, prefs.max_detour_km)
    reasons: list[str] = []
    source_label = "по дорогам" if route_source == "osrm" else "оценка по прямой"

    if detour_km <= limit:
        ratio = detour_km / limit
        score = 100.0 - ratio * 35.0
        reasons.append(f"крюк {detour_km:.1f} км ({source_label}) в лимите {limit:.1f} км")
        return max(65.0, round(score, 1)), reasons

    over_km = detour_km - limit
    score = max(0.0, 60.0 - over_km * 3.0)
    reasons.append(
        f"крюк {detour_km:.1f} км ({source_label}) выше лимита {limit:.1f} км (+{over_km:.1f} км)"
    )
    return round(score, 1), reasons


def evaluate_stop(
    point: RoutePoint,
    detour_km: float,
    route_source: str,
    weather: WeatherSnapshot,
    prefs: UserPreferences,
) -> EvaluatedStop:
    attr_score = max(0.0, min(100.0, point.attractiveness * 10.0))
    weather_score, w_reasons = score_weather(weather, prefs)
    detour_score, d_reasons = score_detour(detour_km, prefs, route_source)

    w_sum = prefs.weight_attractiveness + prefs.weight_weather + prefs.weight_detour
    if w_sum <= 0:
        w_sum = 1.0

    total = (
        attr_score * prefs.weight_attractiveness
        + weather_score * prefs.weight_weather
        + detour_score * prefs.weight_detour
    ) / w_sum

    if detour_km > prefs.max_detour_km * 1.5 and total < 65.0:
        total = max(0.0, total - 15.0)

    total_rounded = round(total, 1)
    if total_rounded >= 72.0:
        verdict = "Заехать"
    elif total_rounded >= 50.0:
        verdict = "Под вопросом"
    else:
        verdict = "Пропустить"

    reasons = [f"интерес {point.attractiveness}/10"] + d_reasons + w_reasons
    return EvaluatedStop(
        point=point,
        detour_km=detour_km,
        route_source=route_source,
        weather=weather,
        weather_score=weather_score,
        detour_score=detour_score,
        total_score=total_rounded,
        verdict=verdict,
        reasons=reasons,
    )