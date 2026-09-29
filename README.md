# Indicadores SII

Los valores y fechas del [SII](https://www.sii.cl/valores_y_fechas/index_valores_y_fechas.html)
como JSON estático en GitHub Pages. Una GitHub Action corre `scraper/scrape.py`
todos los días (12:15 UTC), guarda los cambios en `docs/api/` y publica.

| Endpoint | Contenido |
|---|---|
| `api/hoy.json` | UF y dólar del día, UTM/UTA del mes, último IPC |
| `api/uf.json`, `api/dolar.json` | últimos 31 días (mismo formato que mindicador.cl) |
| `api/uf/<año>.json` | UF diaria desde 2013, incluye días futuros ya publicados |
| `api/dolar/<año>.json` | dólar observado, solo días hábiles |
| `api/utm/<año>.json` | UTM, UTA e IPC por mes |
| `api/tablas/<slug>.json` | resto de las páginas del SII, tabla por tabla |
| `api/index.json` | catálogo y errores de la última lectura |

UF, dólar y UTM vienen interpretados (fechas ISO y números). Las demás páginas
(impuesto de segunda categoría, corrección monetaria, Mepco, Odepa, tabaco,
vida útil, etc.) se publican tal como están: cada tabla es una lista de filas
de texto, con `colspan`/`rowspan` expandidos. El calendario de IVA del SII es
una aplicación y no se incluye.

## Correr a mano

```sh
uv run --with beautifulsoup4 python scraper/scrape.py         # año en curso + años faltantes
uv run --with beautifulsoup4 python scraper/scrape.py --todo  # todo desde 2013
```

Si el SII cambia el formato de alguna página, el job queda en rojo (llega
correo) y `api/index.json` lista qué falló; lo que sí se pudo leer se publica
igual.
