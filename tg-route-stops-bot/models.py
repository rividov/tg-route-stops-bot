from dataclasses import dataclass


@dataclass
class RoutePoint:
    id: int
    name: str
    lat: float
    lon: float
    category: str
    attractiveness: int  # 1..10
    notes: str = ""


@dataclass
class UserPreferences:
    max_detour_km: float = 25.0
    preferred_temp_c: float = 18.0
    max_precip_mm: float = 1.0
    max_wind_mps: float = 12.0
    weight_attractiveness: float = 0.45
    weight_weather: float = 0.35
    weight_detour: float = 0.20


@dataclass
class WeatherSnapshot:
    temp_c: float
    precip_mm: float
    wind_mps: float
    weather_code: int
    fetched_at: str


@dataclass
class EvaluatedStop:
    point: RoutePoint
    detour_km: float
    route_source: str       # "osrm" | "haversine_fallback"
    weather: WeatherSnapshot
    weather_score: float    # 0..100
    detour_score: float     # 0..100
    total_score: float      # 0..100
    verdict: str            # "Заехать" | "Под вопросом" | "Пропустить"
    reasons: list[str]