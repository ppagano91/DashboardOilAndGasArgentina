"""
Procesamiento reutilizable de recursos SESCO (CKAN → modelo analítico → CSV limpio).

Extraído de exploration/notebooks/01_exploracion_sesco.ipynb para los 6 recursos MVP.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

PACKAGE_ID = "energia-produccion-petroleo-gas-sesco"
CKAN_URL = f"https://datos.gob.ar/api/3/action/package_show?id={PACKAGE_ID}"
USER_AGENT = "OilGas-Exploration/1.0"

SCRIPT_DIR = Path(__file__).resolve().parent
EXPLORATION_DIR = SCRIPT_DIR.parent
RAW_DIR = EXPLORATION_DIR / "data" / "raw"
PROCESSED_DIR = EXPLORATION_DIR / "data" / "processed"

RESOURCE_FIELDS = [
    "name",
    "id",
    "format",
    "url",
    "created",
    "last_modified",
    "size",
]

REQUIRED_MODEL_COLUMNS = [
    "periodo",
    "produccion",
    "agrupador_nombre",
    "producto",
    "agrupador_tipo",
]

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

PERIOD_COLUMN_PRIORITY = (
    "indice_tiempo",
    "periodo",
    "fecha",
    "anio_mes",
)

DUPLICATE_KEY_COLUMNS = [
    "periodo_str",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
]


@dataclass
class ProcessResult:
    """Resultado del pipeline para un recurso MVP."""

    resource_key: str
    config: dict[str, Any]
    df_model: pd.DataFrame
    df_model_clean: pd.DataFrame
    df_model_clean_valid: pd.DataFrame
    periodo_info: dict[str, Any]
    validation: dict[str, Any]
    raw_path: Path
    export_path: Path
    observaciones: list[str] = field(default_factory=list)


def normalize_text(value: str) -> str:
    """Minúsculas sin acentos para comparar nombres de recursos/columnas."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def fetch_ckan_package(timeout: int = 60, retries: int = 3, backoff_s: float = 2.0) -> dict:
    """Consulta metadata del dataset SESCO en CKAN (con reintentos simples)."""
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
    """Lista recursos del paquete como DataFrame."""
    if package is None:
        package = fetch_ckan_package()
    rows = [
        {field: resource.get(field) for field in RESOURCE_FIELDS}
        for resource in package.get("resources", [])
    ]
    return pd.DataFrame(rows)


def find_resource_by_name(
    resources_df: pd.DataFrame,
    nombre_recurso: str,
    *,
    fallback_keywords: list[str] | None = None,
) -> pd.Series | None:
    """
    Busca un recurso CSV por nombre exacto (normalizado) o por palabras clave.

    Prioriza CSV, recursos con 'promedio' en el nombre y last_modified más reciente.
    """
    target = normalize_text(nombre_recurso)
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


def infer_raw_filename(resource_key: str, resource_name: str, url: str) -> str:
    """Nombre de archivo local en data/raw/."""
    legacy = {
        "petroleo_provincia": "produccion_petroleo_promedio_diaria_por_provincia.csv",
    }
    if resource_key in legacy:
        return legacy[resource_key]
    parsed = Path(unquote(urlparse(url).path)).name
    if parsed.lower().endswith(".csv"):
        return parsed
    return f"{resource_key}.csv"


def download_csv(url: str, destination: Path, timeout: int = 120) -> Path:
    """Descarga un CSV remoto si no existe localmente."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
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
    """Lee CSV SESCO con encoding habitual."""
    return pd.read_csv(path, encoding="utf-8-sig")


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza nombres de columnas a snake_case sin acentos."""

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
    """Columnas relacionadas con período/fecha."""
    tokens = ("anio", "ano", "mes", "fecha", "periodo", "indice", "tiempo")
    return [col for col in columns if any(token in col for token in tokens)]


def detect_numeric_columns(df: pd.DataFrame) -> list[str]:
    return df.select_dtypes(include="number").columns.tolist()


def detect_categorical_columns(df: pd.DataFrame) -> list[str]:
    numeric = set(detect_numeric_columns(df))
    date_like = set(detect_date_columns(df.columns))
    return [col for col in df.columns if col not in numeric and col not in date_like]


def detect_period_column(columns: list[str] | pd.Index) -> str | None:
    """
    Columna principal de período.

    Orden documentado: indice_tiempo → periodo → fecha.
    Si no hay columna única, se usa anio + mes en build_model_df.
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


def find_production_column(df_norm: pd.DataFrame, producto: str) -> str | None:
    """Detecta columna de producción (petróleo o gas)."""
    columns = list(df_norm.columns)
    product_tokens = ("petroleo", "petr") if producto == "petroleo" else ("gas",)

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


def find_group_column(df_norm: pd.DataFrame, agrupador_tipo: str) -> str | None:
    """Detecta columna del agrupador (provincia, cuenca, empresa)."""
    for col in df_norm.columns:
        if agrupador_tipo in col:
            return col
    cat_cols = detect_categorical_columns(df_norm)
    return cat_cols[0] if cat_cols else None


def infer_tipo_recurso(name: str) -> str | None:
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
    producto: str,
    agrupador_tipo: str,
    source_resource: str,
    tipo_recurso: str | None = None,
) -> pd.DataFrame:
    """Construye df_model con esquema analítico común."""
    if tipo_recurso is None:
        tipo_recurso = infer_tipo_recurso(source_resource) or "promedio_diario"

    prod_col = find_production_column(df_norm, producto)
    group_col = find_group_column(df_norm, agrupador_tipo)
    period_col = detect_period_column(df_norm.columns)

    if prod_col is None:
        raise ValueError(
            f"No se detectó columna de producción para {producto} en {list(df_norm.columns)}"
        )
    if group_col is None:
        raise ValueError(
            f"No se detectó columna de agrupador '{agrupador_tipo}' en {list(df_norm.columns)}"
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

    df_model["producto"] = producto
    df_model["agrupador_tipo"] = agrupador_tipo
    df_model["agrupador_nombre"] = df_norm[group_col]
    df_model["tipo_recurso"] = tipo_recurso
    df_model["produccion"] = df_norm[prod_col]
    df_model["source_resource"] = source_resource

    return df_model


def preprocess_model_df(df_model: pd.DataFrame) -> pd.DataFrame:
    """Normaliza periodo, producción y elimina filas sin datos clave."""
    df = df_model.copy()

    missing = [c for c in REQUIRED_MODEL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas requeridas: {missing}")

    df["periodo_original"] = df["periodo"]

    periodo_raw = df["periodo"].astype(str).str.strip()
    valid_mask = periodo_raw.str.match(r"^\d{4}-\d{1,2}$", na=False)
    periodo_str = periodo_raw.where(valid_mask)

    if "anio" in df.columns and "mes" in df.columns:
        anio_num = pd.to_numeric(df["anio"], errors="coerce")
        mes_num = pd.to_numeric(df["mes"], errors="coerce")
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


def detectar_periodo_incompleto(
    df_model_clean: pd.DataFrame,
    threshold: float = 0.5,
    *,
    verbose: bool = True,
) -> dict[str, Any]:
    """Excluye el último período si su total es < threshold vs. el anterior."""
    agg = (
        df_model_clean.groupby(["periodo_dt", "periodo_str"], as_index=False)["produccion"]
        .sum()
        .sort_values("periodo_dt")
    )

    latest_period = agg.iloc[-1]["periodo_str"]
    latest_valid_period = latest_period
    excluded_period = None
    ratio = None
    periodos_validos = agg["periodo_dt"].tolist()

    if len(agg) >= 2:
        ultimo_valor = agg.iloc[-1]["produccion"]
        penultimo_valor = agg.iloc[-2]["produccion"]
        ratio = ultimo_valor / penultimo_valor if penultimo_valor else 1.0

        if ratio < threshold:
            excluded_period = latest_period
            latest_valid_period = agg.iloc[-2]["periodo_str"]
            periodos_validos = agg.iloc[:-1]["periodo_dt"].tolist()
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
        "periodos_validos": periodos_validos,
    }


def apply_valid_periods(
    df_model_clean: pd.DataFrame, periodo_info: dict[str, Any]
) -> pd.DataFrame:
    """Filtra períodos válidos (sin el último incompleto si corresponde)."""
    return df_model_clean[
        df_model_clean["periodo_dt"].isin(periodo_info["periodos_validos"])
    ].copy()


def validate_resource_basic(
    resource_key: str,
    config: dict[str, Any],
    df_model: pd.DataFrame,
    df_model_clean_valid: pd.DataFrame,
    periodo_info: dict[str, Any],
    observaciones: list[str] | None = None,
) -> dict[str, Any]:
    """Resumen de validación por recurso para sesco_validaciones_resumen.csv."""
    obs = "; ".join(observaciones) if observaciones else ""

    return {
        "resource_key": resource_key,
        "producto": config["producto"],
        "agrupador_tipo": config["agrupador_tipo"],
        "cantidad_filas_original": len(df_model),
        "cantidad_filas_limpias": len(df_model_clean_valid),
        "periodo_min": df_model_clean_valid["periodo_str"].min()
        if len(df_model_clean_valid)
        else None,
        "periodo_max_disponible": periodo_info.get("latest_period"),
        "latest_valid_period": periodo_info.get("latest_valid_period"),
        "excluded_period": periodo_info.get("excluded_period") or "",
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
    """Selecciona columnas finales y formatea periodo_dt para CSV."""
    export = df[EXPORT_COLUMNS].copy()
    if pd.api.types.is_datetime64_any_dtype(export["periodo_dt"]):
        export["periodo_dt"] = export["periodo_dt"].dt.strftime("%Y-%m-%d")
    return export


def export_model_clean(
    df_model_clean_valid: pd.DataFrame,
    resource_key: str,
    processed_dir: Path | None = None,
) -> Path:
    """Exporta CSV limpio individual del recurso."""
    processed_dir = processed_dir or PROCESSED_DIR
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{resource_key}_model_clean.csv"
    prepare_export_df(df_model_clean_valid).to_csv(
        path, encoding="utf-8-sig", index=False
    )
    return path


def export_periodos_resumen(
    df_model_clean_valid: pd.DataFrame,
    resource_key: str,
    processed_dir: Path | None = None,
) -> Path:
    """Resumen de producción total y agrupadores por período."""
    processed_dir = processed_dir or PROCESSED_DIR
    resumen = (
        df_model_clean_valid.groupby("periodo_str", as_index=False)
        .agg(
            produccion_total=("produccion", "sum"),
            cantidad_agrupadores=("agrupador_nombre", "nunique"),
        )
        .sort_values("periodo_str")
    )
    path = processed_dir / f"{resource_key}_periodos_resumen.csv"
    resumen.to_csv(path, encoding="utf-8-sig", index=False)
    return path


def format_number(value: float, decimals: int = 0) -> str:
    """Formato local: coma decimal, sin separador de miles."""
    return f"{value:.{decimals}f}".replace(".", ",")


def get_valid_periods_by_group(
    df: pd.DataFrame,
    producto: str,
    agrupador_tipo: str,
    threshold: float = 0.5,
    *,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Detecta períodos válidos por combinación producto + agrupador_tipo.

    Evita usar un único corte global cuando cada recurso puede tener
    distinto último período cargado.
    """
    sub = df[
        (df["producto"] == producto) & (df["agrupador_tipo"] == agrupador_tipo)
    ].copy()
    if sub.empty:
        return {
            "producto": producto,
            "agrupador_tipo": agrupador_tipo,
            "latest_period": None,
            "latest_valid_period": None,
            "excluded_period": None,
            "ratio": None,
            "periodos_validos": [],
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
    periodos_validos = agg["periodo_dt"].tolist()

    if len(agg) >= 2:
        ultimo_valor = agg.iloc[-1]["produccion"]
        penultimo_valor = agg.iloc[-2]["produccion"]
        ratio = ultimo_valor / penultimo_valor if penultimo_valor else 1.0
        if ratio < threshold:
            excluded_period = latest_period
            latest_valid_period = agg.iloc[-2]["periodo_str"]
            periodos_validos = agg.iloc[:-1]["periodo_dt"].tolist()
            if verbose:
                print(
                    f"[{producto}/{agrupador_tipo}] se excluye {excluded_period} "
                    f"({ratio * 100:.1f}% vs período anterior)"
                )

    return {
        "producto": producto,
        "agrupador_tipo": agrupador_tipo,
        "latest_period": latest_period,
        "latest_valid_period": latest_valid_period,
        "excluded_period": excluded_period,
        "ratio": ratio,
        "periodos_validos": periodos_validos,
    }


def build_totals_comparison_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compara magnitudes por período y producto entre provincia, cuenca y empresa.

    No suma vistas entre sí; sólo permite controlar diferencias de cobertura.
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
    Pipeline completo para un recurso MVP:
    CKAN → raw CSV → df_model → limpio → export.
    """
    raw_dir = raw_dir or RAW_DIR
    processed_dir = processed_dir or PROCESSED_DIR
    observaciones: list[str] = []

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
    raw_name = infer_raw_filename(resource_key, source_name, url)
    raw_path = raw_dir / raw_name

    if download_if_missing and not raw_path.exists():
        download_csv(url, raw_path)
        observaciones.append("descargado desde CKAN")
    elif not raw_path.exists():
        raise FileNotFoundError(f"CSV no encontrado: {raw_path}")

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
        producto=config["producto"],
        agrupador_tipo=config["agrupador_tipo"],
        source_resource=source_name,
    )

    df_model_clean = preprocess_model_df(df_model)
    periodo_info = detectar_periodo_incompleto(
        df_model_clean, threshold=incomplete_threshold, verbose=verbose
    )
    df_valid = apply_valid_periods(df_model_clean, periodo_info)

    if periodo_info.get("excluded_period"):
        observaciones.append(
            f"excluido periodo {periodo_info['excluded_period']}"
        )

    export_path = export_model_clean(df_valid, resource_key, processed_dir)
    export_periodos_resumen(df_valid, resource_key, processed_dir)

    validation = validate_resource_basic(
        resource_key,
        config,
        df_model,
        df_valid,
        periodo_info,
        observaciones=observaciones,
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
        periodo_info=periodo_info,
        validation=validation,
        raw_path=raw_path,
        export_path=export_path,
        observaciones=observaciones,
    )


def process_all_resources(
    resources_config: dict[str, dict[str, Any]],
    *,
    package: dict | None = None,
    verbose: bool = True,
) -> tuple[list[ProcessResult], pd.DataFrame, pd.DataFrame]:
    """
    Procesa todos los recursos MVP y devuelve resultados + validaciones + unificado.
    """
    if package is None:
        package = fetch_ckan_package()
    resources_df = list_resources(package)

    results: list[ProcessResult] = []
    for resource_key, config in resources_config.items():
        try:
            result = process_resource(
                resource_key,
                config,
                resources_df,
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
                    periodo_info={},
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
                    raw_path=RAW_DIR / f"{resource_key}.csv",
                    export_path=PROCESSED_DIR / f"{resource_key}_model_clean.csv",
                    observaciones=[str(exc)],
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
    """Validaciones del dataset unificado."""
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
    """Exporta dataset unificado y resumen de validaciones."""
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
