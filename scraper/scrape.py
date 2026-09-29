#!/usr/bin/env python3
"""Lee los valores y fechas publicados por el SII y los deja como JSON estático.

Salida en docs/api/ (servida por GitHub Pages):

  hoy.json                 UF y dólar del día, UTM/UTA del mes, último IPC
  uf.json, dolar.json      últimos 31 días publicados hasta hoy
  uf/<año>.json            serie diaria del año (incluye días futuros ya publicados)
  dolar/<año>.json         dólar observado, solo días hábiles
  utm/<año>.json           UTM, UTA e IPC por mes
  tablas/<slug>.json       resto de las páginas del SII, tabla por tabla
  index.json               catálogo de todo lo anterior

Uso:
  scrape.py            actualiza el año en curso y completa los años que falten
  scrape.py --todo     vuelve a descargar todos los años desde 2013
"""

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

BASE = "https://www.sii.cl/valores_y_fechas/"
PRIMER_ANIO = 2013  # el SII no publica estas series antes de 2013
TZ = ZoneInfo("America/Santiago")
OUT = Path(__file__).resolve().parent.parent / "docs" / "api"
USER_AGENT = "indicadores-klv/1.0 (+https://github.com/KLV-SPA/indicadores)"

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

SERIES = {
    "uf": {
        "nombre": "Unidad de fomento (UF)",
        "unidad_medida": "Pesos",
        "ruta": "uf/uf{y}.htm",
    },
    "dolar": {
        "nombre": "Dólar observado",
        "unidad_medida": "Pesos",
        "ruta": "dolar/dolar{y}.htm",
    },
    "utm": {
        "nombre": "UTM, UTA e IPC",
        "unidad_medida": "Pesos (UTM, UTA), puntos y porcentaje (IPC)",
        "ruta": "utm/utm{y}.htm",
    },
}

# Páginas de index_valores_y_fechas.html sin formato de serie: se publican
# tabla por tabla. {y} es el año en curso (con respaldo al anterior).
PAGINAS = [
    ("impuesto-segunda-categoria", "Impuesto Único de Segunda Categoría (Art. 43 N° 1 LIR)", "impuesto_2da_categoria/impuesto{y}.htm"),
    ("impuesto-segunda-categoria-art52", "Impuesto Único de Segunda Categoría (Art. 52 bis letra a) LIR)", "impuesto_2da_categoria/impuesto{y}_art52.htm"),
    ("reajuste-declaracion-renta", "Reajuste declaración renta", "reajuste_declaracion_renta.html"),
    ("correccion-monetaria", "Corrección monetaria mensual", "correccion_monetaria/correccion{y}.htm"),
    ("renta-personas-naturales", "Datos informativos Operación Renta: personas naturales", "renta/{y}/personas_naturales.html"),
    ("renta-grandes-contribuyentes", "Datos informativos Operación Renta: empresas", "renta/{y}/grandes_contribuyentes.html"),
    ("renta-pequenos-contribuyentes", "Datos informativos Operación Renta: pequeños contribuyentes", "renta/{y}/pequenos_contribuyentes.html"),
    ("iva-reajuste-pequenos-productores", "Porcentaje de reajuste de devoluciones de IVA a pequeños productores agrícolas", "iva/porcentaje_de_reajuste.html"),
    ("petroleo-fepp", "Fondo de Estabilización de Precios del Petróleo", "petroleo/petro{y}.htm"),
    ("mepco", "Impuesto al petróleo (componentes base y variable)", "mepco/mepco{y}.htm"),
    ("limite-ingreso-iepd", "Tabla de límite de ingreso para recuperación IEPD", "tabla_limite_ingreso.html"),
    ("vida-util", "Tabla de vida útil de los bienes físicos del activo inmovilizado", "tabla_vida_util_activo_inmovilizado.html"),
    ("vida-util-existente", "Tabla de vida útil (bienes existentes)", "tabla_vida_util_activo_inmovilizado_existente.html"),
    ("precios-odepa", "Precios Odepa", "odepa/precios_odepa{y}.htm"),
    ("precios-cigarrillos", "Base de precios de venta final de cigarrillos", "cigarrillos/precios_cigarrillos.htm"),
    ("precios-otros-tabaco", "Base de precios de venta final de otros productos de tabaco", "otros_valores/otros_productos_tabaco.html"),
    ("precio-libra-cobre", "Precio promedio libra de cobre (Art. 7 Ley 21.591)", "otros_valores/precio_libra_cobre.html"),
    ("tasa-interes-penal-diario", "Tasa de interés penal diario", "otros_valores/tasa_interes_penal_diario.html"),
]


class NoPublicado(Exception):
    """La página no existe (404): p. ej. el año aún no se publica."""


def descargar(ruta):
    url = BASE + ruta
    return descargar_url(url), url


def descargar_url(url):
    for intento in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=40) as r:
                raw = r.read()
            time.sleep(0.4)  # sin apuro: son pocas páginas y el SII es un servicio público
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("latin-1")
        except HTTPError as e:
            if e.code == 404:
                raise NoPublicado(url) from e
            error = e
        except URLError as e:
            error = e
        time.sleep(3 * (intento + 1))
    raise RuntimeError(f"{url}: {error}")


def texto(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def numero(s, decimal):
    """'41.049,01' -> 41049.01 (decimal=','), '933.4' -> 933.4 (decimal='.')."""
    s = s.strip().replace("$", "").replace("%", "").replace(" ", "")
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")  # siempre formato chileno
    elif decimal == ",":
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")  # el dólar de 2017-2018 viene con coma decimal
    try:
        return float(s)
    except ValueError:
        return None


# ---- Series diarias (UF, dólar): una tabla por mes dentro de div.meses#mes_<mes>.

def serie_diaria(html, anio, decimal):
    soup = BeautifulSoup(html, "html.parser")
    valores = {}
    for div in soup.select("div.meses"):
        mes = (div.get("id") or "").replace("mes_", "")
        if mes not in MESES:
            continue
        m = MESES.index(mes) + 1
        for tr in div.select("table tr"):
            celdas = tr.find_all(["th", "td"])
            # Filas de pares <th>día</th><td>valor</td>
            for th, td in zip(celdas[0::2], celdas[1::2]):
                if th.name != "th" or td.name != "td":
                    continue
                dia = texto(th)
                valor = numero(texto(td), decimal)
                if not dia.isdigit() or valor is None:
                    continue
                try:
                    fecha = date(anio, m, int(dia))
                except ValueError:
                    continue
                valores[fecha.isoformat()] = valor
    return [{"fecha": f, "valor": v} for f, v in sorted(valores.items(), reverse=True)]


# ---- UTM / UTA / IPC: una tabla con una fila por mes.

def serie_utm(html):
    soup = BeautifulSoup(html, "html.parser")
    filas = []
    for tr in soup.select("table tr"):
        celdas = [texto(c) for c in tr.find_all(["th", "td"])]
        if not celdas or celdas[0].lower() not in MESES:
            continue
        mes = MESES.index(celdas[0].lower()) + 1
        v = [numero(c, ",") for c in celdas[1:]] + [None] * 6
        if v[0] is None and v[1] is None:
            continue  # mes aún no publicado
        filas.append({
            "mes": mes,
            "utm": v[0],
            "uta": v[1],
            "ipc": v[2],
            "ipc_variacion_mensual": v[3],
            "ipc_variacion_acumulada": v[4],
            "ipc_variacion_12_meses": v[5],
        })
    return sorted(filas, key=lambda f: f["mes"], reverse=True)


# ---- Páginas genéricas: todas las tablas, con colspan/rowspan expandidos.

def tabla_a_filas(table):
    filas, pendientes = [], {}  # pendientes[col] = [texto, filas restantes]
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        fila, col = [], 0
        celdas = tr.find_all(["th", "td"], recursive=False)
        i = 0
        while i < len(celdas) or col in pendientes:
            if col in pendientes:
                t, resto = pendientes[col]
                fila.append(t)
                if resto <= 1:
                    del pendientes[col]
                else:
                    pendientes[col][1] = resto - 1
                col += 1
                continue
            c = celdas[i]
            i += 1
            t = texto(c)
            colspan = int(c.get("colspan", 1) or 1) if str(c.get("colspan", 1)).isdigit() else 1
            rowspan = int(c.get("rowspan", 1) or 1) if str(c.get("rowspan", 1)).isdigit() else 1
            for _ in range(colspan):
                fila.append(t)
                if rowspan > 1:
                    pendientes[col] = [t, rowspan - 1]
                col += 1
        if any(x for x in fila):
            filas.append(fila)
    return filas


def titulo_de(table):
    cap = table.find("caption")
    if cap and texto(cap):
        return texto(cap)
    prev = table.find_previous(["h1", "h2", "h3", "h4", "h5", "caption"])
    return texto(prev) if prev else ""


def pagina_generica(html, url):
    soup = BeautifulSoup(html, "html.parser")
    tablas = []
    for table in soup.find_all("table"):
        if table.find_parent("table"):
            continue
        filas = tabla_a_filas(table)
        if filas:
            tablas.append({"titulo": titulo_de(table), "filas": filas})

    # Algunas páginas (tabaco, cigarrillos) arman la tabla en el navegador a
    # partir de un CSV separado por ";": se lee el CSV directamente.
    for m in re.finditer(r"""(?:cargarCSV|fetch)\(\s*["']([^"']+\.csv)["']\s*(?:,\s*["']([^"']+)["'])?""", html):
        csv_url = urljoin(url, m.group(1))
        filas = [f.split(";") for f in descargar_url(csv_url).lstrip("\ufeff").splitlines() if f.strip()]
        destino = soup.find(id=m.group(2)) if m.group(2) else None
        titulo = texto(destino.find_previous(["h2", "h3", "h4"])) if destino else ""
        # cigarrillos: el encabezado está en la tabla HTML y el CSV trae solo datos.
        solo_encabezado = [t for t in tablas if len(t["filas"]) == 1 and len(t["filas"][0]) == len(filas[0])]
        if not destino and solo_encabezado:
            solo_encabezado[0]["filas"] += filas
            continue
        tablas.append({"titulo": titulo, "fuente": csv_url, "filas": filas})

    documentos = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(r"\.(pdf|xlsx?|csv|zip)$", href, re.I):
            documentos.append({"nombre": texto(a), "url": urljoin(url, href)})

    # Tablas publicadas como imagen (vida útil de bienes existentes), a veces
    # repartidas en varias páginas: se listan las imágenes.
    if not tablas:
        paginas = [(url, soup)]
        for a in soup.select("#indice_pags a[href]"):
            otra = urljoin(url, a["href"])
            if otra != url:
                paginas.append((otra, BeautifulSoup(descargar_url(otra), "html.parser")))
        for n, (purl, psoup) in enumerate(paginas, 1):
            for img in psoup.find_all("img", src=True):
                if not img["src"].startswith(("/", "http")):
                    documentos.append({"nombre": f"Imagen (página {n})", "url": urljoin(purl, img["src"])})
    return tablas, documentos


# ---- Escritura.

def guardar(ruta, data):
    """Escribe solo si cambió algo más que la fecha de `actualizado`, para
    que el commit diario no reescriba años que el SII ya no toca."""
    ruta = OUT / ruta
    if ruta.exists():
        previo = json.loads(ruta.read_text(encoding="utf-8"))
        if {**previo, "actualizado": None} == {**data, "actualizado": None}:
            return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def leer(ruta):
    p = OUT / ruta
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="volver a descargar todos los años")
    args = ap.parse_args()

    ahora = datetime.now(TZ)
    hoy = ahora.date()
    actualizado = ahora.isoformat(timespec="seconds")
    errores = []

    # Años a refrescar: el actual; en enero también el anterior (correcciones
    # tardías) y en diciembre el siguiente (la UF de enero sale el 10 de dic).
    frescos = {hoy.year}
    if hoy.month == 1:
        frescos.add(hoy.year - 1)
    if hoy.month == 12:
        frescos.add(hoy.year + 1)

    anios_por_serie = {}
    for codigo, meta in SERIES.items():
        anios = []
        for anio in range(PRIMER_ANIO, hoy.year + 2):
            ruta = f"{codigo}/{anio}.json"
            if not (args.todo or anio in frescos or leer(ruta) is None):
                anios.append(anio)
                continue
            try:
                html, url = descargar(meta["ruta"].format(y=anio))
            except NoPublicado:
                continue
            except Exception as e:
                errores.append(f"{codigo} {anio}: {e}")
                if leer(ruta) is not None:
                    anios.append(anio)
                continue
            serie = serie_utm(html) if codigo == "utm" else serie_diaria(html, anio, "," if codigo == "uf" else ".")
            if not serie:
                if anio <= hoy.year:
                    errores.append(f"{codigo} {anio}: la página no trajo datos (¿cambió el formato?)")
                continue
            guardar(ruta, {
                "codigo": codigo,
                "nombre": meta["nombre"],
                "unidad_medida": meta["unidad_medida"],
                "anio": anio,
                "fuente": url,
                "actualizado": actualizado,
                "serie": serie,
            })
            anios.append(anio)
        anios_por_serie[codigo] = anios

    # Últimos 31 días hasta hoy (formato compatible con mindicador.cl/api/<codigo>).
    ultimos = {}
    for codigo in ("uf", "dolar"):
        serie = []
        for anio in (hoy.year, hoy.year - 1):
            d = leer(f"{codigo}/{anio}.json")
            if d:
                serie += d["serie"]
        serie = sorted((p for p in serie if p["fecha"] <= hoy.isoformat()), key=lambda p: p["fecha"], reverse=True)[:31]
        ultimos[codigo] = serie
        guardar(f"{codigo}.json", {
            "codigo": codigo,
            "nombre": SERIES[codigo]["nombre"],
            "unidad_medida": SERIES[codigo]["unidad_medida"],
            "actualizado": actualizado,
            "serie": serie,
        })

    utm_anio = (leer(f"utm/{hoy.year}.json") or {}).get("serie", [])
    utm_mes = next((f for f in utm_anio if f["mes"] == hoy.month), None)
    ipc = next((f for f in utm_anio if f.get("ipc") is not None), None)

    uf_hoy = next((p for p in ultimos["uf"] if p["fecha"] == hoy.isoformat()), None)
    if uf_hoy is None:
        errores.append(f"uf: no hay valor para hoy ({hoy.isoformat()})")

    guardar("hoy.json", {
        "fecha": hoy.isoformat(),
        "actualizado": actualizado,
        "uf": uf_hoy,
        "dolar": ultimos["dolar"][0] if ultimos["dolar"] else None,
        "utm": {"mes": f"{hoy.year}-{hoy.month:02d}", "valor": utm_mes["utm"]} if utm_mes else None,
        "uta": {"mes": f"{hoy.year}-{hoy.month:02d}", "valor": utm_mes["uta"]} if utm_mes else None,
        "ipc": {
            "mes": f"{hoy.year}-{ipc['mes']:02d}",
            "valor": ipc["ipc"],
            "variacion_mensual": ipc["ipc_variacion_mensual"],
            "variacion_12_meses": ipc["ipc_variacion_12_meses"],
        } if ipc else None,
        "fuente": BASE + "index_valores_y_fechas.html",
    })

    tablas_idx = []
    for slug, nombre, ruta in PAGINAS:
        candidatos = [ruta.format(y=hoy.year)]
        if "{y}" in ruta:
            candidatos.append(ruta.format(y=hoy.year - 1))
        for r in candidatos:
            try:
                html, url = descargar(r)
            except NoPublicado:
                continue
            except Exception as e:
                errores.append(f"{slug}: {e}")
                break
            tablas, documentos = pagina_generica(html, url)
            if not tablas and not documentos:
                errores.append(f"{slug}: la página no trajo tablas ni documentos")
            guardar(f"tablas/{slug}.json", {
                "slug": slug,
                "nombre": nombre,
                "fuente": url,
                "actualizado": actualizado,
                "tablas": tablas,
                "documentos": documentos,
            })
            tablas_idx.append({"slug": slug, "nombre": nombre, "url": f"tablas/{slug}.json", "fuente": url, "tablas": len(tablas)})
            break
        else:
            errores.append(f"{slug}: no publicada ({candidatos})")

    guardar("index.json", {
        "nombre": "Indicadores KLV",
        "descripcion": "Valores y fechas del SII como JSON, actualizados a diario.",
        "fuente": BASE + "index_valores_y_fechas.html",
        "actualizado": actualizado,
        "endpoints": {
            "hoy": "hoy.json",
            "uf": "uf.json",
            "dolar": "dolar.json",
        },
        "series": {
            codigo: {
                "nombre": SERIES[codigo]["nombre"],
                "anios": {str(a): f"{codigo}/{a}.json" for a in sorted(anios)},
            }
            for codigo, anios in anios_por_serie.items()
        },
        "tablas": tablas_idx,
        "errores": errores,
    })

    for e in errores:
        print("ERROR", e, file=sys.stderr)
    print(f"listo: {sum(len(a) for a in anios_por_serie.values())} años de series, {len(tablas_idx)} páginas, {len(errores)} errores")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
