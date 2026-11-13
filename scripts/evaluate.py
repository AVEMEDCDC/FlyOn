#!/usr/bin/env python3
"""Оцінює прогноз (data/forecast.json) за ТТХ кожного літака (config/aircraft/*.json)
і правилами (config/rules.json). Результат: data/assessment.json.

Для кожної точки й кожного літака рахує по кожній годині:
  green  - усе в межах оптимуму;
  yellow - можливо, але є причина бути уважним (вітер між оптимальним і максимальним, сніг);
  red    - польоти неможливі (хоч одна межа перевищена, дощ, туман);
  unknown - немає даних, щоб оцінити вітер, температуру чи вологість.
Обмерзання, близька до порогу підошва хмар і низька достовірність дальніх днів
подаються як попередження і колір не змінюють.

Вікна: безперервні відрізки з годин green/yellow тривалістю від window_min_hours.
Режим «Весь район»: година червона, якщо вона червона хоч в одній точці.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"
AREA_ID = "area"
AREA_NAME = "Весь район"


def fmt(value):
    """Число з комою: 14 -> '14', 15.25 -> '15,3'."""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}".replace(".", ",")


def vals(mapping, heights):
    """[(висота, значення)] без порожніх значень."""
    return [(h, mapping.get(str(h))) for h in heights if mapping.get(str(h)) is not None]


def at_heights(pairs, unit):
    return ", ".join(f"{h} м ({fmt(v)} {unit})" for h, v in pairs)


def evaluate_hour(hour, aircraft, rules):
    lim = aircraft["limits"]
    heights = rules["heights_m_agl"]
    red, yellow, warnings, missing = [], [], [], []

    # Температура на всіх висотах
    temps = vals(hour.get("temp_c", {}), heights)
    if not temps:
        missing.append("температура")
    t_min, t_max = lim["temp_c"]["min"], lim["temp_c"]["max"]
    bad = [(h, v) for h, v in temps if v < t_min or v > t_max]
    if bad:
        red.append(f"Температура поза межами від {fmt(t_min)} до {fmt(t_max)} °C: " + at_heights(bad, "°C"))

    # Вітер на всіх висотах
    winds = vals(hour.get("wind_ms", {}), heights)
    if not winds:
        missing.append("вітер")
    w_opt, w_max = lim["wind_ms"]["optimal"], lim["wind_ms"]["max"]
    over_max = [(h, v) for h, v in winds if v > w_max]
    over_opt = [(h, v) for h, v in winds if w_opt < v <= w_max]
    if over_max:
        red.append(f"Вітер вище максимуму ({fmt(w_max)} м/с): " + at_heights(over_max, "м/с"))
    if over_opt:
        yellow.append(f"Вітер вище оптимального ({fmt(w_opt)} м/с, максимум {fmt(w_max)} м/с): "
                      + at_heights(over_opt, "м/с"))

    # Пориви на землі
    gust = hour.get("gust_ground_ms")
    g_max = lim["gust_ground_ms"]["max"]
    if gust is None:
        missing.append("пориви")
    elif gust > g_max:
        red.append(f"Пориви на землі {fmt(gust)} м/с, максимум {fmt(g_max)} м/с")

    # Вологість на всіх висотах
    rhs = vals(hour.get("rh_pct", {}), heights)
    if not rhs:
        missing.append("вологість")
    rh_max = lim["humidity_pct"]["max"]
    wet = [(h, v) for h, v in rhs if v > rh_max]
    if wet:
        red.append(f"Вологість вище {fmt(rh_max)} %: " + at_heights(wet, "%"))

    # Підошва хмар (None означає, що хмар у шарі профілю немає)
    base = hour.get("cloud_base_m")
    c_min = lim["cloud_base_m"]["min"]
    tol = rules.get("cloud_base_tolerance_m", 100)
    if base is not None:
        if base < c_min:
            red.append(f"Підошва хмар {base} м, мінімум {c_min} м")
        elif base < c_min + tol:
            warnings.append(f"Підошва хмар {base} м близько до мінімуму {c_min} м (похибка оцінки до ±{tol} м)")

    # Опади
    pr = rules["precipitation"]
    code = hour.get("weather_code")
    precip = hour.get("precip_mm") or 0.0
    snowfall = hour.get("snowfall_cm") or 0.0
    rain_limit = pr["rain_red_above_mm_per_h"]
    if code in pr["wet_snow_codes_red"]:
        red.append("Мокрий сніг або дощ зі снігом")
    elif code in pr["snow_grains_codes_yellow"]:
        yellow.append("Опади: " + pr["hints"]["77"] + " (політ можливий, будьте уважні)")
    elif code in pr["rain_codes"] and snowfall > 0 and precip > rain_limit:
        red.append(f"Дощ зі снігом, опади {fmt(precip)} мм/год")
    elif code in pr["snow_codes_yellow"] or (snowfall > 0 and code not in pr["rain_codes"]):
        yellow.append("Опади: " + pr["hints"]["snow"])
    elif precip > rain_limit:
        red.append(f"Дощ {fmt(precip)} мм/год, межа {fmt(rain_limit)} мм/год")

    # Туман і видимість
    fog = rules["fog"]
    vis = hour.get("visibility_m")
    if vis is not None and vis < fog["visibility_red_below_m"]:
        red.append(f"Туман або низька видимість: {fmt(vis)} м, потрібно не менше {fog['visibility_red_below_m']} м")
    elif code in fog["fog_codes"]:
        red.append("Туман (за кодом погоди)")

    # Обмерзання: лише попередження
    icing = hour.get("icing_from_m")
    if icing is not None:
        warnings.append(f"Можливе обмерзання з висоти {icing} м")

    if red:
        status = RED
    elif missing and any(m in missing for m in ("вітер", "температура", "вологість")):
        status = UNKNOWN
    elif yellow:
        status = YELLOW
    else:
        status = GREEN

    result = {"t": hour["t"], "status": status}
    reasons = [{"level": RED, "text": r} for r in red] + [{"level": YELLOW, "text": y} for y in yellow]
    if reasons:
        result["reasons"] = reasons
    if warnings:
        result["warnings"] = warnings
    if missing:
        result["missing"] = missing
    return result


def parse_t(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M")


def find_windows(hours, min_hours):
    """Безперервні відрізки з годин green/yellow довжиною від min_hours."""
    runs, current, prev = [], [], None
    for h in hours:
        t = parse_t(h["t"])
        contiguous = prev is None or t - prev == timedelta(hours=1)
        if not contiguous and current:
            runs.append(current)
            current = []
        if h["status"] in (GREEN, YELLOW):
            current.append(h)
        elif current:
            runs.append(current)
            current = []
        prev = t
    if current:
        runs.append(current)

    windows = []
    for run in runs:
        if len(run) < min_hours:
            continue
        end = parse_t(run[-1]["t"]) + timedelta(hours=1)
        windows.append({
            "start": run[0]["t"],
            "end": end.strftime("%Y-%m-%dT%H:%M"),
            "hours": len(run),
            "green": sum(1 for h in run if h["status"] == GREEN),
            "yellow": sum(1 for h in run if h["status"] == YELLOW),
            "low_confidence": any(h.get("low_confidence") for h in run),
        })
    return windows


def combine_area(named_hours):
    """named_hours: {назва точки: [оцінені години]}. Година району червона,
    якщо вона червона хоч в одній точці; причини підписуються назвою точки."""
    names = list(named_hours)
    by_time = {n: {h["t"]: h for h in named_hours[n]} for n in names}
    combined = []
    for base in named_hours[names[0]]:
        t = base["t"]
        items = [(n, by_time[n].get(t)) for n in names]
        statuses = [h["status"] for _, h in items if h is not None]
        if RED in statuses:
            status = RED
        elif UNKNOWN in statuses or len(statuses) < len(names):
            status = UNKNOWN
        elif YELLOW in statuses:
            status = YELLOW
        else:
            status = GREEN
        row = {"t": t, "status": status}
        reasons, warnings = [], []
        for n, h in items:
            if h is None:
                continue
            reasons += [{"level": r["level"], "text": f"{n}: {r['text']}"} for r in h.get("reasons", [])]
            warnings += [f"{n}: {w}" for w in h.get("warnings", [])]
        if reasons:
            row["reasons"] = reasons
        if warnings:
            row["warnings"] = warnings
        if base.get("low_confidence"):
            row["low_confidence"] = True
        combined.append(row)
    return combined


def load_aircraft():
    files = sorted((ROOT / "config" / "aircraft").glob("*.json"))
    aircraft = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    # порядок кнопок на сторінці: за полем "order", далі за назвою
    return sorted(aircraft, key=lambda a: (a.get("order", 999), a["name"]))


def assess(forecast, aircraft_list, rules):
    window_min = rules["window_min_hours"]
    low_from = rules["low_confidence_from_day"]
    first_date = parse_t(forecast["locations"][0]["hours"][0]["t"]).date()

    def low_confidence(t):
        return (parse_t(t).date() - first_date).days + 1 >= low_from

    results = {}
    per_aircraft_area = {a["id"]: {} for a in aircraft_list}
    for loc in forecast["locations"]:
        results[loc["id"]] = {}
        for ac in aircraft_list:
            hours = []
            for raw in loc["hours"]:
                row = evaluate_hour(raw, ac, rules)
                if low_confidence(raw["t"]):
                    row["low_confidence"] = True
                hours.append(row)
            results[loc["id"]][ac["id"]] = {"hours": hours, "windows": find_windows(hours, window_min)}
            per_aircraft_area[ac["id"]][loc["name"]] = hours

    results[AREA_ID] = {}
    for ac in aircraft_list:
        hours = combine_area(per_aircraft_area[ac["id"]])
        results[AREA_ID][ac["id"]] = {"hours": hours, "windows": find_windows(hours, window_min)}

    locations = [{"id": l["id"], "name": l["name"]} for l in forecast["locations"]]
    locations.append({"id": AREA_ID, "name": AREA_NAME})
    return {
        "forecast_generated_at": forecast["generated_at"],
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": forecast.get("source"),
        "timezone": forecast.get("timezone"),
        "first_day": first_date.isoformat(),
        "window_min_hours": window_min,
        "low_confidence_from_day": low_from,
        "aircraft": [{"id": a["id"], "name": a["name"], "limits": a["limits"]} for a in aircraft_list],
        "locations": locations,
        "default_location": None,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forecast", default="data/forecast.json")
    parser.add_argument("--output", default="data/assessment.json")
    args = parser.parse_args()

    rules = json.loads((ROOT / "config/rules.json").read_text(encoding="utf-8"))
    forecast = json.loads((ROOT / args.forecast).read_text(encoding="utf-8"))
    aircraft_list = load_aircraft()
    if not aircraft_list:
        sys.exit("У config/aircraft немає жодного літака.")

    out = assess(forecast, aircraft_list, rules)
    out["default_location"] = json.loads(
        (ROOT / "config/locations.json").read_text(encoding="utf-8")).get("default")

    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out_path)
    n_windows = sum(len(v["windows"]) for loc in out["results"].values() for v in loc.values())
    print(f"Збережено {args.output}: {len(out['locations'])} режимів, "
          f"{len(aircraft_list)} літаків, вікон загалом: {n_windows}.")


if __name__ == "__main__":
    main()
