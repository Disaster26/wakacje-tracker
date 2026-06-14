# ✈️ Tracker cen wycieczek last-minute

Monitoruje ceny wycieczek z **wakacje.pl** (agregator: Rainbow, Itaka, TUI, Coral Travel,
Join UP itd.) dla kierunków **Egipt, Turcja, Tunezja, Grecja**. Zbiera dane **2× dziennie**
przez GitHub Actions i pokazuje historię na stronie (GitHub Pages) — żeby widzieć, czy ceny
spadają i czy oferty się nie wyprzedają.

> Historii **wstecz** nie da się pobrać — tracker buduje historię „w przód", od pierwszego uruchomienia.

## Co pokazuje dashboard
- **Najtańsza cena w czasie** (za 2 osoby) — osobna linia na każdy kierunek
- **Liczba dostępnych ofert w czasie** — gdy spada, kierunek zaczyna się wyprzedawać
- **Tabela najtańszych ofert teraz** — hotel, termin, długość, wyżywienie, lotnisko, operator, cena (i cena przed obniżką)
- Na kartach: zmiana ceny od pierwszego pomiaru (% i zł)

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

## Uwagi
- API ma okno wyników (~1200 ofert na zapytanie); najtańsze oferty mieszczą się w tym oknie.
- Jeśli wakacje.pl zmieni strukturę API, trzeba będzie poprawić `build_body()` w scraperze.
- Projekt do użytku prywatnego (monitoring własnych zakupów), z umiarem co do liczby zapytań.
