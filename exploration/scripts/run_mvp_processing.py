#!/usr/bin/env python3
"""
Procesa los 6 recursos MVP SESCO desde línea de comandos.

Alternativa scriptable a la notebook ``02_modelo_unificado_sesco.ipynb`` para
regenerar ``sesco_produccion_model_clean.csv`` y validaciones por recurso.

Uso (desde la raíz del repo)::

    python exploration/scripts/run_mvp_processing.py
    python exploration/scripts/run_mvp_processing.py --update-raw
    python exploration/scripts/run_mvp_processing.py --update-raw --allow-stale-raw
    python exploration/scripts/run_mvp_processing.py --force-download

Notes
-----
También exporta auxiliares del dashboard (``sesco_latest_periods_by_view.csv``,
etc.) con la misma lógica que la notebook ``03_validacion_final_sesco.ipynb``.
"""
from __future__ import annotations

import argparse
import os
import sys

from sesco_processing import (
    SESCO_RESOURCES_MVP,
    ensure_raw_snapshot,
    export_dashboard_auxiliaries,
    export_unified,
    get_last_raw_snapshot_status,
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
    parser.add_argument(
        "--allow-stale-raw",
        action="store_true",
        help=(
            "Si CKAN no está disponible, reutiliza raw/latest válido en lugar de fallar. "
            "Sin este flag, --update-raw y --force-download siguen en modo estricto."
        ),
    )
    return parser.parse_args()


def _append_github_step_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n### Estado de actualización SESCO\n\n")
            fh.write("```\n")
            fh.write(text.rstrip())
            fh.write("\n```\n")
    except OSError:
        pass


def _log_snapshot_status() -> None:
    status = get_last_raw_snapshot_status()
    if status is None:
        return
    lines = [
        "Estado de actualización raw:",
        f"  ckan_available = {str(status.ckan_available).lower()}",
        f"  used_stale_raw = {str(status.used_stale_raw).lower()}",
        f"  last_successful_snapshot = {status.last_successful_snapshot}",
        f"  error = {status.error or ''}",
    ]
    text = "\n".join(lines)
    print(text)
    if status.used_stale_raw:
        _append_github_step_summary(text)


def main() -> int:
    args = parse_args()
    raw_dir = resolve_raw_dir()
    package = None

    if args.force_download:
        snapshot_dir = ensure_raw_snapshot(
            force_download=True,
            allow_stale_raw=args.allow_stale_raw,
        )
        raw_dir = snapshot_dir
        print(f"Snapshot raw (forzado): {snapshot_dir}")
        _log_snapshot_status()
    elif args.update_raw:
        snapshot_dir = ensure_raw_snapshot(
            force_download=False,
            allow_stale_raw=args.allow_stale_raw,
        )
        raw_dir = snapshot_dir
        print(f"Snapshot raw: {snapshot_dir}")
        _log_snapshot_status()

    status = get_last_raw_snapshot_status()
    download_if_missing = True
    if status is not None and status.package is not None:
        package = status.package
        if status.used_stale_raw:
            download_if_missing = False

    _, df_unified, df_valid = process_all_resources(
        SESCO_RESOURCES_MVP,
        package=package,
        raw_dir=raw_dir,
        verbose=True,
        download_if_missing=download_if_missing,
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
