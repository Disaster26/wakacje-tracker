#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tracker cen wycieczek last-minute z wakacje.pl (agregator: Rainbow, Itaka, TUI, Coral itd.)

Pobiera ceny dla wybranych kierunków przez wewnetrzne API wakacje.pl
(POST /v2/api/offers -> silnik storebox-searchengine) i zapisuje migawke (snapshot)
do plikow danych. Uruchamiany 2x dziennie przez GitHub Actions buduje historie cen
"w przod" (historii wstecz sie nie da pobrac).

Konfiguracja na gorze pliku — smialo edytuj kierunki/daty/dlugosc.
"""

import json
import os
import sys
import time
import statistics
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
#  KONFIGURACJA  (edytuj wedle potrzeb)
# --------------------------------------------------------------------------

# Kierunki: nazwa wyswietlana -> countryId w systemie wakacje.pl
DESTINATIONS = {
    "Egipt":   37,
    "Turcja":  16,
    "Tunezja": 65,
    "Grecja":  29,
}

DEPARTURE_DATE = "2026-06-28"   # najwczesniejszy wylot (minDate)
ARRIVAL_DATE   = "2026-07-12"   # najpozniejszy powrot/wylot (maxDate)
DURATION_MIN   = 14             # min liczba dni
DURATION_MAX   = 17             # max liczba dni
ADULTS         = 2              # liczba doroslych
DEPARTURE_CITY = None           # None = dowolne lotnisko w PL (kod IATA np. "KTW" by zawezic)

# Ranking API jest niedeterministyczny (qsVersion=cx_auction) — przy probkowaniu stron
# najtansza cena skakalaby. Zamiast tego uzywamy FILTRA CENY (maxPrice) i wyszukiwania
# binarnego, by deterministycznie zlapac waskie pasmo najtanszych ofert.
BAND_CAP       = 60            # max ofert w pasmie (limit jednego zapytania -> wszystkie zwracane)
BAND_ITERS     = 8            # iteracje wyszukiwania binarnego po cenie
PRICE_HI       = 12000        # gorna granica ceny do wyszukiwania (zl za wszystkich)
TOP_N_OFFERS   = 15            # ile najtanszych ofert zapisac do tabeli

# --------------------------------------------------------------------------

API_URL = "https://www.wakacje.pl/v2/api/offers"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Accept-Language": "pl-PL,pl;q=0.9",
    "Origin": "https://www.wakacje.pl",
    "Referer": "https://www.wakacje.pl/wczasy/",
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DATA_DIR = os.path.join(ROOT, "docs", "data")


def build_body(country_id, limit, max_price=None):
    """Sklada tablice wywolan dla API wakacje.pl (format JSON-RPC)."""
    query = {
        "campTypes": [], "qsVersion": 0, "qsVersionLast": 0,
        "tab": False, "candy": False, "pok": None, "flush": False,
        "obj_xCode": None, "tourOpAndCode": None, "obj_code": None, "obj_type": None,
        "catalog": None, "roomType": None, "test": None, "year": None, "month": None,
        "rangeDate": None, "withoutLast": 0, "category": False, "not-attribute": False,
        "pageNumber": 1,
        "departureDate": DEPARTURE_DATE,
        "arrivalDate": ARRIVAL_DATE,
        "departure": DEPARTURE_CITY,
        "type": [],
        "duration": {"min": DURATION_MIN, "max": DURATION_MAX},
        "minPrice": None, "maxPrice": max_price,
        "service": [], "firstminute": None, "attribute": [], "promotion": [],
        "tourId": None, "search": None,
        "minCategory": None, "maxCategory": 50,
        "sort": None, "order": None,   # serwerowe sortowanie po cenie rzuca 400 -> sortujemy lokalnie
        "rank": None,
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


def query(country_id, max_price=None, limit=10, retries=3):
    """Jedno zapytanie do API. Zwraca (count, [offers]) lub (None, [])."""
    for attempt in range(retries):
        try:
            r = requests.post(API_URL, headers=HEADERS,
                              json=build_body(country_id, limit, max_price), timeout=60)
            data = json.loads(r.content.decode("utf-8"))  # wymuszamy UTF-8 (polskie znaki)
            if not data.get("success"):
                msg = data.get("error", {}).get("message", "?")
                print(f"    ! API blad (maxPrice={max_price}): {msg[-70:]}", file=sys.stderr)
                time.sleep(1.5 * (attempt + 1))
                continue
            d = data["data"]
            return d.get("count"), d.get("offers", [])
        except Exception as e:
            print(f"    ! wyjatek (proba {attempt+1}): {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return None, []


def slim_offer(o):
    """Wyciaga tylko interesujace pola z surowej oferty."""
    place = o.get("place", {}) or {}
    country = (place.get("country") or {}).get("slug", "")
    return {
        "name": o.get("name"),
        "place": o.get("placeName"),
        "price": o.get("price"),
        "priceOld": o.get("priceOld") or None,
        "duration": o.get("duration"),
        "departureDate": o.get("departureDate"),
        "returnDate": o.get("returnDate"),
        "departureFrom": o.get("departurePlace"),
        "operator": o.get("tourOperatorName"),
        "service": o.get("serviceDesc"),
        "category": o.get("category"),
        "rating": o.get("ratingValue") or None,
        "offerId": o.get("offerId"),
        "url": f"https://www.wakacje.pl/wczasy/{country}/" if country else "https://www.wakacje.pl/",
    }


def find_price_band(country_id):
    """Wyszukiwanie binarne: najwyzszy maxPrice, przy ktorym count <= BAND_CAP.
    Dzieki temu jednym zapytaniem dostajemy KOMPLET najtanszych ofert (deterministycznie)."""
    lo, hi, best = 0, PRICE_HI, PRICE_HI
    for _ in range(BAND_ITERS):
        mid = (lo + hi) // 2
        count, _ = query(country_id, max_price=mid, limit=1)
        time.sleep(0.8)
        if count is None:
            break
        if count <= BAND_CAP:
            best = mid          # mozemy poszerzyc pasmo (wiecej tanich ofert)
            lo = mid + 1
        else:
            hi = mid - 1        # za duzo ofert -> obnizamy prog
    return best


def scrape_destination(name, country_id):
    """Pobiera najtansze oferty dla jednego kierunku (przez filtr ceny) i buduje migawke."""
    print(f"  -> {name} (countryId={country_id})")

    # 1) calkowita liczba ofert (wskaznik dostepnosci / wyprzedania)
    total_count, _ = query(country_id, max_price=None, limit=1)
    time.sleep(0.8)

    # 2) deterministyczne pasmo najtanszych ofert
    band = find_price_band(country_id)
    _, band_offers = query(country_id, max_price=band, limit=BAND_CAP)

    rows = [slim_offer(o) for o in band_offers if o.get("price")]
    rows.sort(key=lambda x: x["price"])
    prices = [r["price"] for r in rows]

    summary = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dest": name,
        "count": total_count if total_count is not None else len(rows),
        "band": band,                 # gorna granica zlapanego pasma cen
        "bandCount": len(rows),       # ile ofert w pasmie
        "minPrice": prices[0] if prices else None,
        "avgTop5": round(statistics.mean(prices[:5])) if prices else None,
        "median": round(statistics.median(prices)) if prices else None,
    }
    top = rows[:TOP_N_OFFERS]
    print(f"     count={summary['count']} min={summary['minPrice']} zl "
          f"(pasmo <= {band} zl, {len(rows)} ofert)")
    return summary, top


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(DOCS_DATA_DIR, exist_ok=True)

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"== Scrape {run_ts} ==")

    summaries = []
    latest = {"generatedAt": run_ts, "config": {
        "departureDate": DEPARTURE_DATE, "arrivalDate": ARRIVAL_DATE,
        "durationMin": DURATION_MIN, "durationMax": DURATION_MAX, "adults": ADULTS,
        "departureCity": DEPARTURE_CITY or "dowolne",
    }, "destinations": {}}

    for name, cid in DESTINATIONS.items():
        try:
            summary, top = scrape_destination(name, cid)
        except Exception as e:
            print(f"  !! {name}: {e}", file=sys.stderr)
            continue
        summaries.append(summary)
        latest["destinations"][name] = {"summary": summary, "offers": top}

    if not summaries:
        print("!! Brak danych — nic nie zapisuje.", file=sys.stderr)
        sys.exit(1)

    # 1) dopisz lekka linie czasowa do history.jsonl (zrodlo prawdy historii)
    hist_path = os.path.join(DATA_DIR, "history.jsonl")
    with open(hist_path, "a", encoding="utf-8") as f:
        for s in summaries:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 2) najnowsze pelne oferty (nadpisywane)
    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)

    # 3) wygeneruj pliki dla dashboardu (GitHub Pages serwuje /docs)
    history = []
    with open(hist_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                history.append(json.loads(line))
    with open(os.path.join(DOCS_DATA_DIR, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)
    with open(os.path.join(DOCS_DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False)

    print(f"== Zapisano {len(summaries)} kierunkow, historia: {len(history)} punktow ==")


if __name__ == "__main__":
    main()
