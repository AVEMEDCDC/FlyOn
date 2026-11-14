"""Тести логіки оцінки. Запуск з кореня проєкту: python -m unittest discover -s tests -v"""
import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate as ev  # noqa: E402

# Тести перевіряють ЛОГІКУ на власних фіксованих даних, а не на ваших конфігах:
# змінення порогів у config/ не повинно ламати тести й автооновлення сайту.
RULES = {
    "heights_m_agl": [100, 550, 750, 1000, 1500, 2000, 2500, 3000],
    "window_min_hours": 4,
    "low_confidence_from_day": 4,
    "cloud_base_tolerance_m": 100,
    "precipitation": {
        "rain_red_above_mm_per_h": 0.1,
        "rain_codes": [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82],
        "wet_snow_codes_red": [68, 69],
        "snow_grains_codes_yellow": [77],
        "snow_codes_yellow": [71, 73, 75, 85, 86],
        "hints": {"77": "сніг крупою", "snow": "сніг, перевірте тип візуально"},
    },
    "fog": {"visibility_red_below_m": 2000, "fog_codes": [45, 48]},
    "icing": {"role": "warning_only", "temp_range_c": [-10, 0], "humidity_min_pct": 90},
}
LELEKA = {"id": "t-leleka", "name": "Тест-1", "limits": {
    "temp_c": {"min": -30, "max": 50}, "wind_ms": {"optimal": 14, "max": 19},
    "gust_ground_ms": {"max": 12}, "humidity_pct": {"max": 90}, "cloud_base_m": {"min": 800}}}
FURIYA = {"id": "t-furiya", "name": "Тест-2", "limits": {
    "temp_c": {"min": -30, "max": 50}, "wind_ms": {"optimal": 8, "max": 10},
    "gust_ground_ms": {"max": 10}, "humidity_pct": {"max": 90}, "cloud_base_m": {"min": 600}}}
HEIGHTS = RULES["heights_m_agl"]


def make_hour(**over):
    hour = {
        "t": "2026-11-13T12:00",
        "wind_ms": {str(h): 5.0 for h in HEIGHTS},
        "wind_dir": {str(h): 0 for h in HEIGHTS},
        "temp_c": {str(h): 5.0 for h in HEIGHTS},
        "rh_pct": {str(h): 60 for h in HEIGHTS},
        "gust_ground_ms": 5.0,
        "cloud_base_m": None,
        "icing_from_m": None,
        "precip_mm": 0.0,
        "snowfall_cm": 0.0,
        "weather_code": 3,
        "visibility_m": 20000,
        "cloud_cover_pct": 10,
    }
    hour.update(over)
    return hour


def with_wind(value, height=100):
    wind = {str(h): 5.0 for h in HEIGHTS}
    wind[str(height)] = value
    return make_hour(wind_ms=wind)


def status(hour, aircraft=LELEKA):
    return ev.evaluate_hour(hour, aircraft, RULES)["status"]


class HourStatus(unittest.TestCase):
    def test_calm_is_green(self):
        self.assertEqual(status(make_hour()), ev.GREEN)
        self.assertEqual(status(make_hour(), FURIYA), ev.GREEN)

    def test_wind_thresholds_leleka(self):
        self.assertEqual(status(with_wind(14.0)), ev.GREEN)    # рівно оптимум
        self.assertEqual(status(with_wind(14.1)), ev.YELLOW)
        self.assertEqual(status(with_wind(19.0)), ev.YELLOW)   # рівно максимум
        self.assertEqual(status(with_wind(19.1)), ev.RED)

    def test_wind_thresholds_furiya(self):
        self.assertEqual(status(with_wind(8.0), FURIYA), ev.GREEN)
        self.assertEqual(status(with_wind(9.0), FURIYA), ev.YELLOW)
        self.assertEqual(status(with_wind(10.0), FURIYA), ev.YELLOW)
        self.assertEqual(status(with_wind(10.1), FURIYA), ev.RED)

    def test_wind_matters_up_to_work_altitude_only(self):
        # Лелека: мін. хмари 800 -> робоча висота 750 м. Рівні до 750 включно обов'язкові.
        for h in HEIGHTS:
            expected = ev.RED if h <= 750 else ev.GREEN
            self.assertEqual(status(with_wind(20.0, h)), expected, f"Лелека, висота {h}")
        # Фурія: мін. хмари 600 -> робоча висота 550 м.
        for h in HEIGHTS:
            expected = ev.RED if h <= 550 else ev.GREEN
            self.assertEqual(status(with_wind(11.0, h), FURIYA), expected, f"Фурія, висота {h}")

    def test_same_weather_differs_by_aircraft(self):
        hour = with_wind(12.0)
        self.assertEqual(status(hour, LELEKA), ev.GREEN)
        self.assertEqual(status(hour, FURIYA), ev.RED)

    def test_gusts(self):
        self.assertEqual(status(make_hour(gust_ground_ms=12.0)), ev.GREEN)
        self.assertEqual(status(make_hour(gust_ground_ms=12.1)), ev.RED)
        self.assertEqual(status(make_hour(gust_ground_ms=10.0), FURIYA), ev.GREEN)
        self.assertEqual(status(make_hour(gust_ground_ms=10.1), FURIYA), ev.RED)

    def test_humidity_matters_up_to_work_altitude_only(self):
        for h in HEIGHTS:
            rh = {str(x): 60 for x in HEIGHTS}
            rh[str(h)] = 91
            expected = ev.RED if h <= 750 else ev.GREEN
            self.assertEqual(status(make_hour(rh_pct=rh)), expected, f"висота {h}")
        rh = {str(x): 90 for x in HEIGHTS}
        self.assertEqual(status(make_hour(rh_pct=rh)), ev.GREEN)

    def test_temperature_matters_up_to_work_altitude_only(self):
        for bad in (-30.1, 50.1):
            for h in HEIGHTS:
                temp = {str(x): 5.0 for x in HEIGHTS}
                temp[str(h)] = bad
                expected = ev.RED if h <= 750 else ev.GREEN
                self.assertEqual(status(make_hour(temp_c=temp)), expected, f"{bad} на {h} м")
        temp = {str(x): -30.0 for x in HEIGHTS}
        self.assertEqual(status(make_hour(temp_c=temp)), ev.GREEN)

    def test_cloud_base(self):
        self.assertEqual(status(make_hour(cloud_base_m=799)), ev.RED)     # Лелека мін. 800
        self.assertEqual(status(make_hour(cloud_base_m=1500)), ev.GREEN)
        self.assertEqual(status(make_hour(cloud_base_m=700), FURIYA), ev.GREEN)  # Фурія мін. 600
        self.assertEqual(status(make_hour(cloud_base_m=599), FURIYA), ev.RED)
        self.assertEqual(status(make_hour(cloud_base_m=None)), ev.GREEN)

    def test_cloud_base_near_limit_is_only_warning(self):
        res = ev.evaluate_hour(make_hour(cloud_base_m=850), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertTrue(res["warnings"])

    def test_rain(self):
        self.assertEqual(status(make_hour(precip_mm=0.1, weather_code=61)), ev.GREEN)
        self.assertEqual(status(make_hour(precip_mm=0.2, weather_code=61)), ev.RED)
        self.assertEqual(status(make_hour(precip_mm=0.5, weather_code=3)), ev.RED)

    def test_snow_grains_is_yellow_with_hint(self):
        res = ev.evaluate_hour(make_hour(weather_code=77, precip_mm=0.2, snowfall_cm=0.3), LELEKA, RULES)
        self.assertEqual(res["status"], ev.YELLOW)
        self.assertIn("сніг крупою", res["reasons"][0]["text"])

    def test_ordinary_snow_is_yellow_with_hint(self):
        res = ev.evaluate_hour(make_hour(weather_code=73, precip_mm=0.4, snowfall_cm=0.5), LELEKA, RULES)
        self.assertEqual(res["status"], ev.YELLOW)
        self.assertIn("перевірте тип візуально", res["reasons"][0]["text"])

    def test_sleet_is_red(self):
        self.assertEqual(status(make_hour(weather_code=68)), ev.RED)
        self.assertEqual(status(make_hour(weather_code=61, precip_mm=0.4, snowfall_cm=0.2)), ev.RED)

    def test_fog_and_visibility(self):
        self.assertEqual(status(make_hour(visibility_m=2000)), ev.GREEN)
        self.assertEqual(status(make_hour(visibility_m=1999)), ev.RED)
        self.assertEqual(status(make_hour(weather_code=45, visibility_m=None)), ev.RED)

    def test_icing_is_warning_only(self):
        res = ev.evaluate_hour(make_hour(icing_from_m=3500), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertIn("3500", res["warnings"][0])

    def test_missing_wind_is_unknown(self):
        res = ev.evaluate_hour(make_hour(wind_ms={str(h): None for h in HEIGHTS}), LELEKA, RULES)
        self.assertEqual(res["status"], ev.UNKNOWN)
        self.assertIn("вітер", res["missing"])

    def test_red_wins_over_yellow(self):
        res = ev.evaluate_hour(with_wind(15.0), LELEKA, RULES)
        self.assertEqual(res["status"], ev.YELLOW)
        res = ev.evaluate_hour(make_hour(wind_ms={**{str(h): 15.0 for h in HEIGHTS}, "100": 20.0}),
                               LELEKA, RULES)
        self.assertEqual(res["status"], ev.RED)


class FlightLayer(unittest.TestCase):
    """Логіка «шару підйому»: літак піднімається від землі до робочої висоти."""

    def test_work_altitude_rounds_to_nearest_level(self):
        self.assertEqual(ev.work_altitude(HEIGHTS, 800), 750)
        self.assertEqual(ev.work_altitude(HEIGHTS, 600), 550)
        self.assertEqual(ev.work_altitude(HEIGHTS, 1000), 1000)
        self.assertEqual(ev.work_altitude(HEIGHTS, 650), 550)   # рівна відстань: нижчий рівень

    def test_user_example_good_to_1000_bad_above(self):
        wind = {str(h): 5.0 for h in HEIGHTS}
        for h in (1500, 2000, 2500, 3000):
            wind[str(h)] = 25.0
        res = ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertEqual((res["work_alt_m"], res["ceiling_m"], res["stop_m"]), (750, 1000, 1500))
        self.assertIn("750–1000", res["info"][0])
        self.assertIn("1500", res["info"][1])

    def test_bad_wind_inside_required_layer_is_red(self):
        for bad_level in (100, 550, 750):
            wind = {str(h): 5.0 for h in HEIGHTS}
            wind[str(bad_level)] = 25.0
            res = ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)
            self.assertEqual(res["status"], ev.RED, f"поганий рівень {bad_level}")

    def test_narrow_layer_is_allowed_with_warning(self):
        wind = {str(h): 5.0 for h in HEIGHTS}
        wind["1000"] = 25.0   # перший поганий рівень одразу над робочою висотою 750
        res = ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertEqual(res["ceiling_m"], 750)
        self.assertTrue(any("Вузький шар" in w for w in res["warnings"]))

    def test_wind_stops_ceiling_even_if_higher_levels_are_fine(self):
        wind = {str(h): 5.0 for h in HEIGHTS}
        wind["1500"] = 25.0   # вище 1500 вітер знову гарний, але підйом туди неможливий
        res = ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)
        self.assertEqual(res["ceiling_m"], 1000)

    def test_humidity_above_ceiling_does_not_matter(self):
        rh = {str(h): 60 for h in HEIGHTS}
        rh["3000"] = 97
        res = ev.evaluate_hour(make_hour(rh_pct=rh), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertEqual(res["ceiling_m"], 2500)

    def test_yellow_only_in_required_layer(self):
        wind = {str(h): 5.0 for h in HEIGHTS}
        wind["2000"] = 16.0   # вище оптимального, але над робочою висотою Лелеки
        self.assertEqual(ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)["status"], ev.GREEN)
        wind = {str(h): 5.0 for h in HEIGHTS}
        wind["550"] = 16.0
        self.assertEqual(ev.evaluate_hour(make_hour(wind_ms=wind), LELEKA, RULES)["status"], ev.YELLOW)

    def test_ground_conditions_still_apply_to_whole_hour(self):
        self.assertEqual(status(make_hour(gust_ground_ms=15.0)), ev.RED)
        self.assertEqual(status(make_hour(precip_mm=0.5, weather_code=61)), ev.RED)
        self.assertEqual(status(make_hour(visibility_m=1500)), ev.RED)
        self.assertEqual(status(make_hour(cloud_base_m=300)), ev.RED)

    def test_icing_inside_flight_layer_is_flagged(self):
        res = ev.evaluate_hour(make_hour(icing_from_m=900), LELEKA, RULES)
        self.assertEqual(res["status"], ev.GREEN)
        self.assertTrue(any("в межах шару польоту" in w for w in res["warnings"]))

    def test_area_ceiling_is_the_lowest(self):
        a = [{"t": "2026-11-13T12:00", "status": G, "work_alt_m": 750, "ceiling_m": 2000, "stop_m": 2500}]
        b = [{"t": "2026-11-13T12:00", "status": G, "work_alt_m": 750, "ceiling_m": 1000, "stop_m": 1500}]
        out = ev.combine_area({"А": a, "Б": b})
        self.assertEqual((out[0]["ceiling_m"], out[0]["stop_m"]), (1000, 1500))


def timeline(pattern, start="2026-11-13T00:00"):
    t0 = datetime.strptime(start, "%Y-%m-%dT%H:%M")
    return [{"t": (t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M"), "status": s}
            for i, s in enumerate(pattern)]


G, Y, R, U = ev.GREEN, ev.YELLOW, ev.RED, ev.UNKNOWN


class Windows(unittest.TestCase):
    def test_four_hours_is_a_window_three_is_not(self):
        w = ev.find_windows(timeline([R, G, G, G, R, G, G, G, G, R]), 4)
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0]["start"], w[0]["end"], w[0]["hours"]),
                         ("2026-11-13T05:00", "2026-11-13T09:00", 4))

    def test_yellow_counts_inside_window(self):
        w = ev.find_windows(timeline([G, Y, Y, G, G]), 4)
        self.assertEqual(len(w), 1)
        self.assertEqual((w[0]["hours"], w[0]["green"], w[0]["yellow"]), (5, 3, 2))

    def test_red_or_unknown_breaks_window(self):
        self.assertEqual(ev.find_windows(timeline([G, G, R, G, G]), 4), [])
        self.assertEqual(ev.find_windows(timeline([G, G, U, G, G]), 4), [])

    def test_window_across_midnight(self):
        w = ev.find_windows(timeline([G] * 6, start="2026-11-13T21:00"), 4)
        self.assertEqual(w[0]["start"], "2026-11-13T21:00")
        self.assertEqual(w[0]["end"], "2026-11-14T03:00")

    def test_gap_in_time_breaks_window(self):
        hours = timeline([G, G]) + timeline([G, G], start="2026-11-13T05:00")
        self.assertEqual(ev.find_windows(hours, 4), [])

    def test_low_confidence_flag(self):
        hours = timeline([G] * 4)
        hours[2]["low_confidence"] = True
        self.assertTrue(ev.find_windows(hours, 4)[0]["low_confidence"])


class Area(unittest.TestCase):
    def test_one_red_point_makes_area_red(self):
        a = timeline([G, G, G])
        b = timeline([G, R, G])
        for h in b:
            if h["status"] == R:
                h["reasons"] = [{"level": R, "text": "Дощ"}]
        out = ev.combine_area({"Точка А": a, "Точка Б": b})
        self.assertEqual([h["status"] for h in out], [G, R, G])
        self.assertEqual(out[1]["reasons"][0]["text"], "Точка Б: Дощ")

    def test_yellow_if_no_red(self):
        out = ev.combine_area({"А": timeline([G, Y]), "Б": timeline([G, G])})
        self.assertEqual([h["status"] for h in out], [G, Y])

    def test_unknown_when_point_missing_hour(self):
        out = ev.combine_area({"А": timeline([G, G]), "Б": timeline([G])})
        self.assertEqual(out[1]["status"], U)


class ConfigFiles(unittest.TestCase):
    """Перевіряє лише ФОРМУ файлів у config/, а не конкретні значення.
    Якщо хтось додав літак з помилкою, автооновлення впаде з зрозумілим повідомленням."""

    def test_aircraft_files_are_valid(self):
        files = sorted((ROOT / "config" / "aircraft").glob("*.json"))
        self.assertTrue(files, "У config/aircraft немає жодного файлу літака")
        ids = set()
        for path in files:
            data = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(file=path.name):
                for key in ("id", "name", "limits"):
                    self.assertIn(key, data, f"{path.name}: немає поля {key}")
                self.assertNotIn(data["id"], ids, f"{path.name}: id повторюється")
                ids.add(data["id"])
                lim = data["limits"]
                self.assertLessEqual(lim["temp_c"]["min"], lim["temp_c"]["max"])
                self.assertLessEqual(lim["wind_ms"]["optimal"], lim["wind_ms"]["max"],
                                     f"{path.name}: оптимальний вітер більший за максимальний")
                for section, field in (("gust_ground_ms", "max"), ("humidity_pct", "max"), ("cloud_base_m", "min")):
                    self.assertIsInstance(lim[section][field], (int, float), f"{path.name}: {section}.{field}")

    def test_locations_file_is_valid(self):
        data = json.loads((ROOT / "config/locations.json").read_text(encoding="utf-8"))
        ids = [l["id"] for l in data["locations"]]
        self.assertEqual(len(ids), len(set(ids)), "id точок повторюються")
        self.assertIn(data["default"], ids)
        for loc in data["locations"]:
            if loc.get("lat") is not None:
                self.assertTrue(-90 <= loc["lat"] <= 90 and -180 <= loc["lon"] <= 180, loc["name"])


class EndToEnd(unittest.TestCase):
    def test_assess_builds_all_modes(self):
        hours = []
        t0 = datetime(2026, 11, 13, 0, 0)
        for i in range(24 * 6):
            hours.append(make_hour(t=(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")))
        forecast = {"generated_at": "2026-11-13T10:00:00Z", "source": "test", "timezone": "Europe/Kyiv",
                    "locations": [{"id": "a", "name": "А", "hours": hours},
                                  {"id": "b", "name": "Б", "hours": hours}]}
        out = ev.assess(forecast, [LELEKA, FURIYA], RULES)
        self.assertEqual([l["id"] for l in out["locations"]], ["a", "b", "area"])
        self.assertEqual(len(out["results"]["area"][FURIYA["id"]]["windows"]), 1)
        # день 4 і далі позначені низькою достовірністю
        flags = [h.get("low_confidence", False) for h in out["results"]["a"][FURIYA["id"]]["hours"]]
        self.assertFalse(flags[0])
        self.assertFalse(flags[24 * 2 + 23])
        self.assertTrue(flags[24 * 3])


if __name__ == "__main__":
    unittest.main()
