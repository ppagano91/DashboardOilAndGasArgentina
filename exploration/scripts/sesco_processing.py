"""
Pipeline ETL exploratorio para datos de producción SESCO (datos.gob.ar).

Concentra la lógica reutilizable extraída de ``01_exploracion_sesco.ipynb`` y
generalizada en ``02_modelo_unificado_sesco.ipynb``. Procesa los 6 recursos MVP
(provincia / cuenca / empresa × petróleo / gas): consulta CKAN, descarga CSV,
normaliza columnas heterogéneas, construye un modelo analítico común, detecta
períodos incompletos y exporta archivos en ``exploration/data/processed/``.

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
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

# Identificador oficial del dataset en datos.gob.ar (único punto de verdad CKAN).
PACKAGE_ID = "energia-produccion-petroleo-gas-sesco"
CKAN_URL = f"https://datos.gob.ar/api/3/action/package_show?id={PACKAGE_ID}"
USER_AGENT = "OilGas-Exploration/1.0"

# Rutas relativas al módulo: independientes del directorio de trabajo al ejecutar CLI.
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
    Resultado completo del pipeline para un recurso MVP individual.

    Agrupa DataFrames intermedios, metadatos de períodos y rutas de exportación
    para inspección en notebooks o auditoría sin re-ejecutar transformaciones.

    Attributes
    ----------
    resource_key : str
        Clave interna (ej. ``petroleo_provincia``).
    config : dict
        Configuración del recurso (producto, agrupador_tipo, nombre_recurso).
    df_model : pd.DataFrame
        Modelo analítico antes del preprocesamiento final.
    df_model_clean : pd.DataFrame
        Tras ``preprocess_model_df``; puede incluir último período incompleto.
    df_model_clean_valid : pd.DataFrame
        Períodos válidos tras aplicar la regla del 50 %; base de los exports.
    periodo_info : dict
        Salida de ``detectar_periodo_incompleto``.
    validation : dict
        Fila de resumen para ``sesco_validaciones_resumen.csv``.
    raw_path : Path
        CSV descargado en ``data/raw/``.
    export_path : Path
        CSV limpio ``{resource_key}_model_clean.csv``.
    observaciones : list[str]
        Notas de descarga, exclusión de período o errores.
    """

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
    nombre_recurso: str,
    *,
    fallback_keywords: list[str] | None = None,
) -> pd.Series | None:
    """
    Busca un recurso CSV por nombre exacto (normalizado) o por palabras clave.

    Parameters
    ----------
    resources_df : pd.DataFrame
        Tabla de recursos CKAN (salida de ``list_resources``).
    nombre_recurso : str
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
    """
    Deriva el nombre de archivo local en ``data/raw/``.

    Parameters
    ----------
    resource_key : str
        Clave interna del recurso (ej. ``petroleo_provincia``).
    resource_name : str
        Nombre CKAN (reservado para futuras reglas; hoy solo afecta legacy).
    url : str
        URL de descarga; se usa el último segmento del path si termina en ``.csv``.

    Returns
    -------
    str
        Nombre de archivo para persistir el CSV raw.
    """
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
    """
    Descarga un CSV remoto si no existe ya en disco local.

    Parameters
    ----------
    url : str
        URL directa del recurso.
    destination : Path
        Ruta de destino (típicamente bajo ``RAW_DIR``).
    timeout : int
        Segundos de espera HTTP.

    Returns
    -------
    Path
        Ruta al archivo local (existente o recién descargado).

    Notes
    -----
    Si el archivo ya existe no se sobrescribe; para actualizar hay que borrarlo
    manualmente o usar ``download_sesco_resource.py`` con otro flujo.
    """
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


def find_production_column(df_norm: pd.DataFrame, producto: str) -> str | None:
    """
    Detecta la columna de producción según el producto (petróleo o gas).

    Parameters
    ----------
    df_norm : pd.DataFrame
        DataFrame con columnas normalizadas.
    producto : str
        ``"petroleo"`` o ``"gas"``.

    Returns
    -------
    str or None
        Nombre de columna detectada, o ``None`` si no hay candidato confiable.
    """
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
    """
    Detecta la columna del agrupador (provincia, cuenca o empresa).

    Parameters
    ----------
    df_norm : pd.DataFrame
        DataFrame con columnas normalizadas.
    agrupador_tipo : str
        Tipo de vista: ``"provincia"``, ``"cuenca"`` o ``"empresa"``.

    Returns
    -------
    str or None
        Nombre de columna que contiene el agrupador.
    """
    for col in df_norm.columns:
        if agrupador_tipo in col:
            return col
    cat_cols = detect_categorical_columns(df_norm)
    return cat_cols[0] if cat_cols else None


def infer_tipo_recurso(name: str) -> str | None:
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
    producto: str,
    agrupador_tipo: str,
    source_resource: str,
    tipo_recurso: str | None = None,
) -> pd.DataFrame:
    """
    Construye el DataFrame con esquema analítico común a los 6 recursos MVP.

    Parameters
    ----------
    df_norm : pd.DataFrame
        CSV normalizado (columnas en snake_case).
    producto : str
        ``"petroleo"`` o ``"gas"``.
    agrupador_tipo : str
        ``"provincia"``, ``"cuenca"`` o ``"empresa"``.
    source_resource : str
        Nombre oficial del recurso CKAN (trazabilidad).
    tipo_recurso : str, optional
        Clasificación de la serie; si es ``None``, se infiere del nombre.

    Returns
    -------
    pd.DataFrame
        Modelo intermedio con columnas semánticas unificadas.

    Notes
    -----
    Provincia, cuenca y empresa son vistas alternativas del mismo fenómeno;
    cada llamada corresponde a un único CSV fuente y un único ``agrupador_tipo``.
    """
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
    ``detectar_periodo_incompleto``.
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


def detectar_periodo_incompleto(
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
        ``periodos_validos`` (lista de ``periodo_dt`` aceptados).

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
    periodos_validos = agg["periodo_dt"].tolist()

    if len(agg) >= 2:
        ultimo_valor = agg.iloc[-1]["produccion"]
        penultimo_valor = agg.iloc[-2]["produccion"]
        ratio = ultimo_valor / penultimo_valor if penultimo_valor else 1.0

        # Caída abrupta: típico de mes en curso con pocos días reportados.
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
    """
    Filtra filas a los períodos considerados válidos tras la regla del 50 %.

    Parameters
    ----------
    df_model_clean : pd.DataFrame
        Datos limpios que pueden incluir el último período incompleto.
    periodo_info : dict
        Salida de ``detectar_periodo_incompleto`` (clave ``periodos_validos``).

    Returns
    -------
    pd.DataFrame
        Subconjunto exportable y apto para análisis/KPIs.
    """
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
    """
    Genera una fila de resumen de validación para un recurso procesado.

    Parameters
    ----------
    resource_key : str
        Clave interna del recurso.
    config : dict
        Configuración MVP del recurso.
    df_model : pd.DataFrame
        Modelo original (antes de filtrar períodos incompletos).
    df_model_clean_valid : pd.DataFrame
        Datos exportables.
    periodo_info : dict
        Metadatos de períodos válidos.
    observaciones : list[str], optional
        Notas acumuladas (descarga, exclusión de período, etc.).

    Returns
    -------
    dict
        Métricas para una fila de ``sesco_validaciones_resumen.csv``.
    """
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


def export_periodos_resumen(
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
    """Formatea número con coma decimal (presentación en notebooks)."""
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
    Calcula períodos válidos para una vista del dataset unificado.

    Aplica la misma regla del 50 % que ``detectar_periodo_incompleto``, pero
    sobre el subconjunto ``producto`` + ``agrupador_tipo`` del unificado.

    Parameters
    ----------
    df : pd.DataFrame
        Dataset unificado (``sesco_produccion_model_clean``).
    producto : str
        ``"petroleo"`` o ``"gas"``.
    agrupador_tipo : str
        ``"provincia"``, ``"cuenca"`` o ``"empresa"``.
    threshold : float
        Umbral último/penúltimo (default 0.5).
    verbose : bool
        Imprime advertencia si se excluye un período.

    Returns
    -------
    dict
        Metadatos de períodos, incluyendo ``producto`` y ``agrupador_tipo``.

    Notes
    -----
    Evita usar un único corte global cuando cada vista puede tener distinto
    último mes publicado por SESCO. Usado en notebook 02; el dashboard consume
    ``sesco_latest_periods_by_view.csv`` generado en notebook 03.
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
        # Misma regla del 50 % que detectar_periodo_incompleto, por vista del unificado.
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
        Umbral para ``detectar_periodo_incompleto``.
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
    # Regla del 50 %: puede marcar el último mes como incompleto a nivel recurso.
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
    Procesa todos los recursos MVP y arma el dataset unificado.

    Parameters
    ----------
    resources_config : dict
        Mapa ``resource_key`` → config (como ``SESCO_RESOURCES_MVP``).
    package : dict, optional
        Paquete CKAN precargado.
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

    results: list[ProcessResult] = []
    i=0
    for resource_key, config in resources_config.items():
        i+=1
        try:
            result = process_resource(
                resource_key,
                config,
                resources_df,
                verbose=verbose,
            )
            print(i)
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
    print(valid_frames)
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
