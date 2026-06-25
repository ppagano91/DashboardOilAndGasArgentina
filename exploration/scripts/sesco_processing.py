"""
Pipeline ETL exploratorio para datos de producción SESCO (datos.gob.ar).

Concentra la lógica reutilizable extraída de ``01_exploracion_sesco.ipynb`` y
generalizada en ``02_modelo_unificado_sesco.ipynb``. Procesa los 6 recursos MVP
(provincia / cuenca / empresa × petróleo / gas): consulta CKAN, descarga CSV
versionados en ``data/raw/snapshots/``, mantiene ``data/raw/latest/``, normaliza
columnas heterogéneas, construye un modelo analítico común, detecta períodos
incompletos y exporta archivos en ``exploration/data/processed/``.

Esta etapa precede a una arquitectura formal con PostgreSQL/PostGIS; varias
funciones de este módulo son candidatas a migrar a un ETL de producción.

Notes
-----
- Provincia, cuenca y empresa son vistas alternativas: no deben sumarse entre sí.
- El último período válido se calcula por recurso o por ``producto`` +
  ``agrupador_tipo``, nunca como máximo global del dataset.
- Petróleo y gas conservan unidades distintas; el modelo los separa por columna
  ``producto``.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

# Identificador oficial del dataset en datos.gob.ar (único punto de verdad CKAN).
PACKAGE_ID = "energia-produccion-petroleo-gas-sesco"
CKAN_URL = f"https://datos.gob.ar/api/3/action/package_show?id={PACKAGE_ID}"
USER_AGENT = "OilGas-Exploration/1.0"

# Rutas relativas al módulo: independientes del directorio de trabajo al ejecutar CLI.
SCRIPT_DIR = Path(__file__).resolve().parent
EXPLORATION_DIR = SCRIPT_DIR.parent
RAW_DIR = EXPLORATION_DIR / "data" / "raw"
RAW_SNAPSHOTS_DIR = RAW_DIR / "snapshots"
RAW_LATEST_DIR = RAW_DIR / "latest"
PROCESSED_DIR = EXPLORATION_DIR / "data" / "processed"

# Configuración MVP compartida (scripts CLI y snapshots).
SESCO_RESOURCES_MVP: dict[str, dict[str, Any]] = {
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

# Campos CKAN persistidos en manifest.json por recurso.
MANIFEST_RESOURCE_FIELDS = [
    "id",
    "name",
    "url",
    "format",
    "mimetype",
    "size",
    "created",
    "last_modified",
    "cache_last_updated",
    "revision_timestamp",
    "hash",
]

# Campos usados para detectar cambios entre snapshots (CKAN puede omitir algunos).
RESOURCE_CHANGE_FIELDS = [
    "resource_id",
    "url",
    "last_modified",
    "cache_last_updated",
    "revision_timestamp",
    "size",
    "hash",
]

RESOURCE_FIELDS = [
    "name",
    "id",
    "format",
    "url",
    "created",
    "last_modified",
    "size",
]

# Columnas mínimas que debe tener df_model antes de preprocess_model_df.
REQUIRED_MODEL_COLUMNS = [
    "periodo",
    "produccion",
    "agrupador_nombre",
    "producto",
    "agrupador_tipo",
]

# Esquema estable del CSV final consumido por el unificado y Streamlit.
EXPORT_COLUMNS = [
    "periodo_str",
    "periodo_dt",
    "anio",
    "mes",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
    "produccion",
    "source_resource",
]

# Orden de preferencia al detectar la columna de período en CSV SESCO heterogéneos.
PERIOD_COLUMN_PRIORITY = (
    "indice_tiempo",
    "periodo",
    "fecha",
    "anio_mes",
)

# Clave lógica para detectar duplicados en el dataset unificado.
DUPLICATE_KEY_COLUMNS = [
    "periodo_str",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
]


@dataclass
class ProcessResult:
    """
    Resultado estructurado del procesamiento de un recurso SESCO individual.

    Agrupa en un único objeto los DataFrames intermedios, metadatos de períodos,
    validaciones, rutas de archivos y notas de ejecución generados al procesar
    un recurso MVP (por ejemplo petróleo por provincia, gas por cuenca).

    Se crea al final de ``process_resource`` (una instancia por recurso) y se
    acumula en una lista dentro de ``process_all_resources`` para unificar
    resultados y armar ``sesco_validaciones_resumen.csv``.

  Diferencia entre capas de datos
    - ``df_model``: modelo analítico recién construido; puede tener nulos y
      períodos sin parsear del todo.
    - ``df_model_clean``: tras ``preprocess_model_df``; tipos normalizados y
      filas sin claves nulas; **puede** incluir el último período incompleto.
    - ``df_model_clean_valid``: tras ``apply_valid_periods``; excluye el último
      mes si la regla del 50 % lo marcó incompleto; **base de los CSV exportados**.
    - ``validation``: diccionario resumen (una fila de validación), no un
      DataFrame de producción.
    - Los resúmenes por período (``*_periodos_resumen.csv``) se escriben en
      disco vía ``export_period_summary``; no forman parte de este dataclass.

    Attributes
    ----------
    resource_key : str
        Obligatorio. Identificador interno del recurso (ej. ``"petroleo_provincia"``).
        Tipo: metadato / clave de configuración. Ejemplo: ``"gas_empresa"``.
        Uso: prefijo de archivos exportados y filas de validación.
    config : dict[str, Any]
        Obligatorio. Configuración MVP del recurso (claves en español por
        contrato: ``producto``, ``agrupador_tipo``, ``nombre_recurso``).
        Tipo: metadato. Ejemplo: ``{"producto": "gas", "agrupador_tipo": "cuenca", ...}``.
        Uso: trazabilidad y ``validate_resource_basic``.
    df_model : pd.DataFrame
        Obligatorio. Modelo intermedio post-``build_model_df``, pre-limpieza final.
        Tipo: datos (intermedio). Columnas del modelo en español (``producto``, etc.).
        Uso: auditoría de filas originales y conteo de nulos en producción.
    df_model_clean : pd.DataFrame
        Obligatorio. Salida de ``preprocess_model_df`` con ``periodo_dt`` y tipos listos.
        Tipo: datos (limpios, posible último período incompleto).
        Uso: entrada de ``detect_incomplete_period``; inspección en notebooks.
    df_model_clean_valid : pd.DataFrame
        Obligatorio. Subconjunto con períodos válidos para análisis y export.
        Tipo: datos (finales del recurso). Uso: ``export_model_clean``, unificado.
    period_info : dict[str, Any]
        Obligatorio. Metadatos de períodos (salida de ``detect_incomplete_period``).
        Tipo: metadato. Claves: ``latest_period``, ``latest_valid_period``,
        ``excluded_period``, ``ratio``, ``valid_periods``.
        Uso: ``apply_valid_periods``, ``validate_resource_basic``.
    validation : dict[str, Any]
        Obligatorio. Fila de métricas para ``sesco_validaciones_resumen.csv``.
        Tipo: validación. Claves en español (``cantidad_filas_limpias``, etc.).
        Uso: concatenación en ``process_all_resources``.
    raw_path : Path
        Obligatorio. Ruta del CSV descargado en ``data/raw/``.
        Tipo: path / estado de ejecución.
    export_path : Path
        Obligatorio. Ruta del ``{resource_key}_model_clean.csv`` generado.
        Tipo: path / estado de ejecución.
    notes : list[str]
        Opcional (default lista vacía). Notas de ejecución: descarga CKAN,
        período excluido, errores capturados.
        Tipo: estado de ejecución. Se serializa en ``validation["observaciones"]``.
    """

    resource_key: str
    config: dict[str, Any]
    df_model: pd.DataFrame
    df_model_clean: pd.DataFrame
    df_model_clean_valid: pd.DataFrame
    period_info: dict[str, Any]
    validation: dict[str, Any]
    raw_path: Path
    export_path: Path
    notes: list[str] = field(default_factory=list)


def normalize_text(value: str) -> str:
    """
    Normaliza texto para comparaciones insensibles a acentos y mayúsculas.

    Parameters
    ----------
    value : str
        Texto a normalizar (nombre CKAN, columna, etc.).

    Returns
    -------
    str
        Texto en minúsculas sin diacríticos, recortado.
    """
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def fetch_ckan_package(timeout: int = 60, retries: int = 3, backoff_s: float = 2.0) -> dict:
    """
    Consulta metadata del dataset SESCO en la API CKAN de datos.gob.ar.

    Parameters
    ----------
    timeout : int
        Segundos de espera por intento HTTP.
    retries : int
        Cantidad máxima de intentos ante errores transitorios.
    backoff_s : float
        Base de espera entre reintentos (multiplicada por el número de intento).

    Returns
    -------
    dict
        Nodo ``result`` del JSON CKAN (incluye ``resources``).

    Notes
    -----
    CKAN puede responder HTTP 500 de forma temporal; se reintenta antes de fallar.
    """
    request = urllib.request.Request(CKAN_URL, headers={"User-Agent": USER_AGENT})

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("success"):
                raise RuntimeError(f"CKAN respondió con error: {payload.get('error')}")
            return payload["result"]
        except urllib.error.HTTPError as exc:
            # A veces CKAN responde 500 temporalmente.
            last_exc = exc
        except urllib.error.URLError as exc:
            last_exc = exc

        if attempt < retries:
            time.sleep(backoff_s * attempt)

    raise RuntimeError(f"No se pudo consultar CKAN luego de {retries} intentos: {last_exc}")


def list_resources(package: dict | None = None) -> pd.DataFrame:
    """
    Lista recursos del paquete CKAN como tabla plana.

    Parameters
    ----------
    package : dict, optional
        Paquete CKAN ya obtenido. Si es ``None``, llama a ``fetch_ckan_package``.

    Returns
    -------
    pd.DataFrame
        Una fila por recurso con campos definidos en ``RESOURCE_FIELDS``.
    """
    if package is None:
        package = fetch_ckan_package()
    rows = [
        {field: resource.get(field) for field in RESOURCE_FIELDS}
        for resource in package.get("resources", [])
    ]
    return pd.DataFrame(rows)


def find_resource_by_name(
    resources_df: pd.DataFrame,
    resource_name: str,
    *,
    fallback_keywords: list[str] | None = None,
) -> pd.Series | None:
    """
    Busca un recurso CSV por nombre exacto (normalizado) o por palabras clave.

    Parameters
    ----------
    resources_df : pd.DataFrame
        Tabla de recursos CKAN (salida de ``list_resources``).
    resource_name : str
        Nombre oficial esperado del recurso.
    fallback_keywords : list[str], optional
        Si no hay match exacto, exige que todas las keywords aparezcan en el nombre.

    Returns
    -------
    pd.Series or None
        Fila del recurso elegido, o ``None`` si no hay candidatos.

    Notes
    -----
    Ante múltiples coincidencias prioriza: formato CSV, nombre con "promedio",
  y ``last_modified`` más reciente. Evita depender de URLs fijas en código.
    """
    target = normalize_text(resource_name)
    matches: list[pd.Series] = []

    for _, row in resources_df.iterrows():
        if normalize_text(row["name"]) == target:
            matches.append(row)

    if not matches and fallback_keywords:
        for _, row in resources_df.iterrows():
            normalized = normalize_text(row["name"])
            if all(normalize_text(kw) in normalized for kw in fallback_keywords):
                matches.append(row)

    if not matches:
        return None

    df_match = pd.DataFrame(matches)
    df_match["is_csv"] = df_match["format"].astype(str).str.upper().eq("CSV")
    df_match["is_promedio"] = df_match["name"].apply(
        lambda x: "promedio" in normalize_text(x)
    )
    df_match = df_match.sort_values(
        ["is_csv", "is_promedio", "last_modified"],
        ascending=[False, False, False],
    )
    return df_match.iloc[0]


def get_resource_local_filename(resource_key: str) -> str:
    """
    Nombre canónico del CSV raw local para un recurso MVP.

    Parameters
    ----------
    resource_key : str
        Clave interna (ej. ``petroleo_provincia``).

    Returns
    -------
    str
        ``{resource_key}.csv`` — usado en snapshots, ``latest/`` y ``raw/``.
    """
    if resource_key not in SESCO_RESOURCES_MVP:
        raise KeyError(
            f"resource_key desconocido: {resource_key!r}. "
            f"Claves MVP: {sorted(SESCO_RESOURCES_MVP)}"
        )
    return f"{resource_key}.csv"


def snapshot_filename(resource_key: str) -> str:
    """Alias de ``get_resource_local_filename`` (compatibilidad)."""
    return get_resource_local_filename(resource_key)


def get_today_snapshot_dir(base_dir: Path | None = None) -> Path:
    """
    Devuelve la carpeta de snapshot del día en formato ISO ``YYYY-MM-DD``.

    Parameters
    ----------
    base_dir : Path, optional
        Directorio base de snapshots; por defecto ``RAW_SNAPSHOTS_DIR``.

    Returns
    -------
    Path
        ``exploration/data/raw/snapshots/YYYY-MM-DD`` (puede no existir aún).
    """
    snapshots_root = base_dir or RAW_SNAPSHOTS_DIR
    return snapshots_root / date.today().isoformat()


def load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    """
    Lee ``manifest.json`` de un snapshot.

    Parameters
    ----------
    snapshot_dir : Path
        Carpeta del snapshot.

    Returns
    -------
    dict
        Contenido del manifest, o dict vacío si falta o es inválido.
    """
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        with manifest_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def save_manifest(snapshot_dir: Path, manifest: dict[str, Any]) -> None:
    """
    Persiste ``manifest.json`` indentado en la carpeta del snapshot.

    Parameters
    ----------
    snapshot_dir : Path
        Carpeta destino.
    manifest : dict
        Metadata del snapshot (recursos CKAN, fechas, etc.).
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshot_dir / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _is_valid_manifest(manifest: dict[str, Any]) -> bool:
    resources = manifest.get("resources")
    return isinstance(resources, dict) and len(resources) > 0


def get_latest_snapshot_dir(snapshots_dir: Path | None = None) -> Path | None:
    """
    Busca el snapshot más reciente con ``manifest.json`` válido.

    Parameters
    ----------
    snapshots_dir : Path, optional
        Raíz de snapshots; por defecto ``RAW_SNAPSHOTS_DIR``.

    Returns
    -------
    Path or None
        Carpeta del snapshot más reciente, o ``None`` si no hay snapshots válidos.

    Notes
    -----
    Ordena por nombre de carpeta (``YYYY-MM-DD`` o ``YYYY-MM-DD_HHMMSS``) de forma
    lexicográfica descendente.
    """
    root = snapshots_dir or RAW_SNAPSHOTS_DIR
    if not root.is_dir():
        return None

    candidates: list[str] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        manifest = load_manifest(entry)
        if _is_valid_manifest(manifest):
            candidates.append(entry.name)

    if not candidates:
        return None

    latest_name = sorted(candidates, reverse=True)[0]
    return root / latest_name


def build_resource_metadata(resource: dict[str, Any]) -> dict[str, Any]:
    """
    Extrae metadata relevante de un recurso CKAN (campos crudos de la API).

    Parameters
    ----------
    resource : dict
        Nodo ``resources[]`` del paquete CKAN.

    Returns
    -------
    dict
        Subconjunto de campos CKAN; ``None`` si el campo no existe en la fuente.
    """
    return {field: resource.get(field) for field in MANIFEST_RESOURCE_FIELDS}


def build_manifest_resource_entry(
    resource_key: str,
    ckan_resource: dict[str, Any],
    *,
    downloaded: bool = True,
    reason: str = "ckan_download",
) -> dict[str, Any]:
    """
    Construye la entrada de un recurso para ``manifest.json``.

    Parameters
    ----------
    resource_key : str
        Clave interna MVP.
    ckan_resource : dict
        Recurso completo desde CKAN.
    downloaded : bool
        Si el CSV se descargó en este snapshot.
    reason : str
        Motivo del snapshot (``initial_snapshot``, ``ckan_changed``, ``force_download``, etc.).

    Returns
    -------
    dict
        Entrada con ``local_file`` canónico y metadata CKAN normalizada.
    """
    ckan_meta = build_resource_metadata(ckan_resource)
    return {
        "resource_key": resource_key,
        "local_file": get_resource_local_filename(resource_key),
        "resource_id": ckan_meta.get("id"),
        "name": ckan_meta.get("name"),
        "url": ckan_meta.get("url"),
        "format": ckan_meta.get("format"),
        "mimetype": ckan_meta.get("mimetype"),
        "size": ckan_meta.get("size"),
        "created": ckan_meta.get("created"),
        "last_modified": ckan_meta.get("last_modified"),
        "cache_last_updated": ckan_meta.get("cache_last_updated"),
        "revision_timestamp": ckan_meta.get("revision_timestamp"),
        "hash": ckan_meta.get("hash"),
        "downloaded": downloaded,
        "reason": reason,
    }


def _manifest_field_value(entry: dict[str, Any], field: str) -> Any:
    if field == "resource_id":
        return entry.get("resource_id") or entry.get("id")
    return entry.get(field)


def has_resource_changed(current_metadata: dict[str, Any], previous_metadata: dict[str, Any]) -> bool:
    """
    Indica si la metadata CKAN de un recurso cambió respecto al último snapshot.

    Parameters
    ----------
    current_metadata : dict
        Metadata actual (``build_resource_metadata``).
    previous_metadata : dict
        Metadata del manifest anterior para el mismo ``resource_key``.

    Returns
    -------
    bool
        ``True`` si no hay metadata previa o algún campo relevante difiere.

    Notes
    -----
    CKAN puede no publicar todos los campos (``hash``, ``revision_timestamp``, etc.).
    Solo se comparan campos presentes en al menos uno de los dos lados; si todos los
    disponibles coinciden, se considera sin cambios.
    """
    if not previous_metadata:
        return True

    for field in RESOURCE_CHANGE_FIELDS:
        current = _manifest_field_value(current_metadata, field)
        previous = _manifest_field_value(previous_metadata, field)
        if current is None and previous is None:
            continue
        if str(current) != str(previous):
            return True
    return False


def _find_full_resource(package: dict[str, Any], resource_id: str) -> dict[str, Any]:
    for resource in package.get("resources", []):
        if resource.get("id") == resource_id:
            return resource
    raise ValueError(f"Recurso CKAN no encontrado en paquete: {resource_id}")


def _collect_mvp_resource_metadata(
    package: dict[str, Any],
    resources_df: pd.DataFrame,
    resources_config: dict[str, dict[str, Any]],
    *,
    downloaded: bool = True,
    reason: str = "ckan_download",
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for resource_key, config in resources_config.items():
        row = find_resource_by_name(
            resources_df,
            config["nombre_recurso"],
            fallback_keywords=[config["producto"], config["agrupador_tipo"]],
        )
        if row is None:
            raise ValueError(
                f"Recurso no encontrado en CKAN: {config['nombre_recurso']} ({resource_key})"
            )
        full_resource = _find_full_resource(package, row["id"])
        metadata[resource_key] = build_manifest_resource_entry(
            resource_key,
            full_resource,
            downloaded=downloaded,
            reason=reason,
        )
    return metadata


def _any_resource_changed(
    current: dict[str, dict[str, Any]],
    previous_manifest: dict[str, Any],
) -> bool:
    previous_resources = previous_manifest.get("resources", {})
    if not isinstance(previous_resources, dict) or not previous_resources:
        return True
    for resource_key, current_meta in current.items():
        prev_meta = previous_resources.get(resource_key, {})
        if has_resource_changed(current_meta, prev_meta):
            return True
    return False


def sync_latest_from_snapshot(snapshot_dir: Path) -> None:
    """
    Actualiza ``raw/latest/`` copiando CSV y manifest desde un snapshot.

    Parameters
    ----------
    snapshot_dir : Path
        Snapshot fuente.

    Notes
    -----
    ``latest/`` es una copia de conveniencia (no symlink) para compatibilidad Windows.
    """
    RAW_LATEST_DIR.mkdir(parents=True, exist_ok=True)
    for item in RAW_LATEST_DIR.iterdir():
        if item.is_file():
            item.unlink()

    for csv_path in sorted(snapshot_dir.glob("*.csv")):
        shutil.copy2(csv_path, RAW_LATEST_DIR / csv_path.name)

    manifest_src = snapshot_dir / "manifest.json"
    if manifest_src.is_file():
        shutil.copy2(manifest_src, RAW_LATEST_DIR / "manifest.json")


def _download_mvp_resources(
    snapshot_dir: Path,
    resources_config: dict[str, dict[str, Any]],
    resources_df: pd.DataFrame,
) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for resource_key, config in resources_config.items():
        row = find_resource_by_name(
            resources_df,
            config["nombre_recurso"],
            fallback_keywords=[config["producto"], config["agrupador_tipo"]],
        )
        if row is None:
            raise ValueError(
                f"Recurso no encontrado en CKAN: {config['nombre_recurso']} ({resource_key})"
            )
        destination = snapshot_dir / get_resource_local_filename(resource_key)
        download_csv(row["url"], destination, overwrite=True)


def ensure_raw_snapshot(
    force_download: bool = False,
    update_latest: bool = True,
    resources_config: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """
    Asegura un snapshot raw versionado de los recursos MVP desde CKAN.

    Parameters
    ----------
    force_download : bool
        Si es ``True``, descarga aunque CKAN no haya cambiado.
    update_latest : bool
        Si es ``True``, sincroniza ``raw/latest/`` con el snapshot seleccionado.
    resources_config : dict, optional
        Mapa ``resource_key`` → config MVP; por defecto ``SESCO_RESOURCES_MVP``.

    Returns
    -------
    Path
        Carpeta del snapshot a usar para procesamiento.

    Notes
    -----
    - Sin snapshots previos: crea ``snapshots/YYYY-MM-DD/``, descarga los 6 CSV,
      escribe manifest y actualiza ``latest/``.
    - CKAN sin cambios: reutiliza el último snapshot válido (sin descargas duplicadas).
    - CKAN con cambios: crea snapshot del día y descarga los 6 recursos (snapshot completo).
    - ``force_download=True`` con carpeta del día existente: usa sufijo ``_HHMMSS``.
    """
    resources_config = resources_config or SESCO_RESOURCES_MVP
    package = fetch_ckan_package()
    resources_df = list_resources(package)

    latest_snapshot = get_latest_snapshot_dir()
    previous_manifest = load_manifest(latest_snapshot) if latest_snapshot else {}
    today_dir = get_today_snapshot_dir()

    preview_metadata = _collect_mvp_resource_metadata(
        package,
        resources_df,
        resources_config,
        downloaded=False,
        reason="metadata_check",
    )

    needs_download = force_download or _any_resource_changed(
        preview_metadata, previous_manifest
    )

    if not needs_download:
        target = latest_snapshot
        if target is None and today_dir.exists() and _is_valid_manifest(load_manifest(today_dir)):
            target = today_dir
        if target is not None:
            if update_latest:
                sync_latest_from_snapshot(target)
            return target

    if force_download:
        download_reason = "force_download"
    elif not previous_manifest.get("resources"):
        download_reason = "initial_snapshot"
    else:
        download_reason = "ckan_changed"

    current_metadata = _collect_mvp_resource_metadata(
        package,
        resources_df,
        resources_config,
        downloaded=True,
        reason=download_reason,
    )

    if today_dir.exists() and _is_valid_manifest(load_manifest(today_dir)):
        stamp = datetime.now().strftime("%H%M%S")
        snapshot_dir = today_dir.parent / f"{date.today().isoformat()}_{stamp}"
    else:
        snapshot_dir = today_dir
    _download_mvp_resources(snapshot_dir, resources_config, resources_df)

    manifest: dict[str, Any] = {
        "snapshot_date": snapshot_dir.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "timezone_note": "created_at en hora local del sistema",
        "package_id": PACKAGE_ID,
        "ckan_url": CKAN_URL,
        "resources": current_metadata,
    }
    save_manifest(snapshot_dir, manifest)

    if update_latest:
        sync_latest_from_snapshot(snapshot_dir)

    return snapshot_dir


def resolve_raw_dir() -> Path:
    """
    Resuelve el directorio raw preferido para procesamiento por lote.

    Returns
    -------
    Path
        ``raw/latest/`` si existe con manifest válido; si no, ``RAW_DIR``.

    Notes
    -----
    Para resolver el path de un recurso concreto, preferir ``resolve_raw_resource_path``.
    """
    if RAW_LATEST_DIR.is_dir() and _is_valid_manifest(load_manifest(RAW_LATEST_DIR)):
        return RAW_LATEST_DIR
    return RAW_DIR


def resolve_raw_resource_path(
    resource_key: str,
    *,
    verbose: bool = False,
) -> Path:
    """
    Devuelve el path del CSV raw canónico a leer para un recurso MVP.

    Parameters
    ----------
    resource_key : str
        Clave interna (ej. ``petroleo_provincia``).
    verbose : bool
        Reservado; sin efecto (compatibilidad con llamadas existentes).

    Returns
    -------
    Path
        Archivo CSV existente con nombre canónico.

    Raises
    ------
    FileNotFoundError
        Si no existe el archivo canónico en ninguna ubicación revisada.

    Notes
    -----
    Orden de búsqueda (solo nombres canónicos, coincidencia exacta):

    1. ``raw/latest/{resource_key}.csv``
    2. Último snapshot válido ``/{resource_key}.csv``
    3. ``raw/{resource_key}.csv``

    No se buscan nombres derivados de CKAN ni archivos históricos con otros nombres.
    """
    _ = verbose
    canonical = get_resource_local_filename(resource_key)
    latest_snapshot = get_latest_snapshot_dir()
    snapshot_label = latest_snapshot.name if latest_snapshot else "<ninguno>"

    candidates: list[tuple[Path, str]] = [
        (RAW_LATEST_DIR / canonical, f"exploration/data/raw/latest/{canonical}"),
    ]
    if latest_snapshot is not None:
        candidates.append(
            (
                latest_snapshot / canonical,
                f"exploration/data/raw/snapshots/{snapshot_label}/{canonical}",
            )
        )
    candidates.append((RAW_DIR / canonical, f"exploration/data/raw/{canonical}"))

    checked_labels: list[str] = []
    for path, label in candidates:
        checked_labels.append(f"- {label}")
        if path.is_file():
            return path

    locations = "\n".join(checked_labels)
    raise FileNotFoundError(
        f"No se encontró el archivo raw canónico para resource_key={resource_key!r}.\n"
        f"Nombre esperado: {canonical}\n"
        f"Ubicaciones revisadas:\n{locations}\n\n"
        f"Para resolverlo:\n"
        f"- ejecutar ensure_raw_snapshot(), o\n"
        f"- renombrar manualmente el archivo al nombre canónico."
    )


def infer_raw_filename(resource_key: str, resource_name: str = "", url: str = "") -> str:
    """
    Devuelve el nombre canónico del CSV raw local.

    Parameters
    ----------
    resource_key : str
        Clave interna del recurso.
    resource_name : str
        Ignorado (compatibilidad hacia atrás).
    url : str
        Ignorado (compatibilidad hacia atrás).

    Returns
    -------
    str
        Nombre canónico ``{resource_key}.csv``.

    Notes
    -----
    Preferir ``get_resource_local_filename``. Los parámetros ``resource_name`` y
    ``url`` se conservan solo para no romper firmas antiguas.
    """
    _ = resource_name, url
    return get_resource_local_filename(resource_key)


def download_csv(
    url: str,
    destination: Path,
    timeout: int = 120,
    *,
    overwrite: bool = False,
) -> Path:
    """
    Descarga un CSV remoto si no existe ya en disco local.

    Parameters
    ----------
    url : str
        URL directa del recurso.
    destination : Path
        Ruta de destino (típicamente bajo ``RAW_DIR`` o un snapshot).
    timeout : int
        Segundos de espera HTTP.
    overwrite : bool
        Si es ``True``, sobrescribe un archivo existente.

    Returns
    -------
    Path
        Ruta al archivo local (existente o recién descargado).

    Notes
    -----
    Por defecto no sobrescribe archivos existentes. Los snapshots usan
    ``overwrite=True`` para garantizar contenido actualizado.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        return destination

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            destination.write_bytes(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No se pudo descargar: {exc.reason}") from exc
    return destination


def read_csv(path: Path) -> pd.DataFrame:
    """
    Lee un CSV SESCO con el encoding habitual de datos.gob.ar.

    Parameters
    ----------
    path : Path
        Ruta al archivo CSV raw.

    Returns
    -------
    pd.DataFrame
        Contenido sin transformar (columnas originales).
    """
    return pd.read_csv(path, encoding="utf-8-sig")


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Unifica nombres de columnas a snake_case sin acentos.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame con encabezados heterogéneos del CSV fuente.

    Returns
    -------
    pd.DataFrame
        Copia con columnas normalizadas para detección heurística posterior.

    Notes
    -----
    Primer paso de transformación tras la lectura raw; habilita ``detect_*``
    sin depender del nombre exacto publicado por SESCO.
    """

    def clean(name: str) -> str:
        text = unicodedata.normalize("NFKD", str(name))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.strip().lower()
        text = re.sub(r"[^a-z0-9]+", "_", text)
        return text.strip("_")

    out = df.copy()
    out.columns = [clean(col) for col in out.columns]
    return out


def detect_date_columns(columns: list[str] | pd.Index) -> list[str]:
    """
    Identifica columnas relacionadas con período o fecha por tokens en el nombre.

    Parameters
    ----------
    columns : list[str] or pd.Index
        Nombres de columnas ya normalizados.

    Returns
    -------
    list[str]
        Subconjunto de columnas candidatas a período.
    """
    tokens = ("anio", "ano", "mes", "fecha", "periodo", "indice", "tiempo")
    return [col for col in columns if any(token in col for token in tokens)]


def detect_numeric_columns(df: pd.DataFrame) -> list[str]:
    """Lista columnas numéricas del DataFrame (soporte para detección de producción)."""
    return df.select_dtypes(include="number").columns.tolist()


def detect_categorical_columns(df: pd.DataFrame) -> list[str]:
    """Columnas no numéricas ni date-like; candidatas a agrupador geográfico/empresa."""
    numeric = set(detect_numeric_columns(df))
    date_like = set(detect_date_columns(df.columns))
    return [col for col in df.columns if col not in numeric and col not in date_like]


def detect_period_column(columns: list[str] | pd.Index) -> str | None:
    """
    Selecciona la columna principal de período según prioridad documentada.

    Parameters
    ----------
    columns : list[str] or pd.Index
        Nombres de columnas normalizados.

    Returns
    -------
    str or None
        Nombre de la columna elegida, o ``None`` si solo hay ``anio``/``mes``.

    Notes
    -----
    Orden: ``indice_tiempo`` → ``periodo`` → ``fecha`` → ``anio_mes`` → otra
    date-like. Si retorna ``None``, ``preprocess_model_df`` arma ``periodo_str``
    desde columnas ``anio`` y ``mes``.
    """
    cols = list(columns)
    for candidate in PERIOD_COLUMN_PRIORITY:
        if candidate in cols:
            return candidate
    date_cols = detect_date_columns(cols)
    for col in date_cols:
        if col not in ("anio", "mes", "ano"):
            return col
    return None


def find_production_column(df_norm: pd.DataFrame, product: str) -> str | None:
    """
    Detecta la columna de producción según el producto (petróleo o gas).

    Parameters
    ----------
    df_norm : pd.DataFrame
        DataFrame con columnas normalizadas.
    product : str
        ``"petroleo"`` o ``"gas"`` (valor de la columna exportada ``producto``).

    Returns
    -------
    str or None
        Nombre de columna detectada, o ``None`` si no hay candidato confiable.
    """
    columns = list(df_norm.columns)
    product_tokens = ("petroleo", "petr") if product == "petroleo" else ("gas",)

    for col in columns:
        norm = normalize_text(col)
        if "produccion" in norm and any(t in norm for t in product_tokens):
            return col

    for col in columns:
        if "produccion" in col or col.startswith("prod"):
            return col

    numeric = detect_numeric_columns(df_norm)
    excluded = {"anio", "mes", "ano"}
    candidates = [c for c in numeric if c not in excluded]
    return candidates[0] if candidates else None


def find_group_column(df_norm: pd.DataFrame, grouping_type: str) -> str | None:
    """
    Detecta la columna del agrupador (provincia, cuenca o empresa).

    Parameters
    ----------
    df_norm : pd.DataFrame
        DataFrame con columnas normalizadas.
    grouping_type : str
        Tipo de vista: ``"provincia"``, ``"cuenca"`` o ``"empresa"``.

    Returns
    -------
    str or None
        Nombre de columna que contiene el agrupador.
    """
    for col in df_norm.columns:
        if grouping_type in col:
            return col
    cat_cols = detect_categorical_columns(df_norm)
    return cat_cols[0] if cat_cols else None


def infer_resource_type(name: str) -> str | None:
    """
    Clasifica el tipo de serie según palabras clave en el nombre CKAN.

    Returns
    -------
    str or None
        ``shale_tight``, ``promedio_diario``, ``serie_historica`` o ``None``.
    """
    n = normalize_text(name)
    if "shale" in n or "tight" in n:
        return "shale_tight"
    if "promedio" in n:
        return "promedio_diario"
    if "historica" in n or "serie" in n:
        return "serie_historica"
    return None


def build_model_df(
    df_norm: pd.DataFrame,
    *,
    product: str,
    grouping_type: str,
    source_resource: str,
    resource_type: str | None = None,
) -> pd.DataFrame:
    """
    Construye el DataFrame con esquema analítico común a los 6 recursos MVP.

    Parameters
    ----------
    df_norm : pd.DataFrame
        CSV normalizado (columnas en snake_case).
    product : str
        ``"petroleo"`` o ``"gas"`` (se asigna a la columna exportada ``producto``).
    grouping_type : str
        ``"provincia"``, ``"cuenca"`` o ``"empresa"`` (columna ``agrupador_tipo``).
    source_resource : str
        Nombre oficial del recurso CKAN (trazabilidad).
    resource_type : str, optional
        Clasificación de la serie; si es ``None``, se infiere del nombre.

    Returns
    -------
    pd.DataFrame
        Modelo intermedio con columnas semánticas unificadas (nombres en español).

    Notes
    -----
    Provincia, cuenca y empresa son vistas alternativas del mismo fenómeno;
    cada llamada corresponde a un único CSV fuente y un único ``agrupador_tipo``.
    """
    if resource_type is None:
        resource_type = infer_resource_type(source_resource) or "promedio_diario"

    prod_col = find_production_column(df_norm, product)
    group_col = find_group_column(df_norm, grouping_type)
    period_col = detect_period_column(df_norm.columns)

    if prod_col is None:
        raise ValueError(
            f"No se detectó columna de producción para {product} en {list(df_norm.columns)}"
        )
    if group_col is None:
        raise ValueError(
            f"No se detectó columna de agrupador '{grouping_type}' en {list(df_norm.columns)}"
        )

    df_model = pd.DataFrame()
    if period_col:
        df_model["periodo"] = df_norm[period_col]
    else:
        df_model["periodo"] = None

    if "anio" in df_norm.columns:
        df_model["anio"] = df_norm["anio"]
    elif "ano" in df_norm.columns:
        df_model["anio"] = df_norm["ano"]
    else:
        df_model["anio"] = None
    df_model["mes"] = df_norm["mes"] if "mes" in df_norm.columns else None

    df_model["producto"] = product
    df_model["agrupador_tipo"] = grouping_type
    df_model["agrupador_nombre"] = df_norm[group_col]
    df_model["tipo_recurso"] = resource_type
    df_model["produccion"] = df_norm[prod_col]
    df_model["source_resource"] = source_resource

    return df_model


def preprocess_model_df(df_model: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza período y producción; elimina filas sin datos clave.

    Parameters
    ----------
    df_model : pd.DataFrame
        Salida de ``build_model_df`` con ``REQUIRED_MODEL_COLUMNS``.

    Returns
    -------
    pd.DataFrame
        Registros con ``periodo_str``, ``periodo_dt``, ``anio``, ``mes`` y
        ``produccion`` numérica; sin filas con nulos en campos obligatorios.

    Notes
    -----
    No convierte nulos de producción a cero: los descarta con ``dropna``.
    El período incompleto del último mes se trata después en
    ``detect_incomplete_period``.
    """
    df = df_model.copy()

    missing = [c for c in REQUIRED_MODEL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    df["periodo_original"] = df["periodo"]

    periodo_raw = df["periodo"].astype(str).str.strip()
    # Formato canónico YYYY-M o YYYY-MM publicado por SESCO en indice_tiempo / periodo.
    valid_mask = periodo_raw.str.match(r"^\d{4}-\d{1,2}$", na=False)
    periodo_str = periodo_raw.where(valid_mask)

    if "anio" in df.columns and "mes" in df.columns:
        anio_num = pd.to_numeric(df["anio"], errors="coerce")
        mes_num = pd.to_numeric(df["mes"], errors="coerce")
        # Fallback cuando el CSV trae año y mes en columnas separadas.
        fallback = (
            anio_num.astype("Int64").astype(str)
            + "-"
            + mes_num.astype("Int64").astype(str).str.zfill(2)
        )
        periodo_str = periodo_str.fillna(fallback)

    df["periodo_str"] = periodo_str
    df["periodo_dt"] = pd.to_datetime(
        df["periodo_str"] + "-01", format="%Y-%m-%d", errors="coerce"
    )
    df["periodo_dt"] = df["periodo_dt"].fillna(
        pd.to_datetime(df["periodo_str"], format="%Y-%m", errors="coerce")
    )
    df["anio"] = df["periodo_dt"].dt.year
    df["mes"] = df["periodo_dt"].dt.month

    df["produccion"] = pd.to_numeric(df["produccion"], errors="coerce")

    return (
        df.dropna(subset=["periodo_dt", "produccion", "agrupador_nombre"])
        .sort_values(["periodo_dt", "agrupador_nombre"])
        .reset_index(drop=True)
    )


def detect_incomplete_period(
    df_model_clean: pd.DataFrame,
    threshold: float = 0.5,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Detecta si el último período parece una carga parcial del mes en curso.

    Parameters
    ----------
    df_model_clean : pd.DataFrame
        Datos ya preprocesados de un recurso.
    threshold : float
        Umbral mínimo (último/penúltimo) para considerar el último período válido.
        Por defecto 0.5 (regla del 50 %).
    verbose : bool
        Si es ``True``, imprime advertencia en consola.

    Returns
    -------
    dict
        ``latest_period``, ``latest_valid_period``, ``excluded_period``, ``ratio``,
        ``valid_periods`` (lista de ``periodo_dt`` aceptados).

    Notes
    -----
    No elimina filas del DataFrame de entrada; la exclusión la aplica
    ``apply_valid_periods``. Evita distorsionar KPIs cuando SESCO publica el mes
    antes de cerrarlo.
    """
    # Total nacional del recurso por período (suma de todos los agrupadores).
    agg = (
        df_model_clean.groupby(["periodo_dt", "periodo_str"], as_index=False)["produccion"]
        .sum()
        .sort_values("periodo_dt")
    )

    latest_period = agg.iloc[-1]["periodo_str"]
    latest_valid_period = latest_period
    excluded_period = None
    ratio = None
    valid_periods = agg["periodo_dt"].tolist()

    if len(agg) >= 2:
        latest_total = agg.iloc[-1]["produccion"]
        previous_total = agg.iloc[-2]["produccion"]
        ratio = latest_total / previous_total if previous_total else 1.0

        # Caída abrupta: típico de mes en curso con pocos días reportados.
        if ratio < threshold:
            excluded_period = latest_period
            latest_valid_period = agg.iloc[-2]["periodo_str"]
            valid_periods = agg.iloc[:-1]["periodo_dt"].tolist()
            if verbose:
                print(
                    f"Advertencia: se excluye {excluded_period} porque parece incompleto. "
                    f"Representa {ratio * 100:.1f}% del período anterior."
                )

    return {
        "latest_period": latest_period,
        "latest_valid_period": latest_valid_period,
        "excluded_period": excluded_period,
        "ratio": ratio,
        "valid_periods": valid_periods,
    }


def apply_valid_periods(
    df_model_clean: pd.DataFrame, period_info: dict[str, Any]
) -> pd.DataFrame:
    """
    Filtra filas a los períodos considerados válidos tras la regla del 50 %.

    Parameters
    ----------
    df_model_clean : pd.DataFrame
        Datos limpios que pueden incluir el último período incompleto.
    period_info : dict
        Salida de ``detect_incomplete_period`` (clave ``valid_periods``).

    Returns
    -------
    pd.DataFrame
        Subconjunto exportable y apto para análisis/KPIs.
    """
    valid_periods = period_info.get("valid_periods") or period_info.get("periodos_validos", [])
    return df_model_clean[
        df_model_clean["periodo_dt"].isin(valid_periods)
    ].copy()


def validate_resource_basic(
    resource_key: str,
    config: dict[str, Any],
    df_model: pd.DataFrame,
    df_model_clean_valid: pd.DataFrame,
    period_info: dict[str, Any],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Genera una fila de resumen de validación para un recurso procesado.

    Parameters
    ----------
    resource_key : str
        Clave interna del recurso.
    config : dict
        Configuración MVP del recurso (claves ``producto``, ``agrupador_tipo``, etc.).
    df_model : pd.DataFrame
        Modelo original (antes de filtrar períodos incompletos).
    df_model_clean_valid : pd.DataFrame
        Datos exportables.
    period_info : dict
        Metadatos de períodos válidos.
    notes : list[str], optional
        Notas acumuladas (descarga, exclusión de período, etc.).

    Returns
    -------
    dict
        Métricas para una fila de ``sesco_validaciones_resumen.csv`` (claves en español).
    """
    obs = "; ".join(notes) if notes else ""

    return {
        "resource_key": resource_key,
        "producto": config["producto"],
        "agrupador_tipo": config["agrupador_tipo"],
        "cantidad_filas_original": len(df_model),
        "cantidad_filas_limpias": len(df_model_clean_valid),
        "periodo_min": df_model_clean_valid["periodo_str"].min()
        if len(df_model_clean_valid)
        else None,
        "periodo_max_disponible": period_info.get("latest_period"),
        "latest_valid_period": period_info.get("latest_valid_period"),
        "excluded_period": period_info.get("excluded_period") or "",
        "cantidad_agrupadores": df_model_clean_valid["agrupador_nombre"].nunique()
        if len(df_model_clean_valid)
        else 0,
        "cantidad_nulos_produccion": int(df_model["produccion"].isna().sum()),
        "cantidad_produccion_negativa": int(
            (df_model_clean_valid["produccion"] < 0).sum()
        ),
        "observaciones": obs,
    }


def prepare_export_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Selecciona columnas finales y formatea ``periodo_dt`` para export CSV.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame con el esquema del modelo (pre o post filtro de períodos).

    Returns
    -------
    pd.DataFrame
        Subconjunto según ``EXPORT_COLUMNS`` listo para ``to_csv``.
    """
    export = df[EXPORT_COLUMNS].copy()
    if pd.api.types.is_datetime64_any_dtype(export["periodo_dt"]):
        export["periodo_dt"] = export["periodo_dt"].dt.strftime("%Y-%m-%d")
    return export


def export_model_clean(
    df_model_clean_valid: pd.DataFrame,
    resource_key: str,
    processed_dir: Path | None = None,
) -> Path:
    """
    Exporta el CSV limpio individual de un recurso MVP.

    Parameters
    ----------
    df_model_clean_valid : pd.DataFrame
        Datos válidos (sin último período incompleto si correspondía).
    resource_key : str
        Prefijo del archivo (ej. ``petroleo_provincia``).
    processed_dir : Path, optional
        Directorio de salida; por defecto ``PROCESSED_DIR``.

    Returns
    -------
    Path
        Ruta a ``{resource_key}_model_clean.csv``.
    """
    processed_dir = processed_dir or PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{resource_key}_model_clean.csv"
    prepare_export_df(df_model_clean_valid).to_csv(
        path, encoding="utf-8-sig", index=False
    )
    return path


def export_period_summary(
    df_model_clean_valid: pd.DataFrame,
    resource_key: str,
    processed_dir: Path | None = None,
) -> Path:
    """
    Exporta resumen de producción total y cantidad de agrupadores por período.

    Parameters
    ----------
    df_model_clean_valid : pd.DataFrame
        Datos válidos del recurso.
    resource_key : str
        Prefijo del archivo de salida.
    processed_dir : Path, optional
        Directorio de salida.

    Returns
    -------
    Path
        Ruta a ``{resource_key}_periodos_resumen.csv``.
    """
    processed_dir = processed_dir or PROCESSED_DIR
    summary = (
        df_model_clean_valid.groupby("periodo_str", as_index=False)
        .agg(
            produccion_total=("produccion", "sum"),
            cantidad_agrupadores=("agrupador_nombre", "nunique"),
        )
        .sort_values("periodo_str")
    )
    path = processed_dir / f"{resource_key}_periodos_resumen.csv"
    summary.to_csv(path, encoding="utf-8-sig", index=False)
    return path


def format_number(value: float, decimals: int = 0) -> str:
    """Formatea número con coma decimal (presentación en notebooks)."""
    return f"{value:.{decimals}f}".replace(".", ",")


def get_valid_periods_by_group(
    df: pd.DataFrame,
    product: str,
    grouping_type: str,
    threshold: float = 0.5,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Calcula períodos válidos para una vista del dataset unificado.

    Aplica la misma regla del 50 % que ``detect_incomplete_period``, pero
    sobre el subconjunto ``producto`` + ``agrupador_tipo`` del unificado.

    Parameters
    ----------
    df : pd.DataFrame
        Dataset unificado (``sesco_produccion_model_clean``).
    product : str
        ``"petroleo"`` o ``"gas"`` (valor de columna ``producto``).
    grouping_type : str
        ``"provincia"``, ``"cuenca"`` o ``"empresa"`` (columna ``agrupador_tipo``).
    threshold : float
        Umbral último/penúltimo (default 0.5).
    verbose : bool
        Imprime advertencia si se excluye un período.

    Returns
    -------
    dict
        Metadatos de períodos, incluyendo ``product`` y ``grouping_type``.

    Notes
    -----
    Evita usar un único corte global cuando cada vista puede tener distinto
    último mes publicado por SESCO. Usado en notebook 02; el dashboard consume
    ``sesco_latest_periods_by_view.csv`` generado en notebook 03.
    """
    sub = df[
        (df["producto"] == product) & (df["agrupador_tipo"] == grouping_type)
    ].copy()
    if sub.empty:
        return {
            "product": product,
            "grouping_type": grouping_type,
            "latest_period": None,
            "latest_valid_period": None,
            "excluded_period": None,
            "ratio": None,
            "valid_periods": [],
        }

    agg = (
        sub.groupby(["periodo_dt", "periodo_str"], as_index=False)["produccion"]
        .sum()
        .sort_values("periodo_dt")
    )
    latest_period = agg.iloc[-1]["periodo_str"]
    latest_valid_period = latest_period
    excluded_period = None
    ratio = None
    valid_periods = agg["periodo_dt"].tolist()

    if len(agg) >= 2:
        latest_total = agg.iloc[-1]["produccion"]
        previous_total = agg.iloc[-2]["produccion"]
        ratio = latest_total / previous_total if previous_total else 1.0
        # Misma regla del 50 % que detect_incomplete_period, por vista del unificado.
        if ratio < threshold:
            excluded_period = latest_period
            latest_valid_period = agg.iloc[-2]["periodo_str"]
            valid_periods = agg.iloc[:-1]["periodo_dt"].tolist()
            if verbose:
                print(
                    f"[{product}/{grouping_type}] se excluye {excluded_period} "
                    f"({ratio * 100:.1f}% vs período anterior)"
                )

    return {
        "product": product,
        "grouping_type": grouping_type,
        "latest_period": latest_period,
        "latest_valid_period": latest_valid_period,
        "excluded_period": excluded_period,
        "ratio": ratio,
        "valid_periods": valid_periods,
    }


def build_totals_comparison_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compara totales por período entre vistas provincia, cuenca y empresa.

    Parameters
    ----------
    df : pd.DataFrame
        Dataset unificado o modelo con columnas estándar.

    Returns
    -------
    pd.DataFrame
        Una fila por ``periodo_str`` y ``producto`` con totales por vista y
        diferencias porcentuales respecto de provincia.

    Notes
    -----
    No suma vistas entre sí: provincia, cuenca y empresa son alternativas.
    La vista provincia se usa como referencia para porcentajes de diferencia.
    """
    base = (
        df.groupby(["periodo_str", "producto", "agrupador_tipo"], as_index=False)["produccion"]
        .sum()
    )
    pivot = (
        base.pivot_table(
            index=["periodo_str", "producto"],
            columns="agrupador_tipo",
            values="produccion",
            aggfunc="sum",
        )
        .reset_index()
        .rename_axis(None, axis=1)
        .rename(
            columns={
                "provincia": "total_provincia",
                "cuenca": "total_cuenca",
                "empresa": "total_empresa",
            }
        )
    )
    for col in ("total_provincia", "total_cuenca", "total_empresa"):
        if col not in pivot.columns:
            pivot[col] = pd.NA

    denom = pivot["total_provincia"].replace(0, pd.NA)
    # Provincia como referencia metodológica; no implica sumar las tres vistas.
    pivot["diff_cuenca_vs_provincia_pct"] = (
        (pivot["total_cuenca"] - pivot["total_provincia"]) / denom * 100
    )
    pivot["diff_empresa_vs_provincia_pct"] = (
        (pivot["total_empresa"] - pivot["total_provincia"]) / denom * 100
    )
    return pivot.sort_values(["producto", "periodo_str"]).reset_index(drop=True)


def process_resource(
    resource_key: str,
    config: dict[str, Any],
    resources_df: pd.DataFrame,
    *,
    raw_dir: Path | None = None,
    processed_dir: Path | None = None,
    incomplete_threshold: float = 0.5,
    download_if_missing: bool = True,
    verbose: bool = True,
) -> ProcessResult:
    """
    Ejecuta el pipeline ETL completo para un recurso MVP.

    Parameters
    ----------
    resource_key : str
        Clave interna (ej. ``gas_cuenca``).
    config : dict
        Debe incluir ``producto``, ``agrupador_tipo``, ``nombre_recurso``.
    resources_df : pd.DataFrame
        Catálogo CKAN (``list_resources``).
    raw_dir : Path, optional
        Directorio de CSV raw.
    processed_dir : Path, optional
        Directorio de salida procesada.
    incomplete_threshold : float
        Umbral para ``detect_incomplete_period``.
    download_if_missing : bool
        Si es ``True``, descarga desde CKAN cuando falta el raw local.
    verbose : bool
        Logs de mapeo de columnas y conteos.

    Returns
    -------
    ProcessResult
        Resultado con DataFrames intermedios, validación y rutas exportadas.

    Notes
    -----
    Secuencia: CKAN → raw → normalizar → modelo → limpiar → período incompleto
    → export individual + resumen → validación básica.
    """
    raw_dir = raw_dir or resolve_raw_dir()
    processed_dir = processed_dir or PROCESSED_DIR
    notes: list[str] = []

    fallback_kw = [config["producto"], config["agrupador_tipo"]]
    resource = find_resource_by_name(
        resources_df,
        config["nombre_recurso"],
        fallback_keywords=fallback_kw,
    )
    if resource is None:
        raise ValueError(
            f"Recurso no encontrado: {config['nombre_recurso']} ({resource_key})"
        )

    source_name = resource["name"]
    url = resource["url"]

    try:
        raw_path = resolve_raw_resource_path(resource_key, verbose=verbose)
    except FileNotFoundError:
        if download_if_missing:
            raw_path = RAW_DIR / get_resource_local_filename(resource_key)
            download_csv(url, raw_path, overwrite=True)
            notes.append("descargado desde CKAN")
        else:
            raise

    df_raw = read_csv(raw_path)
    df_norm = normalize_column_names(df_raw)

    period_col = detect_period_column(df_norm.columns)
    prod_col = find_production_column(df_norm, config["producto"])
    group_col = find_group_column(df_norm, config["agrupador_tipo"])

    if verbose:
        # Evitar caracteres unicode en consola Windows (p. ej. "←")
        print(f"\n[{resource_key}] {source_name}")
        print(f"  periodo <- {period_col or 'anio+mes'}")
        print(f"  produccion <- {prod_col}")
        print(f"  agrupador <- {group_col}")

    df_model = build_model_df(
        df_norm,
        product=config["producto"],
        grouping_type=config["agrupador_tipo"],
        source_resource=source_name,
    )

    df_model_clean = preprocess_model_df(df_model)
    # Regla del 50 %: puede marcar el último mes como incompleto a nivel recurso.
    period_info = detect_incomplete_period(
        df_model_clean, threshold=incomplete_threshold, verbose=verbose
    )
    df_valid = apply_valid_periods(df_model_clean, period_info)

    if period_info.get("excluded_period"):
        notes.append(
            f"excluido periodo {period_info['excluded_period']}"
        )

    export_path = export_model_clean(df_valid, resource_key, processed_dir)
    export_period_summary(df_valid, resource_key, processed_dir)

    validation = validate_resource_basic(
        resource_key,
        config,
        df_model,
        df_valid,
        period_info,
        notes=notes,
    )

    if verbose:
        print(
            f"  filas: {validation['cantidad_filas_original']:,} -> "
            f"{validation['cantidad_filas_limpias']:,} | "
            f"último válido: {validation['latest_valid_period']}"
        )

    return ProcessResult(
        resource_key=resource_key,
        config=config,
        df_model=df_model,
        df_model_clean=df_model_clean,
        df_model_clean_valid=df_valid,
        period_info=period_info,
        validation=validation,
        raw_path=raw_path,
        export_path=export_path,
        notes=notes,
    )


def process_all_resources(
    resources_config: dict[str, dict[str, Any]],
    *,
    package: dict | None = None,
    raw_dir: Path | None = None,
    verbose: bool = True,
) -> tuple[list[ProcessResult], pd.DataFrame, pd.DataFrame]:
    """
    Procesa todos los recursos MVP y arma el dataset unificado.

    Parameters
    ----------
    resources_config : dict
        Mapa ``resource_key`` → config (como ``SESCO_RESOURCES_MVP``).
    package : dict, optional
        Paquete CKAN precargado.
    raw_dir : Path, optional
        Directorio de CSV raw; por defecto ``resolve_raw_dir()`` (``latest/`` o ``raw/``).
    verbose : bool
        Logs por recurso y errores.

    Returns
    -------
    tuple
        ``(lista ProcessResult, df_unificado, df_validaciones)``.

    Notes
    -----
    Si un recurso falla, registra un ``ProcessResult`` vacío con observación
    de error y continúa con los demás. El unificado concatena solo recursos
    con datos válidos.
    """
    if package is None:
        package = fetch_ckan_package()
    resources_df = list_resources(package)
    raw_dir = raw_dir or resolve_raw_dir()

    results: list[ProcessResult] = []
    for resource_key, config in resources_config.items():
        try:
            result = process_resource(
                resource_key,
                config,
                resources_df,
                raw_dir=raw_dir,
                verbose=verbose,
            )
            results.append(result)
        except Exception as exc:
            if verbose:
                print(f"[ERROR] {resource_key}: {exc}")
            results.append(
                ProcessResult(
                    resource_key=resource_key,
                    config=config,
                    df_model=pd.DataFrame(),
                    df_model_clean=pd.DataFrame(),
                    df_model_clean_valid=pd.DataFrame(),
                    period_info={},
                    validation={
                        "resource_key": resource_key,
                        "producto": config["producto"],
                        "agrupador_tipo": config["agrupador_tipo"],
                        "cantidad_filas_original": 0,
                        "cantidad_filas_limpias": 0,
                        "periodo_min": None,
                        "periodo_max_disponible": None,
                        "latest_valid_period": None,
                        "excluded_period": "",
                        "cantidad_agrupadores": 0,
                        "cantidad_nulos_produccion": 0,
                        "cantidad_produccion_negativa": 0,
                        "observaciones": f"error: {exc}",
                    },
                    raw_path=raw_dir / get_resource_local_filename(resource_key),
                    export_path=PROCESSED_DIR / f"{resource_key}_model_clean.csv",
                    notes=[str(exc)],
                )
            )

    valid_frames = [
        prepare_export_df(r.df_model_clean_valid)
        for r in results
        if len(r.df_model_clean_valid) > 0
    ]
    df_unified = (
        pd.concat(valid_frames, ignore_index=True) if valid_frames else pd.DataFrame()
    )

    df_validaciones = pd.DataFrame([r.validation for r in results])
    return results, df_unified, df_validaciones


def validate_unified_dataset(df: pd.DataFrame) -> dict[str, Any]:
    """
    Ejecuta validaciones de consistencia sobre el dataset unificado.

    Parameters
    ----------
    df : pd.DataFrame
        Dataset concatenado (salida de ``process_all_resources``).

    Returns
    -------
    dict
        Totales, cobertura por producto/tipo, rango temporal, duplicados y
        muestra de filas duplicadas.

    Notes
    -----
    Los duplicados se detectan sobre ``DUPLICATE_KEY_COLUMNS``. Usado en
    notebook 02; no forma parte del CLI ``run_mvp_processing.py``.
    """
    dup_mask = df.duplicated(subset=DUPLICATE_KEY_COLUMNS, keep=False)
    duplicates = df[dup_mask]

    return {
        "total_filas": len(df),
        "productos": sorted(df["producto"].dropna().unique().tolist()),
        "agrupador_tipos": sorted(df["agrupador_tipo"].dropna().unique().tolist()),
        "periodo_min": df["periodo_str"].min() if len(df) else None,
        "periodo_max": df["periodo_str"].max() if len(df) else None,
        "registros_por_producto_tipo": (
            df.groupby(["producto", "agrupador_tipo"]).size().to_dict()
        ),
        "agrupadores_unicos_por_producto_tipo": (
            df.groupby(["producto", "agrupador_tipo"])["agrupador_nombre"]
            .nunique()
            .to_dict()
        ),
        "duplicados_cantidad": int(dup_mask.sum()),
        "duplicados_muestra": duplicates.head(20),
    }


def export_unified(
    df_unified: pd.DataFrame,
    df_validaciones: pd.DataFrame,
    processed_dir: Path | None = None,
) -> tuple[Path, Path]:
    """
    Exporta el dataset unificado y el resumen de validaciones por recurso.

    Parameters
    ----------
    df_unified : pd.DataFrame
        Concatenación de todos los recursos MVP válidos.
    df_validaciones : pd.DataFrame
        Una fila por recurso (salida de ``validate_resource_basic``).
    processed_dir : Path, optional
        Directorio de salida.

    Returns
    -------
    tuple[Path, Path]
        Rutas a ``sesco_produccion_model_clean.csv`` y
        ``sesco_validaciones_resumen.csv``.
    """
    processed_dir = processed_dir or PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    unified_path = processed_dir / "sesco_produccion_model_clean.csv"
    valid_path = processed_dir / "sesco_validaciones_resumen.csv"

    exportable = df_unified.copy()
    if "periodo_dt" in exportable.columns and pd.api.types.is_datetime64_any_dtype(
        exportable["periodo_dt"]
    ):
        exportable = exportable.copy()
        exportable["periodo_dt"] = exportable["periodo_dt"].dt.strftime("%Y-%m-%d")

    exportable.to_csv(unified_path, encoding="utf-8-sig", index=False)
    df_validaciones.to_csv(valid_path, encoding="utf-8-sig", index=False)
    return unified_path, valid_path


def export_dashboard_auxiliaries(
    df_unified: pd.DataFrame,
    processed_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    """
    Exporta auxiliares del dashboard (misma lógica que notebook 03).

    Parameters
    ----------
    df_unified : pd.DataFrame
        Dataset unificado ya validado por recurso.
    processed_dir : Path, optional
        Directorio de salida.

    Returns
    -------
    tuple[Path, Path, Path]
        Rutas a ``sesco_latest_periods_by_view.csv``,
        ``sesco_dashboard_config.csv`` y ``sesco_totales_por_vista_resumen.csv``.
    """
    processed_dir = processed_dir or PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)

    periodos_por_vista = (
        df_unified.groupby(["producto", "agrupador_tipo"], as_index=False)
        .agg(
            periodo_min=("periodo_str", "min"),
            periodo_max=("periodo_str", "max"),
            cantidad_periodos=("periodo_str", "nunique"),
            cantidad_filas=("periodo_str", "size"),
            cantidad_agrupadores=("agrupador_nombre", "nunique"),
        )
        .sort_values(["producto", "agrupador_tipo"])
        .reset_index(drop=True)
    )

    latest_path = processed_dir / "sesco_latest_periods_by_view.csv"
    (
        periodos_por_vista[["producto", "agrupador_tipo", "periodo_max"]]
        .rename(columns={"periodo_max": "latest_valid_period"})
        .to_csv(latest_path, encoding="utf-8-sig", index=False)
    )

    config_path = processed_dir / "sesco_dashboard_config.csv"
    (
        periodos_por_vista[
            [
                "producto",
                "agrupador_tipo",
                "periodo_min",
                "periodo_max",
                "cantidad_agrupadores",
            ]
        ]
        .rename(columns={"periodo_max": "latest_valid_period"})
        .sort_values(["producto", "agrupador_tipo"])
        .reset_index(drop=True)
        .to_csv(config_path, encoding="utf-8-sig", index=False)
    )

    totales_path = processed_dir / "sesco_totales_por_vista_resumen.csv"
    build_totals_comparison_table(df_unified).to_csv(
        totales_path, encoding="utf-8-sig", index=False
    )

    return latest_path, config_path, totales_path


# ---------------------------------------------------------------------------
# Backward-compatible aliases (Spanish names). Remove after notebooks migrate.
# ---------------------------------------------------------------------------
detectar_periodo_incompleto = detect_incomplete_period
infer_tipo_recurso = infer_resource_type
export_periodos_resumen = export_period_summary
