from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from models import RoutePoint, UserPreferences, WeatherSnapshot


class BotStorage:
    def __init__(
        self,
        db_path: str,
        weather_cache_ttl_minutes: int = 30,
        route_cache_ttl_minutes: int = 1440,
    ) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.weather_ttl = timedelta(minutes=weather_cache_ttl_minutes)
        self.route_ttl = timedelta(minutes=route_cache_ttl_minutes)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    lat REAL NOT NULL,
                    lon REAL NOT NULL,
                    category TEXT NOT NULL DEFAULT 'stop',
                    attractiveness INTEGER NOT NULL DEFAULT 7,
                    notes TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    max_detour_km REAL NOT NULL DEFAULT 25.0,
                    preferred_temp_c REAL NOT NULL DEFAULT 18.0,
                    max_precip_mm REAL NOT NULL DEFAULT 1.0,
                    max_wind_mps REAL NOT NULL DEFAULT 12.0,
                    weight_attractiveness REAL NOT NULL DEFAULT 0.45,
                    weight_weather REAL NOT NULL DEFAULT 0.35,
                    weight_detour REAL NOT NULL DEFAULT 0.20
                );

                INSERT OR IGNORE INTO preferences (id) VALUES (1);

                CREATE TABLE IF NOT EXISTS weather_cache (
                    coord_key TEXT PRIMARY KEY,
                    temp_c REAL NOT NULL,
                    precip_mm REAL NOT NULL,
                    wind_mps REAL NOT NULL,
                    weather_code INTEGER NOT NULL,
                    fetched_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS route_cache (
                    route_key TEXT PRIMARY KEY,
                    distance_km REAL NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                """
            )

    def add_point(
        self,
        name: str,
        lat: float,
        lon: float,
        attractiveness: int,
        category: str = "stop",
        notes: str = "",
    ) -> RoutePoint:
        clamped_attr = max(1, min(10, attractiveness))
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO points (name, lat, lon, category, attractiveness, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name.strip(), lat, lon, category.strip() or "stop", clamped_attr, notes.strip()),
            )
            point_id = int(cur.lastrowid)
        return RoutePoint(
            id=point_id,
            name=name.strip(),
            lat=lat,
            lon=lon,
            category=category.strip() or "stop",
            attractiveness=clamped_attr,
            notes=notes.strip(),
        )

    def list_points(self) -> list[RoutePoint]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM points ORDER BY attractiveness DESC, id ASC").fetchall()
        return [
            RoutePoint(
                id=int(r["id"]),
                name=str(r["name"]),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                category=str(r["category"]),
                attractiveness=int(r["attractiveness"]),
                notes=str(r["notes"]),
            )
            for r in rows
        ]

    def delete_point(self, point_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM points WHERE id = ?", (point_id,))
        return cur.rowcount > 0

    def get_preferences(self) -> UserPreferences:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM preferences WHERE id = 1").fetchone()
        if not row:
            return UserPreferences()
        return UserPreferences(
            max_detour_km=float(row["max_detour_km"]),
            preferred_temp_c=float(row["preferred_temp_c"]),
            max_precip_mm=float(row["max_precip_mm"]),
            max_wind_mps=float(row["max_wind_mps"]),
            weight_attractiveness=float(row["weight_attractiveness"]),
            weight_weather=float(row["weight_weather"]),
            weight_detour=float(row["weight_detour"]),
        )

    def update_preferences(self, **kwargs: Any) -> UserPreferences:
        allowed = {
            "max_detour_km",
            "preferred_temp_c",
            "max_precip_mm",
            "max_wind_mps",
            "weight_attractiveness",
            "weight_weather",
            "weight_detour",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if updates:
            assignments = ", ".join(f"{col} = ?" for col in updates)
            values = list(updates.values())
            with self._connect() as conn:
                conn.execute(f"UPDATE preferences SET {assignments} WHERE id = 1", values)
        return self.get_preferences()

    @staticmethod
    def _coord_key(lat: float, lon: float) -> str:
        return f"{lat:.2f}:{lon:.2f}"

    @staticmethod
    def _route_key(coords: list[tuple[float, float]]) -> str:
        return "|".join(f"{lat:.4f},{lon:.4f}" for lat, lon in coords)

    def get_cached_weather(self, lat: float, lon: float) -> WeatherSnapshot | None:
        key = self._coord_key(lat, lon)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM weather_cache WHERE coord_key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            fetched_dt = datetime.fromisoformat(str(row["fetched_at"]))
        except ValueError:
            return None
        if datetime.now(timezone.utc) - fetched_dt > self.weather_ttl:
            return None
        return WeatherSnapshot(
            temp_c=float(row["temp_c"]),
            precip_mm=float(row["precip_mm"]),
            wind_mps=float(row["wind_mps"]),
            weather_code=int(row["weather_code"]),
            fetched_at=str(row["fetched_at"]),
        )

    def save_cached_weather(self, lat: float, lon: float, snap: WeatherSnapshot) -> None:
        key = self._coord_key(lat, lon)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO weather_cache (coord_key, temp_c, precip_mm, wind_mps, weather_code, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(coord_key) DO UPDATE SET
                    temp_c = excluded.temp_c,
                    precip_mm = excluded.precip_mm,
                    wind_mps = excluded.wind_mps,
                    weather_code = excluded.weather_code,
                    fetched_at = excluded.fetched_at
                """,
                (key, snap.temp_c, snap.precip_mm, snap.wind_mps, snap.weather_code, snap.fetched_at),
            )

    def get_cached_route_km(self, coords: list[tuple[float, float]]) -> float | None:
        key = self._route_key(coords)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM route_cache WHERE route_key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            fetched_dt = datetime.fromisoformat(str(row["fetched_at"]))
        except ValueError:
            return None
        if datetime.now(timezone.utc) - fetched_dt > self.route_ttl:
            return None
        return float(row["distance_km"])

    def save_cached_route_km(self, coords: list[tuple[float, float]], distance_km: float) -> None:
        key = self._route_key(coords)
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO route_cache (route_key, distance_km, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(route_key) DO UPDATE SET
                    distance_km = excluded.distance_km,
                    fetched_at = excluded.fetched_at
                """,
                (key, distance_km, now_iso),
            )

    def export_json(self) -> str:
        payload = {
            "preferences": self.get_preferences().__dict__,
            "points": [p.__dict__ for p in self.list_points()],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)