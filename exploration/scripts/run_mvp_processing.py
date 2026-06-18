#!/usr/bin/env python3
"""
Procesa los 6 recursos MVP SESCO desde línea de comandos.

Alternativa scriptable a la notebook ``02_modelo_unificado_sesco.ipynb`` para
regenerar ``sesco_produccion_model_clean.csv`` y validaciones por recurso.

Uso (desde la raíz del repo)::

    python exploration/scripts/run_mvp_processing.py

Notes
-----
No genera ``sesco_latest_periods_by_view.csv`` ni otros auxiliares del
dashboard; ejecutar después la notebook ``03_validacion_final_sesco.ipynb``.
"""
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
