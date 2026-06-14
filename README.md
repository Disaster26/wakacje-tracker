# ✈️ Tracker cen wycieczek last-minute — historia per oferta

Monitoruje ceny wycieczek z **wakacje.pl** (agregator: Rainbow, Itaka, TUI, Coral Travel,
Join UP itd.) dla kierunków **Egipt** i **Turcja**. Zbiera dane **2× dziennie** przez
GitHub Actions i pokazuje historię na stronie (GitHub Pages).

Kluczowa cecha: śledzi **cenę KAŻDEJ oferty z osobna** (każdy hotel w zadanym oknie dat/długości)
i buduje jej własną historię cen — nie tylko najtańszą na kierunek.

> Historii **wstecz** nie da się pobrać — tracker buduje historię „w przód", od pierwszego uruchomienia.

## Co pokazuje dashboard
- **Karty kierunków**: najtańsza cena, liczba dostępnych/śledzonych ofert, ile staniało w ostatnim przebiegu
- **Wykres przeglądowy**: najtańsza cena na kierunek w czasie
- **Przeglądarka ofert**: wyszukiwarka hoteli, sortowanie (najtańsze / największy spadek / najmocniej przecenione / ocena), filtry (dostępne / przecenione)
- **Klik w ofertę** → wykres historii ceny tej konkretnej oferty + min/max, status (dostępna/wyprzedana)
- Mini-wykresy (sparkline) i odznaki „staniało / drożej / wyprzedana" przy każdej ofercie

## Konfiguracja
Wszystko na górze pliku [`scraper/scrape.py`](scraper/scrape.py):

| Ustawienie | Domyślnie | Opis |
|---|---|---|
| `DESTINATIONS` | Egipt 37, Turcja 16, Tunezja 65, Grecja 29 | kierunki (`countryId` wakacje.pl) |
| `DEPARTURE_DATE` / `ARRIVAL_DATE` | 2026-06-28 / 2026-07-12 | okno wylotu/powrotu |
| `DURATION_MIN` / `DURATION_MAX` | 14 / 17 | długość w dniach |
| `ADULTS` | 2 | liczba dorosłych |
| `DEPARTURE_CITY` | `None` (dowolne) | kod lotniska, np. `"KTW"`, by zawęzić |

## Uruchomienie lokalne
```bash
pip install -r scraper/requirements.txt
python scraper/scrape.py
```
Otwórz `docs/index.html` w przeglądarce (przez lokalny serwer, np. `python -m http.server -d docs`).

## Wdrożenie na GitHub (za darmo)
1. Stwórz repozytorium i wrzuć ten folder (`git init`, `git add .`, `git commit`, `git push`).
2. **Settings → Pages** → Source: *Deploy from a branch*, branch `main`, folder **`/docs`**.
3. **Settings → Actions → General** → Workflow permissions → *Read and write permissions*.
4. Gotowe. Workflow [`scrape.yml`](.github/workflows/scrape.yml) odpala się o 06:00 i 18:00 UTC
   (≈08:00 i 20:00 czasu polskiego latem). Możesz też odpalić ręcznie:
   zakładka **Actions → Zbieranie cen wycieczek → Run workflow**.

Dashboard będzie pod `https://<twoj-login>.github.io/<repo>/`.

## Jak to działa
wakacje.pl to aplikacja Next.js, która oferty pobiera z wewnętrznego API
(`POST /v2/api/offers` → silnik `storebox-searchengine`). Scraper woła to API
bezpośrednio i dostaje czysty JSON z cenami — bez renderowania przeglądarki,
więc działa szybko i stabilnie w GitHub Actions.

## Pliki danych
- `data/history.jsonl` — źródło prawdy historii (1 linia = 1 kierunek × 1 pomiar)
- `data/latest.json` — pełne najtańsze oferty z ostatniego pomiaru
- `docs/data/*.json` — kopie dla dashboardu (GitHub Pages serwuje `/docs`)

## Zakres i ograniczenia
- Śledzone są **najtańsze ~500 ofert na kierunek** (`MAX_TRACK` w scraperze) — to pokrywa cały realny zakres last-minute. Droższych/luksusowych ofert z dalekiego ogona nie ma sensu śledzić, a pełne ich pobieranie 2× dziennie jest zawodne przez limity serwera.
- wakacje.pl **throttluje serie zapytań** (burst), dlatego scraper ma łagodne tempo (`REQ_DELAY`) i twardy limit zapytań (`MAX_FETCHES`). Na świeżym IP GitHub Actions działa szybko i stabilnie.
- Klucz oferty = `hotelId|data|powrót|wyżywienie|operator` — śledzi tę samą wycieczkę w czasie (ceny mogą się różnić zależnie od lotniska; bierzemy najtańszy wariant danego hotelu).
- Jeśli wakacje.pl zmieni strukturę API, trzeba poprawić `build_body()` w scraperze.
- Projekt do użytku prywatnego (monitoring własnych zakupów).
