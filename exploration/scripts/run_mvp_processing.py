#!/usr/bin/env python3
"""
Procesa los 6 recursos MVP SESCO desde línea de comandos.

Alternativa scriptable a la notebook ``02_modelo_unificado_sesco.ipynb`` para
regenerar ``sesco_produccion_model_clean.csv`` y validaciones por recurso.

Uso (desde la raíz del repo)::

    python exploration/scripts/run_mvp_processing.py
    python exploration/scripts/run_mvp_processing.py --update-raw
    python exploration/scripts/run_mvp_processing.py --force-download

Notes
-----
También exporta auxiliares del dashboard (``sesco_latest_periods_by_view.csv``,
etc.) con la misma lógica que la notebook ``03_validacion_final_sesco.ipynb``.
"""
from __future__ import annotations

import argparse
import sys

from sesco_processing import (
    SESCO_RESOURCES_MVP,
    ensure_raw_snapshot,
    export_dashboard_auxiliaries,
    export_unified,
    process_all_resources,
    resolve_raw_dir,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Procesa los 6 recursos MVP SESCO y exporta el dataset unificado.",
    )
    parser.add_argument(
        "--update-raw",
        action="store_true",
        help="Consulta CKAN y crea snapshot raw solo si hay cambios (o si no hay snapshot previo).",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Fuerza descarga de los 6 CSV a un snapshot del día (sufijo horario si ya existe).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    raw_dir = resolve_raw_dir()

    if args.force_download:
        snapshot_dir = ensure_raw_snapshot(force_download=True)
        raw_dir = snapshot_dir
        print(f"Snapshot raw (forzado): {snapshot_dir}")
    elif args.update_raw:
        snapshot_dir = ensure_raw_snapshot(force_download=False)
        raw_dir = snapshot_dir
        print(f"Snapshot raw: {snapshot_dir}")

    _, df_unified, df_valid = process_all_resources(
        SESCO_RESOURCES_MVP,
        raw_dir=raw_dir,
        verbose=True,
    )
    if len(df_unified) == 0:
        print("No se generó dataset unificado.", file=sys.stderr)
        return 1
    unified_path, valid_path = export_unified(df_unified, df_valid)
    latest_path, config_path, totales_path = export_dashboard_auxiliaries(df_unified)
    print(f"\nUnificado: {unified_path} ({len(df_unified):,} filas)")
    print(f"Validaciones: {valid_path}")
    print(f"Últimos períodos: {latest_path}")
    print(f"Config dashboard: {config_path}")
    print(f"Totales por vista: {totales_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
