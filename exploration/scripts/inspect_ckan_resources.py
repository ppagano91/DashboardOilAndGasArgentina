#!/usr/bin/env python3
"""
Consulta la API CKAN de datos.gob.ar y lista los recursos del dataset SESCO.

Genera un inventario local en ``exploration/data/processed/ckan_resources.csv``
para auditar fuentes sin depender de URLs fijas en código.

Uso (desde la raíz del repo)::

    python exploration/scripts/inspect_ckan_resources.py
"""
from __future__ import annotations

import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PACKAGE_ID = "energia-produccion-petroleo-gas-sesco"
CKAN_URL = f"https://datos.gob.ar/api/3/action/package_show?id={PACKAGE_ID}"
USER_AGENT = "OilGas-Exploration/1.0"

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_CSV = SCRIPT_DIR.parent / "data" / "processed" / "ckan_resources.csv"

RESOURCE_FIELDS = [
    "name",
    "id",
    "format",
    "url",
    "created",
    "last_modified",
    "size",
]


def fetch_package() -> dict:
    """Obtiene el paquete CKAN y valida la respuesta."""
    request = urllib.request.Request(CKAN_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(f"Error al consultar CKAN: {exc}") from exc

    if not payload.get("success"):
        raise SystemExit(f"CKAN respondió con error: {payload.get('error')}")

    return payload["result"]


def resources_to_rows(resources: list[dict]) -> list[dict]:
    """Extrae metadata relevante de cada recurso."""
    rows = []
    for resource in resources:
        rows.append({field: resource.get(field) for field in RESOURCE_FIELDS})
    return rows


def print_resources(rows: list[dict]) -> None:
    """Imprime recursos ordenados por nombre."""
    sorted_rows = sorted(rows, key=lambda row: (row.get("name") or "").lower())
    print(f"\nRecursos disponibles ({len(sorted_rows)}):\n")
    for index, row in enumerate(sorted_rows, start=1):
        size = row.get("size")
        size_label = f"{size} bytes" if size is not None else "N/D"
        print(f"{index:02d}. {row.get('name')}")
        print(f"    id:            {row.get('id')}")
        print(f"    format:        {row.get('format')}")
        print(f"    created:       {row.get('created')}")
        print(f"    last_modified: {row.get('last_modified')}")
        print(f"    size:          {size_label}")
        print(f"    url:           {row.get('url')}")
        print()


def save_csv(rows: list[dict], output_path: Path) -> None:
    """Guarda metadata de recursos en CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_rows = sorted(rows, key=lambda row: (row.get("name") or "").lower())

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESOURCE_FIELDS)
        writer.writeheader()
        writer.writerows(sorted_rows)

    print(f"Metadata guardada en: {output_path}")


def main() -> int:
    package = fetch_package()
    resources = package.get("resources", [])
    rows = resources_to_rows(resources)

    print(f"Dataset: {package.get('title')}")
    print(f"Recursos encontrados: {len(rows)}")

    print_resources(rows)
    save_csv(rows, OUTPUT_CSV)
    return 0


if __name__ == "__main__":
    sys.exit(main())
