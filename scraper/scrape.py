#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tracker cen wycieczek z wakacje.pl — HISTORIA CENY KAZDEJ OFERTY Z OSOBNA.

Dla kazdego kierunku enumeruje WSZYSTKIE oferty w zadanym oknie (daty + dlugosc),
przesuwajac okno cenowe (minPrice/maxPrice) tak, by w jednym zapytaniu zmiescic
caly przedzial (API zwraca max ~300 ofert/zapytanie, ranking jest niedeterministyczny,
ale banding po cenie jest rozlaczny i deterministyczny).

Kazda oferta ma stabilny klucz (hotel|daty|wyzywienie|operator) i wlasna historie cen.
Uruchamiany 2x dziennie przez GitHub Actions buduje historie "w przod".
"""

import json
import os
import sys
import time
import re
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
#  KONFIGURACJA
# --------------------------------------------------------------------------
DESTINATIONS = {            # nazwa -> countryId wakacje.pl
    "Egipt":  37,
    "Turcja": 16,
}
DEPARTURE_DATE = "2026-06-28"
ARRIVAL_DATE   = "2026-07-12"
DURATION_MIN   = 14
DURATION_MAX   = 17
ADULTS         = 2
DEPARTURE_CITY = None       # None = dowolne lotnisko
SERVICE_FILTER = [1]        # [1] = tylko All Inclusive (1=AI, 2=HB, 4=wlasne); [] = wszystkie

BAND_LIMIT  = 300           # max ofert na jedno zapytanie (limit API)
MAX_TRACK   = 500           # ile NAJTANSZYCH ofert sledzic na kierunek (caly realny zakres last-minute)
MAX_FETCHES = 18            # twardy limit zapytan-pasm na kierunek (czas + grzecznosc)
STEP_START  = 600           # poczatkowa szerokosc pasma cenowego (zl)
STEP_MIN    = 60            # min szerokosc pasma (gdy gesto)
STEP_MAX    = 4000          # max szerokosc pasma (gdy rzadko)
PRICE_HI    = 90000         # gorna granica ceny
REQ_DELAY   = 1.0           # odstep miedzy zapytaniami: LAGODNE tempo eliminuje throttling (burst)
# --------------------------------------------------------------------------

API_URL = "https://www.wakacje.pl/v2/api/offers"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Content-Type": "application/json", "Accept": "application/json",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Origin": "https://www.wakacje.pl", "Referer": "https://www.wakacje.pl/wczasy/",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DATA_DIR = os.path.join(ROOT, "docs", "data")


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build_body(country_id, limit, min_price=None, max_price=None):
    query = {
        "campTypes": [], "qsVersion": 0, "qsVersionLast": 0,
        "tab": False, "candy": False, "pok": None, "flush": False,
        "obj_xCode": None, "tourOpAndCode": None, "obj_code": None, "obj_type": None,
        "catalog": None, "roomType": None, "test": None, "year": None, "month": None,
        "rangeDate": None, "withoutLast": 0, "category": False, "not-attribute": False,
        "pageNumber": 1,
        "departureDate": DEPARTURE_DATE, "arrivalDate": ARRIVAL_DATE,
        "departure": DEPARTURE_CITY, "type": [],
        "duration": {"min": DURATION_MIN, "max": DURATION_MAX},
        "minPrice": min_price, "maxPrice": max_price,
        "service": SERVICE_FILTER, "firstminute": None, "attribute": [], "promotion": [],
        "tourId": None, "search": None, "minCategory": None, "maxCategory": 50,
        "sort": None, "order": None, "rank": None,
        "withoutTours": [], "withoutCountry": [], "withoutTrips": [],
        "rooms": [{"adult": ADULTS, "kid": 0, "ages": [], "inf": None}],
        "offerCode": None,
    }
    params = {
        "searchType": "wczasy", "brand": "WAK", "limit": limit,
        "priceHistory": 1, "imageSizes": ["570,428"], "flatArray": True,
        "multiSearch": True, "withHotelRate": 1, "withPromoOffer": 1,
        "recommendationVersion": "noTUI", "type": "tours", "firstMinuteTui": False,
        "cityId": [], "regionId": [], "countryId": [country_id],
        "hotelId": [], "roundTripId": [], "cruiseId": [], "offersAttributes": [],
        "alternative": {"countryId": [], "regionId": [], "cityId": []},
        "query": query,
    }
    return [{"method": "search.tripsSearch", "params": params}]


def query(country_id, min_price=None, max_price=None, limit=BAND_LIMIT, retries=2):
    """Zwraca (count, [offers]) lub (None, [])."""
    for attempt in range(retries):
        try:
            r = requests.post(API_URL, headers=HEADERS,
                              json=build_body(country_id, limit, min_price, max_price), timeout=45)
            data = json.loads(r.content.decode("utf-8"))
            if not data.get("success"):
                msg = data.get("error", {}).get("message", "?")
                print(f"    ! API blad (<= {max_price}): {msg[-70:]}", file=sys.stderr)
                time.sleep(1.2 * (attempt + 1)); continue
            d = data["data"]
            return d.get("count"), d.get("offers", [])
        except Exception as e:
            print(f"    ! wyjatek (proba {attempt+1}): {e}", file=sys.stderr)
            time.sleep(1.2 * (attempt + 1))
    return None, []


def count_below(country_id, price):
    """Liczba ofert z cena <= price (maxPrice). price=None -> wszystkie."""
    c, _ = query(country_id, max_price=price, limit=1)
    time.sleep(REQ_DELAY)
    return c or 0


def offer_key(o):
    return "|".join(str(x) for x in (
        o.get("hotelId"), o.get("departureDate"), o.get("returnDate"),
        o.get("serviceDesc"), o.get("tourOperator")))


def offer_url(o):
    """Buduje gleboki link do KONKRETNEJ oferty na dany termin (2 os. = domyslne wakacje.pl).
    Wzorzec wakacje.pl: /oferty/{kraj}/{region}/{miasto}/{urlName}-{hotelId}.html?od-...,do-..."""
    place = o.get("place", {}) or {}
    parts = []
    for key in ("country", "region", "city"):
        v = place.get(key)
        if v and v.get("slug"):
            parts.append(v["slug"])
    seg = "/".join(parts)
    url_name, hid = o.get("urlName"), o.get("hotelId")
    dep, ret = o.get("departureDate"), o.get("returnDate")
    if seg and url_name and hid:
        url = f"https://www.wakacje.pl/oferty/{seg}/{url_name}-{hid}.html"
        if dep and ret:
            url += f"?od-{dep},do-{ret}"
        return url
    return "https://www.wakacje.pl/"


def slim(o):
    return {
        "name": o.get("name"), "place": o.get("placeName"),
        "hotelId": o.get("hotelId"), "urlName": o.get("urlName"),
        "departureDate": o.get("departureDate"), "returnDate": o.get("returnDate"),
        "duration": o.get("duration"), "service": o.get("serviceDesc"),
        "operator": o.get("tourOperatorName"), "departureFrom": o.get("departurePlace"),
        "category": o.get("category"), "rating": o.get("ratingValue") or None,
        "url": offer_url(o),
    }


def enumerate_offers(country_id):
    """Zbiera NAJTANSZE ~MAX_TRACK ofert kierunku, przesuwajac okno cenowe od dolu.
    Lagodne tempo + twardy limit zapytan = odporne na throttling (burst).
    Zwraca ({key: raw_offer}, total_dostepnych)."""
    total = count_below(country_id, None)      # 1 lekkie zapytanie: cala dostepnosc
    offers = {}
    lo = 0
    step = STEP_START
    fetches = 0
    while fetches < MAX_FETCHES and len(offers) < MAX_TRACK and lo <= PRICE_HI:
        hi = min(lo + step, PRICE_HI)
        count, band = query(country_id, min_price=(lo or None), max_price=hi, limit=BAND_LIMIT)
        fetches += 1
        time.sleep(REQ_DELAY)
        if count is None:                      # twardy blad mimo ponowien -> pomijamy pasmo
            lo = hi + 1; continue
        if count >= BAND_LIMIT and step > STEP_MIN:
            step = max(STEP_MIN, step // 2)    # za szerokie pasmo (>limit) -> zwez i powtorz
            continue
        for o in band:
            if not o.get("price"):
                continue
            k = offer_key(o)
            if k not in offers or o["price"] < offers[k]["price"]:
                offers[k] = o
        # adaptacja szerokosci pasma do gestosci
        if count == 0:
            step = min(STEP_MAX, step * 2)
        elif count < BAND_LIMIT * 0.4:
            step = min(STEP_MAX, int(step * 1.5))
        elif count > BAND_LIMIT * 0.75:
            step = max(STEP_MIN, int(step * 0.6))
        if hi >= PRICE_HI:
            break
        lo = hi + 1
    return offers, total


def update_store(dest, slug, scraped, ts, run_iso):
    """Wczytuje data/offers_<slug>.json, dopisuje obserwacje cen, zapisuje."""
    path = os.path.join(DATA_DIR, f"offers_{slug}.json")
    store = {"dest": dest, "offers": {}}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            store = json.load(f)
    offers = store.setdefault("offers", {})

    # oznacz wszystkie jako nieaktywne; aktywne te, ktore znowu widzimy
    for e in offers.values():
        e["active"] = False

    new_cnt = changed_cnt = 0
    for k, raw in scraped.items():
        price = raw["price"]
        meta = slim(raw)
        if k in offers:
            e = offers[k]
            e.update(meta)               # odswiez metadane
            e["active"] = True
            e["last"] = run_iso
            e["lastPrice"] = price
            e["minPrice"] = min(e["minPrice"], price)
            e["maxPrice"] = max(e["maxPrice"], price)
            if not e["hist"] or e["hist"][-1][1] != price:
                e["hist"].append([run_iso, price]); changed_cnt += 1
            e["n"] = e.get("n", 0) + 1
        else:
            offers[k] = {**meta, "first": run_iso, "last": run_iso,
                         "firstPrice": price, "lastPrice": price,
                         "minPrice": price, "maxPrice": price,
                         "hist": [[run_iso, price]], "n": 1, "active": True}
            new_cnt += 1

    store["updatedAt"] = run_iso
    store["config"] = CONFIG
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    return offers, new_cnt, changed_cnt


CONFIG = {"departureDate": DEPARTURE_DATE, "arrivalDate": ARRIVAL_DATE,
          "durationMin": DURATION_MIN, "durationMax": DURATION_MAX, "adults": ADULTS,
          "departureCity": DEPARTURE_CITY or "dowolne",
          "board": "All Inclusive" if SERVICE_FILTER == [1] else "dowolne"}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)
    run_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"== Scrape {run_iso} ==")

    index = {"generatedAt": run_iso, "config": CONFIG, "destinations": {}}
    summary_line = []

    for dest, cid in DESTINATIONS.items():
        print(f"  -> {dest} (countryId={cid})")
        try:
            scraped, total = enumerate_offers(cid)
        except Exception as e:
            print(f"  !! {dest}: {e}", file=sys.stderr); continue

        slug = slugify(dest)
        offers, new_cnt, changed_cnt = update_store(dest, slug, scraped, run_iso, run_iso)

        active = [e for e in offers.values() if e["active"]]
        prices = sorted(e["lastPrice"] for e in active)
        cheapest = prices[0] if prices else None
        # licznik spadkow w tym przebiegu
        drops = sum(1 for e in active if len(e["hist"]) >= 2 and e["hist"][-1][1] < e["hist"][-2][1])
        print(f"     sledzonych {len(scraped)} najtanszych z {total} dostepnych · "
              f"nowych {new_cnt} · zmian ceny {changed_cnt} · najtansza {cheapest} zl")

        index["destinations"][dest] = {
            "slug": slug, "total": total, "active": len(active),
            "tracked": len(offers), "cheapest": cheapest,
            "median": prices[len(prices)//2] if prices else None,
            "newThisRun": new_cnt, "priceChangesThisRun": changed_cnt, "dropsThisRun": drops,
        }
        summary_line.append({"ts": run_iso, "dest": dest, "count": total,
                             "active": len(active), "minPrice": cheapest})

        # kopia per-kierunek dla dashboardu (lekka: bez nieaktywnych starszych niz... -> pelna na razie)
        import shutil
        shutil.copy(os.path.join(DATA_DIR, f"offers_{slug}.json"),
                    os.path.join(DOCS_DATA_DIR, f"offers_{slug}.json"))

    if not index["destinations"]:
        print("!! Brak danych — nic nie zapisuje.", file=sys.stderr); sys.exit(1)

    # lekka historia zbiorcza (do wykresu najtanszej ceny / dostepnosci)
    hist_path = os.path.join(DATA_DIR, "history.jsonl")
    with open(hist_path, "a", encoding="utf-8") as f:
        for s in summary_line:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    history = []
    with open(hist_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                history.append(json.loads(line))

    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    with open(os.path.join(DOCS_DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    with open(os.path.join(DOCS_DATA_DIR, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)

    print(f"== Gotowe: {len(index['destinations'])} kierunkow ==")


if __name__ == "__main__":
    main()
