import aiohttp


class GeocodingClient:
    def __init__(self, timeout_sec: float = 8.0) -> None:
        self._url = "https://geocoding-api.open-meteo.com/v1/search"
        self._timeout = aiohttp.ClientTimeout(total=timeout_sec)

    async def resolve_place(self, query: str) -> tuple[float, float, str] | None:
        cleaned = query.strip()
        if not cleaned:
            return None

        params = {
            "name": cleaned,
            "count": 1,
            "language": "ru",
            "format": "json",
        }
        headers = {"User-Agent": "tg-route-stops-bot/1.2"}

        async with aiohttp.ClientSession(timeout=self._timeout, headers=headers) as session:
            async with session.get(self._url, params=params) as resp:
                resp.raise_for_status()
                payload = await resp.json()

        results = payload.get("results")
        if not results or not isinstance(results, list):
            return None

        top = results[0]
        lat = float(top["latitude"])
        lon = float(top["longitude"])
        title = str(top.get("name") or cleaned)
        region = top.get("admin1")
        display = f"{title}, {region}" if region else title
        return lat, lon, display