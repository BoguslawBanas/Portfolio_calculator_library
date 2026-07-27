# Portfolio Library

Biblioteka Python wspomagająca zarządzanie portfelem inwestycyjnym. Umożliwia analizę i przetwarzanie danych dotyczących obligacji skarbowych, akcji oraz funduszy ETF z wykorzystaniem bibliotek `pandas`, `matplotlib` oraz `yfinance`.

## Funkcjonalności

Biblioteka składa się z czterech głównych modułów:

### 📈 stock_calculator_library

Moduł odpowiedzialny za obsługę akcji oraz funduszy ETF.

Najważniejsze funkcje:

- pobieranie historycznych notowań za pomocą biblioteki `yfinance`,
- pobieranie informacji o instrumentach finansowych,
- obliczanie stóp zwrotu,
- przygotowanie danych do dalszej analizy portfela,
- agregacja danych dla wielu instrumentów.

Biblioteka wykorzystuje:

- pandas
- numpy
- yfinance

---

### 🏦 bonds_calculator_library

Moduł przeznaczony do obsługi polskich obligacji skarbowych.

Umożliwia między innymi:

- obliczanie wartości obligacji w czasie,
- wyznaczanie naliczonych odsetek,
- uwzględnianie kapitalizacji odsetek,
- analizę harmonogramów wykupu,
- śledzenie wartości inwestycji.

---

### 📊 portfolio_calculator_library

Moduł zawierający zestaw funkcji operujących na obiektach `pandas.DataFrame`.

Pozwala między innymi na:

- transformację danych portfela,
- łączenie danych z różnych źródeł,
- uzupełnianie brakujących danych czasowych,
- obliczanie wartości portfela w czasie,
- agregację danych według aktywów,
- przygotowanie danych do wizualizacji.

Moduł nie odpowiada za pobieranie danych — jego zadaniem jest ich przetwarzanie.

---

### plot_library

Moduł zawierający zestaw funkcji, która służy do generowania wykresów.

## Przykładowa struktura projektu

```
portfolio_library/
│
├── bonds_library.py
├── stock_library.py
├── portfolio_library.py
└── examples/
```

## Instalacja

```bash
pip install -r requirements.txt
```

lub

```bash
pip install pandas numpy yfinance
```

## Przykład użycia

```python
from stock_library import download_prices
from portfolio_library import calculate_portfolio_value

prices = download_prices(
    tickers=["VWCE.DE", "CSPX.L"],
    start="2020-01-01",
    end="2025-01-01"
)

portfolio = calculate_portfolio_value(prices)
```

## Wymagania

- Python 3.10+
- pandas
- numpy
- yfinance

## Zastosowania

Biblioteka została przygotowana z myślą o:

- prowadzeniu własnego portfela inwestycyjnego,
- analizie historycznych wyników,
- monitorowaniu wartości aktywów,
- analizie obligacji skarbowych,
- budowie własnych narzędzi do raportowania inwestycji.

## Rozwój

Projekt rozwijany jest modułowo, dzięki czemu kolejne klasy i funkcje mogą być dodawane niezależnie do poszczególnych modułów.

Planowane rozszerzenia obejmują m.in.:

- obsługę dywidend,
- analizę podatkową,
- eksport raportów do Excel/PDF,
- wsparcie dla kolejnych źródeł danych,
- rozbudowane statystyki portfela.

## Licencja

Licencja MIT.