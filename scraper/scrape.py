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
DEPARTURE_FROM = "2026-06-28"   # najwczesniejszy WYLOT
DEPARTURE_TO   = "2026-07-12"   # najpozniejszy WYLOT (filtr po stronie kodu)
# arrivalDate w API = "powrot do"; ustawiamy szeroko (wylot_do + max dlugosc + zapas),
# a wylot zawezamy do [DEPARTURE_FROM, DEPARTURE_TO] filtrem ponizej
RETURN_BY      = "2026-07-31"
DURATION_MIN   = 14
DURATION_MAX   = 17
ADULTS         = 2
SERVICE_FILTER = [1]        # [1] = tylko All Inclusive (1=AI, 2=HB, 4=wlasne); [] = wszystkie
PLANE_ONLY     = True       # tylko oferty samolotem (departureType=1)

# Dwa widoki lotnisk: "waw" = domyslny (tylko Warszawa, value 278 = WAW+Modlin),
# "all" = po wylaczeniu filtra (wszystkie lotniska w PL).
DEPARTURES = {
    "waw": {"ids": [278], "slug": "z-warszawy", "label": "tylko z Warszawy"},
    "all": {"ids": None,  "slug": None,         "label": "wszystkie lotniska"},
}
SERVICE_SLUG   = "all-inclusive"   # slug wyzywienia do linku
TRANSPORT_SLUG = "samolotem"       # slug transportu do linku (Samolot)

BAND_LIMIT  = 300           # max ofert na jedno zapytanie (limit API)
MAX_TRACK   = 500           # ile NAJTANSZYCH ofert sledzic na kierunek (caly realny zakres last-minute)
MAX_FETCHES = 42            # twardy limit zapytan-pasm na kierunek (czas + grzecznosc)
STEP_START  = 1200          # poczatkowa szerokosc pasma cenowego (zl)
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


def build_body(country_id, limit, min_price=None, max_price=None, departure=None):
    query = {
        "campTypes": [], "qsVersion": 0, "qsVersionLast": 0,
        "tab": False, "candy": False, "pok": None, "flush": False,
        "obj_xCode": None, "tourOpAndCode": None, "obj_code": None, "obj_type": None,
        "catalog": None, "roomType": None, "test": None, "year": None, "month": None,
        "rangeDate": None, "withoutLast": 0, "category": False, "not-attribute": False,
        "pageNumber": 1,
        "departureDate": DEPARTURE_FROM, "arrivalDate": RETURN_BY,
        "departure": departure, "type": [],
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


def query(country_id, min_price=None, max_price=None, limit=BAND_LIMIT, departure=None, retries=2):
    """Zwraca (count, [offers]) lub (None, [])."""
    for attempt in range(retries):
        try:
            r = requests.post(API_URL, headers=HEADERS,
                              json=build_body(country_id, limit, min_price, max_price, departure), timeout=45)
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


def count_below(country_id, price, departure=None):
    """Liczba ofert z cena <= price (maxPrice). price=None -> wszystkie."""
    c, _ = query(country_id, max_price=price, limit=1, departure=departure)
    time.sleep(REQ_DELAY)
    return c or 0


def offer_key(o):
    return "|".join(str(x) for x in (
        o.get("hotelId"), o.get("departureDate"), o.get("returnDate"),
        o.get("serviceDesc"), o.get("tourOperator")))


def offer_url(o, departure_slug=None):
    """Gleboki link do KONKRETNEJ oferty na dany termin, wyzywienie i transport.
    Wzorzec (parametry rozdzielone PRZECINKAMI, BEZ do-):
      /oferty/{kraj}/{region}/{miasto}/{urlName}-{offerId}.html?od-{wylot},{N}-dni,all-inclusive,samolotem[,z-warszawy]
    UWAGA: id w sciezce to offerId (hotelId przekierowuje na liste!)."""
    place = o.get("place", {}) or {}
    parts = []
    for key in ("country", "region", "city"):
        v = place.get(key)
        if v and v.get("slug"):
            parts.append(v["slug"])
    seg = "/".join(parts)
    url_name, oid = o.get("urlName"), o.get("offerId")
    dep, dur = o.get("departureDate"), o.get("duration")
    if not (seg and url_name and oid and dep and dur):
        return "https://www.wakacje.pl/"
    tokens = [f"od-{dep}", f"{dur}-dni", SERVICE_SLUG, TRANSPORT_SLUG]
    if departure_slug:
        tokens.append(departure_slug)
    return f"https://www.wakacje.pl/oferty/{seg}/{url_name}-{oid}.html?" + ",".join(tokens)


def slim(o, departure_slug=None):
    return {
        "name": o.get("name"), "place": o.get("placeName"),
        "hotelId": o.get("hotelId"), "urlName": o.get("urlName"),
        "departureDate": o.get("departureDate"), "returnDate": o.get("returnDate"),
        "duration": o.get("duration"), "service": o.get("serviceDesc"),
        "operator": o.get("tourOperatorName"), "departureFrom": o.get("departurePlace"),
        "category": o.get("category"), "rating": o.get("ratingValue") or None,
        "url": offer_url(o, departure_slug),
    }


def enumerate_offers(country_id, departure=None):
    """Zbiera NAJTANSZE ~MAX_TRACK ofert kierunku (dla danego lotniska), okno cenowe od dolu.
    Lagodne tempo + twardy limit zapytan = odporne na throttling (burst).
    Zwraca ({key: raw_offer}, total_dostepnych)."""
    total = count_below(country_id, None, departure=departure)   # 1 lekkie zapytanie: dostepnosc
    offers = {}
    lo = 0
    step = STEP_START
    fetches = 0
    while fetches < MAX_FETCHES and len(offers) < MAX_TRACK and lo <= PRICE_HI:
        hi = min(lo + step, PRICE_HI)
        count, band = query(country_id, min_price=(lo or None), max_price=hi,
                            limit=BAND_LIMIT, departure=departure)
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
            if PLANE_ONLY and o.get("departureType") != 1:    # tylko samolot
                continue
            dep = o.get("departureDate") or ""
            if not (DEPARTURE_FROM <= dep <= DEPARTURE_TO):   # tylko wylot w oknie
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


def update_store(dest, store_slug, scraped, run_iso, departure_slug=None):
    """Wczytuje data/offers_<store_slug>.json, dopisuje obserwacje cen, zapisuje."""
    path = os.path.join(DATA_DIR, f"offers_{store_slug}.json")
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
        meta = slim(raw, departure_slug)
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


CONFIG = {"departureFrom": DEPARTURE_FROM, "departureTo": DEPARTURE_TO,
          "durationMin": DURATION_MIN, "durationMax": DURATION_MAX, "adults": ADULTS,
          "board": "All Inclusive" if SERVICE_FILTER == [1] else "dowolne",
          "transport": "Samolot" if PLANE_ONLY else "dowolny",
          "views": {k: v["label"] for k, v in DEPARTURES.items()},
          "defaultView": "waw"}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)
    run_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"== Scrape {run_iso} ==")
    import shutil

    index = {"generatedAt": run_iso, "config": CONFIG, "destinations": {}}
    summary_line = []

    for dest, cid in DESTINATIONS.items():
        slug = slugify(dest)
        index["destinations"][dest] = {"slug": slug, "views": {}}
        for vkey, v in DEPARTURES.items():
            print(f"  -> {dest} / {v['label']}")
            try:
                scraped, total = enumerate_offers(cid, departure=v["ids"])
            except Exception as e:
                print(f"  !! {dest}/{vkey}: {e}", file=sys.stderr); continue

            store_slug = f"{slug}_{vkey}"
            offers, new_cnt, changed_cnt = update_store(dest, store_slug, scraped, run_iso, v["slug"])

            active = [e for e in offers.values() if e["active"]]
            prices = sorted(e["lastPrice"] for e in active)
            cheapest = prices[0] if prices else None
            drops = sum(1 for e in active if len(e["hist"]) >= 2 and e["hist"][-1][1] < e["hist"][-2][1])
            print(f"     [{vkey}] {len(scraped)} ofert AI samolotem (wylot {DEPARTURE_FROM}..{DEPARTURE_TO}) · "
                  f"nowych {new_cnt} · zmian {changed_cnt} · najtansza {cheapest} zl")

            index["destinations"][dest]["views"][vkey] = {
                "label": v["label"], "storeSlug": store_slug,
                "active": len(active), "tracked": len(offers), "cheapest": cheapest,
                "median": prices[len(prices)//2] if prices else None,
                "newThisRun": new_cnt, "priceChangesThisRun": changed_cnt, "dropsThisRun": drops,
            }
            summary_line.append({"ts": run_iso, "dest": dest, "view": vkey,
                                 "active": len(active), "minPrice": cheapest})
            shutil.copy(os.path.join(DATA_DIR, f"offers_{store_slug}.json"),
                        os.path.join(DOCS_DATA_DIR, f"offers_{store_slug}.json"))

    if not index["destinations"]:
        print("!! Brak danych — nic nie zapisuje.", file=sys.stderr); sys.exit(1)

    # lekka historia zbiorcza (do wykresu najtanszej ceny w czasie, per widok)
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
