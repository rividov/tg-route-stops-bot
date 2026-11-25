from datetime import datetime, timezone
import aiohttp
from models import WeatherSnapshot

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherClient:
    def __init__(self, timeout_sec: float = 8.0) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def fetch_current_weather(self, lat: float, lon: float) -> WeatherSnapshot:
        params = {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "current": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "wind_speed_unit": "ms",
            "timezone": "auto",
        }
        headers = {"User-Agent": "tg-route-stops-bot/1.1"}

        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(OPEN_METEO_URL, params=params) as resp:
                resp.raise_for_status()
                payload = await resp.json()

        current = payload.get("current")
        if not isinstance(current, dict):
            raise ValueError("Некорректный формат ответа Open-Meteo: отсутствует блок current")

        return WeatherSnapshot(
            temp_c=float(current.get("temperature_2m", 0.0)),
            precip_mm=float(current.get("precipitation", 0.0)),
            wind_mps=float(current.get("wind_speed_10m", 0.0)),
            weather_code=int(current.get("weather_code", 0)),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )


class OsrmClient:
    def __init__(
        self,
        base_url: str = "https://router.project-osrm.org",
        timeout_sec: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def route_distance_km(self, coords: list[tuple[float, float]]) -> float:
        if len(coords) < 2:
            raise ValueError("Для расчёта маршрута нужно минимум две координаты")

        # В OSRM порядок координат в пути: lon,lat
        path_coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in coords)
        url = f"{self._base_url}/route/v1/driving/{path_coords}"
        params = {"overview": "false"}
        headers = {"User-Agent": "tg-route-stops-bot/1.1"}

        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                payload = await resp.json()

        routes = payload.get("routes")
        if not routes or not isinstance(routes, list):
            raise ValueError("OSRM не вернул маршрут")

        distance_meters = float(routes[0].get("distance", 0.0))
        return round(distance_meters / 1000.0, 1)