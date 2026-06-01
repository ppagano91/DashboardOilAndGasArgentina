#!/usr/bin/env python3
"""Procesa los 6 recursos MVP SESCO desde línea de comandos."""

from __future__ import annotations

import sys

from sesco_processing import export_unified, process_all_resources

SESCO_RESOURCES_MVP = {
    "petroleo_provincia": {
        "producto": "petroleo",
        "agrupador_tipo": "provincia",
        "nombre_recurso": "Producción de petróleo promedio diaria por provincia",
    },
    "gas_provincia": {
        "producto": "gas",
        "agrupador_tipo": "provincia",
        "nombre_recurso": "Producción de gas promedio diaria por provincia",
    },
    "petroleo_cuenca": {
        "producto": "petroleo",
        "agrupador_tipo": "cuenca",
        "nombre_recurso": "Producción de petróleo promedio diaria por cuenca",
    },
    "gas_cuenca": {
        "producto": "gas",
        "agrupador_tipo": "cuenca",
        "nombre_recurso": "Producción de gas promedio diaria por cuenca",
    },
    "petroleo_empresa": {
        "producto": "petroleo",
        "agrupador_tipo": "empresa",
        "nombre_recurso": "Producción de petróleo promedio diaria por empresa",
    },
    "gas_empresa": {
        "producto": "gas",
        "agrupador_tipo": "empresa",
        "nombre_recurso": "Producción de gas promedio diaria por empresa",
    },
}


def main() -> int:
    _, df_unified, df_valid = process_all_resources(SESCO_RESOURCES_MVP, verbose=True)
    if len(df_unified) == 0:
        print("No se generó dataset unificado.", file=sys.stderr)
        return 1
    unified_path, valid_path = export_unified(df_unified, df_valid)
    print(f"\nUnificado: {unified_path} ({len(df_unified):,} filas)")
    print(f"Validaciones: {valid_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
