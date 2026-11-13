#!/usr/bin/env python3
"""Отримує прогноз з Open-Meteo для точок із config/locations.json
і зберігає його в data/forecast.json у вигляді, зручному для оцінки польотів.

Що робить:
  * бере вітер, температуру, вологість на приземних висотах і на барометричних рівнях;
  * інтерполює їх на висоти з config/rules.json (100, 550, ... 3000 м над землею);
  * оцінює підошву хмар і висоту, з якої можливе обмерзання;
  * зберігає опади, видимість, код погоди, пориви на землі.

Використання:
  python scripts/fetch_forecast.py                   # живий запит
  python scripts/fetch_forecast.py --input r.json    # тест на збереженій відповіді API
Потрібен лише Python 3.9+, без сторонніх пакетів.
"""
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "Europe/Kyiv"
LEVELS_HPA = [1000, 975, 950, 925, 900, 850, 800, 700, 600]
LOW_HEIGHTS_M = [80, 120, 180]  # висоти, на яких Open-Meteo віддає вітер і температуру
MIN_LEVEL_AGL_M = 190           # рівні нижче ігноруємо, їх покривають 80/120/180 м
CLOUD_RH_PCT = 95.0             # вологість, з якої шар вважаємо хмарою
CLOUD_COVER_MIN_PCT = 20.0      # хмарність, нижче якої шар із високою вологістю не рахуємо хмарою
LOW_CLOUD_MIN_PCT = 30.0        # нижня хмарність для оцінки за рівнем конденсації
ICING_STEP_M = 50


def load_json(rel_path):
    return json.loads((ROOT / rel_path).read_text(encoding="utf-8"))


def build_url(locations, forecast_days):
    hourly = [
        "temperature_2m", "relative_humidity_2m", "dew_point_2m",
        "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "precipitation", "snowfall", "weather_code", "visibility",
        "cloud_cover", "cloud_cover_low",
    ]
    for h in LOW_HEIGHTS_M:
        hourly += [f"temperature_{h}m", f"wind_speed_{h}m", f"wind_direction_{h}m"]
    for lvl in LEVELS_HPA:
        hourly += [
            f"temperature_{lvl}hPa", f"relative_humidity_{lvl}hPa",
            f"wind_speed_{lvl}hPa", f"wind_direction_{lvl}hPa",
            f"geopotential_height_{lvl}hPa",
        ]
    params = {
        "latitude": ",".join(str(l["lat"]) for l in locations),
        "longitude": ",".join(str(l["lon"]) for l in locations),
        "hourly": ",".join(hourly),
        "wind_speed_unit": "ms",
        "timezone": TIMEZONE,
        "forecast_days": forecast_days,
    }
    return API_URL + "?" + urllib.parse.urlencode(params, safe=",")


def fetch(url, attempts=3):
    last_error = None
    for n in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "flyon-weather/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"Спроба {n}/{attempts} не вдалася: {exc}", file=sys.stderr)
            time.sleep(5 * n)
    raise RuntimeError(f"Не вдалося отримати прогноз: {last_error}")


def series(hourly, name, i):
    arr = hourly.get(name)
    if arr is None or i >= len(arr):
        return None
    return arr[i]


def interp(points, x):
    """Лінійна інтерполяція по відсортованих (x, y). Поза діапазоном повертає None
    (нижче першої точки беремо значення першої точки)."""
    if not points:
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def wind_components(speed, direction):
    rad = math.radians(direction)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def interp_wind(points, x):
    """points: [(agl, speed, direction)]. Швидкість інтерполюємо напряму,
    напрямок через u/v, щоб не ламатися на переході 359 -> 1."""
    if not points:
        return None, None
    speeds = [(p[0], p[1]) for p in points]
    speed = interp(speeds, x)
    if speed is None:
        return None, None
    us = [(p[0], wind_components(p[1], p[2])[0]) for p in points]
    vs = [(p[0], wind_components(p[1], p[2])[1]) for p in points]
    u, v = interp(us, x), interp(vs, x)
    if u is None or v is None or (abs(u) < 1e-9 and abs(v) < 1e-9):
        return speed, None
    direction = math.degrees(math.atan2(-u, -v)) % 360
    return speed, direction


def build_profiles(hourly, i, elevation):
    temp, rh, wind = [], [], []

    t2 = series(hourly, "temperature_2m", i)
    if t2 is not None:
        temp.append((2.0, t2))
    rh2 = series(hourly, "relative_humidity_2m", i)
    if rh2 is not None:
        rh.append((2.0, rh2))
    ws10, wd10 = series(hourly, "wind_speed_10m", i), series(hourly, "wind_direction_10m", i)
    if ws10 is not None and wd10 is not None:
        wind.append((10.0, ws10, wd10))

    for h in LOW_HEIGHTS_M:
        t = series(hourly, f"temperature_{h}m", i)
        if t is not None:
            temp.append((float(h), t))
        ws, wd = series(hourly, f"wind_speed_{h}m", i), series(hourly, f"wind_direction_{h}m", i)
        if ws is not None and wd is not None:
            wind.append((float(h), ws, wd))

    for lvl in LEVELS_HPA:
        gh = series(hourly, f"geopotential_height_{lvl}hPa", i)
        if gh is None:
            continue
        agl = gh - elevation
        if agl <= MIN_LEVEL_AGL_M:
            continue
        t = series(hourly, f"temperature_{lvl}hPa", i)
        if t is not None:
            temp.append((agl, t))
        r = series(hourly, f"relative_humidity_{lvl}hPa", i)
        if r is not None:
            rh.append((agl, r))
        ws, wd = series(hourly, f"wind_speed_{lvl}hPa", i), series(hourly, f"wind_direction_{lvl}hPa", i)
        if ws is not None and wd is not None:
            wind.append((agl, ws, wd))

    temp.sort()
    rh.sort()
    wind.sort()
    return temp, rh, wind


def estimate_cloud_base(rh_profile, cloud_cover, cloud_cover_low, t2, dew2):
    """Груба оцінка підошви хмар (м над землею). Модель не віддає її напряму.
    Береться нижча з двох оцінок:
      1) найнижчий рівень профілю з вологістю >= 95 %, якщо загальна хмарність >= 20 %;
      2) рівень конденсації 125*(T - Td), якщо нижня хмарність >= 30 %.
    None означає, що хмар у шарі профілю немає."""
    candidates = []
    if cloud_cover is not None and cloud_cover >= CLOUD_COVER_MIN_PCT:
        for k, (x, y) in enumerate(rh_profile):
            if y >= CLOUD_RH_PCT:
                if k == 0:
                    candidates.append(0.0)
                else:
                    x0, y0 = rh_profile[k - 1]
                    candidates.append(x0 + (CLOUD_RH_PCT - y0) * (x - x0) / (y - y0))
                break
    if (cloud_cover_low is not None and cloud_cover_low >= LOW_CLOUD_MIN_PCT
            and t2 is not None and dew2 is not None):
        candidates.append(max(125.0 * (t2 - dew2), 0.0))
    if not candidates:
        return None
    return int(round(min(candidates) / 50.0) * 50)


def find_icing_height(temp_profile, rh_profile, icing_rule):
    if not temp_profile or not rh_profile:
        return None
    t_lo, t_hi = icing_rule["temp_range_c"]
    rh_min = icing_rule["humidity_min_pct"]
    top = int(min(temp_profile[-1][0], rh_profile[-1][0]))
    for x in range(0, top + 1, ICING_STEP_M):
        t, r = interp(temp_profile, x), interp(rh_profile, x)
        if t is not None and r is not None and t_lo <= t <= t_hi and r >= rh_min:
            return x
    return None


def rnd(value, digits=1):
    return None if value is None else round(value, digits)


def build_hour(hourly, i, elevation, heights, rules):
    temp_p, rh_p, wind_p = build_profiles(hourly, i, elevation)
    wind, wdir, temp, rh = {}, {}, {}, {}
    for ht in heights:
        key = str(ht)
        s, d = interp_wind(wind_p, ht)
        wind[key] = rnd(s)
        wdir[key] = None if d is None else int(round(d)) % 360
        temp[key] = rnd(interp(temp_p, ht))
        r = interp(rh_p, ht)
        rh[key] = None if r is None else int(round(r))

    t2 = series(hourly, "temperature_2m", i)
    dew2 = series(hourly, "dew_point_2m", i)
    cloud_cover = series(hourly, "cloud_cover", i)
    cloud_low = series(hourly, "cloud_cover_low", i)
    return {
        "t": hourly["time"][i],
        "wind_ms": wind,
        "wind_dir": wdir,
        "temp_c": temp,
        "rh_pct": rh,
        "gust_ground_ms": rnd(series(hourly, "wind_gusts_10m", i)),
        "cloud_base_m": estimate_cloud_base(rh_p, cloud_cover, cloud_low, t2, dew2),
        "icing_from_m": find_icing_height(temp_p, rh_p, rules["icing"]),
        "precip_mm": rnd(series(hourly, "precipitation", i)),
        "snowfall_cm": rnd(series(hourly, "snowfall", i)),
        "weather_code": series(hourly, "weather_code", i),
        "visibility_m": rnd(series(hourly, "visibility", i), 0),
        "cloud_cover_pct": rnd(cloud_cover, 0),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", help="прочитати відповідь API з файлу замість живого запиту (для тестів)")
    parser.add_argument("--output", default="data/forecast.json")
    args = parser.parse_args()

    rules = load_json("config/rules.json")
    all_locations = load_json("config/locations.json")["locations"]
    locations = [l for l in all_locations if l.get("lat") is not None and l.get("lon") is not None]
    skipped = [l["name"] for l in all_locations if l not in locations]
    if skipped:
        print("Пропущено точки без координат: " + ", ".join(skipped), file=sys.stderr)
    if not locations:
        sys.exit("У config/locations.json немає жодної точки з координатами.")

    forecast_days = rules["forecast_days"] + 1  # сьогодні + 7 наступних днів
    if args.input:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        data = fetch(build_url(locations, forecast_days))
    if isinstance(data, dict):
        data = [data]
    if len(data) != len(locations):
        sys.exit(f"API повернув {len(data)} точок, очікувалось {len(locations)}.")

    result = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Open-Meteo",
        "timezone": TIMEZONE,
        "heights_m_agl": rules["heights_m_agl"],
        "locations": [],
    }
    for loc, resp in zip(locations, data):
        hourly = resp["hourly"]
        elevation = resp.get("elevation") or 0.0
        hours = [build_hour(hourly, i, elevation, rules["heights_m_agl"], rules)
                 for i in range(len(hourly["time"]))]
        result["locations"].append({
            "id": loc["id"], "name": loc["name"],
            "lat": loc["lat"], "lon": loc["lon"],
            "elevation_m": rnd(elevation, 0),
            "hours": hours,
        })

    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out_path)
    print(f"Збережено {args.output}: {len(result['locations'])} точок, "
          f"{len(result['locations'][0]['hours'])} годин на точку.")


if __name__ == "__main__":
    main()
