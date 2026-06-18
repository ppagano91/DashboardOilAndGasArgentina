#!/usr/bin/env python3
"""
Descarga un recurso CSV del dataset SESCO por URL directa.

Herramienta auxiliar para pruebas puntuales; no ejecuta normalización ni
construcción del modelo común. Los archivos se guardan en
``exploration/data/raw/``.

Uso::

    python exploration/scripts/download_sesco_resource.py <url> [nombre_archivo.csv]

Notes
-----
A diferencia de ``sesco_processing.download_csv``, siempre sobrescribe el
destino si se repite la descarga con el mismo nombre.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

USER_AGENT = "OilGas-Exploration/1.0"
SCRIPT_DIR = Path(__file__).resolve().parent
RAW_DIR = SCRIPT_DIR.parent / "data" / "raw"


def infer_filename(url: str, override: str | None = None) -> str:
    """Deriva un nombre de archivo desde la URL o usa el override."""
    if override:
        return override

    parsed = urlparse(url)
    candidate = Path(unquote(parsed.path)).name
    if candidate:
        return candidate
    return "recurso_sesco.csv"


def download_file(url: str, destination: Path) -> None:
    """Descarga un archivo remoto a destination."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo descargar el recurso: {exc.reason}") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Descarga un recurso del dataset SESCO en exploration/data/raw/"
    )
    parser.add_argument("url", help="URL directa del recurso (CSV u otro formato)")
    parser.add_argument(
        "filename",
        nargs="?",
        help="Nombre opcional del archivo de salida",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    filename = infer_filename(args.url, args.filename)
    destination = RAW_DIR / filename

    print(f"Descargando: {args.url}")
    print(f"Destino:     {destination}")

    try:
        download_file(args.url, destination)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Descarga completada ({destination.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
