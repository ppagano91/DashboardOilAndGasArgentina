# Documentación técnica — Scripts de `exploration/`

**Proyecto:** Monitor de Producción Hidrocarburífera Argentina — SESCO  
**Ámbito:** `exploration/scripts/` y su relación con notebooks, datos procesados y dashboard Streamlit  
**Última revisión:** junio 2026 (alineada al código actual del repositorio)

---

## 1. Introducción

La carpeta `exploration/scripts/` concentra la **primera versión scriptable** del pipeline exploratorio del MVP. Su propósito es automatizar, fuera de Jupyter, las tareas repetibles de:

1. Consultar el catálogo CKAN de datos.gob.ar.
2. Descargar CSV oficiales de producción SESCO.
3. Normalizar columnas heterogéneas hacia un **modelo analítico común**.
4. Limpiar y validar registros.
5. Detectar períodos incompletos en el último mes cargado.
6. Exportar CSV procesados listos para análisis, validación y visualización.

Estos scripts forman una **pipeline ETL exploratoria**: no reemplazan aún una arquitectura de producción con PostgreSQL/PostGIS, FastAPI y frontend definitivo, sino que **preceden** a esa etapa. El objetivo inmediato es generar archivos en `exploration/data/processed/` que alimenten notebooks de validación y el dashboard Streamlit (`exploration/streamlit_app/app.py`).

### Scripts presentes y esperados

| Archivo | Estado en el repo |
|---------|-------------------|
| `sesco_processing.py` | Presente — módulo central |
| `run_mvp_processing.py` | Presente — orquestador CLI |
| `inspect_ckan_resources.py` | Presente — inspección CKAN |
| `download_sesco_resource.py` | Presente — descarga puntual |
| `__init__.py` | **No presente actualmente** — ver sección 4 |

---

## 2. Visión general del flujo

```
API CKAN (datos.gob.ar)
        │
        ▼
Detección de recursos oficiales del dataset energia-produccion-petroleo-gas-sesco
        │
        ▼
Descarga de CSV → exploration/data/raw/
        │
        ▼
Lectura con pandas (encoding utf-8-sig)
        │
        ▼
Normalización de nombres de columnas (snake_case, sin acentos)
        │
        ▼
Detección heurística de columnas: período, producción, agrupador
        │
        ▼
Construcción del modelo común (build_model_df)
        │
        ▼
Preprocesamiento: periodo_dt, tipos numéricos, drop de filas inválidas
        │
        ▼
Detección de último período incompleto (regla del 50 %)
        │
        ▼
Filtrado a períodos válidos (apply_valid_periods)
        │
        ▼
Validaciones por recurso y del dataset unificado
        │
        ▼
Exportación de CSV limpios individuales + resúmenes por período
        │
        ▼
Concatenación → sesco_produccion_model_clean.csv
        │
        ▼
Notebook 03 → archivos auxiliares para dashboard
        │
        ▼
Dashboard Streamlit (visualización)
```

**Nota:** `run_mvp_processing.py` cubre el tramo desde CKAN hasta el unificado y `sesco_validaciones_resumen.csv`. Los archivos `sesco_latest_periods_by_view.csv`, `sesco_dashboard_config.csv` y `sesco_totales_por_vista_resumen.csv` se generan en la notebook `03_validacion_final_sesco.ipynb`, no en el script CLI.

---

## 3. Mapa de scripts

| Script | Responsabilidad principal | Cuándo se usa | Entradas principales | Salidas principales | Relación con notebooks / dashboard |
|--------|---------------------------|---------------|----------------------|---------------------|-----------------------------------|
| `__init__.py` | Marcador de paquete Python (esperado, ausente) | Al importar `exploration.scripts` como paquete | — | — | Notebooks agregan `scripts/` a `sys.path` en lugar de importar paquete |
| `inspect_ckan_resources.py` | Listar metadata CKAN del dataset SESCO | Antes de procesar; al cambiar recursos en datos.gob.ar | API CKAN `package_show` | Consola + `ckan_resources.csv` | Complementa exploración inicial de `01_exploracion_sesco.ipynb` |
| `download_sesco_resource.py` | Descarga puntual por URL | Pruebas manuales, recursos no incluidos en MVP | URL + nombre opcional | CSV en `data/raw/` | Auxiliar; no forma parte del pipeline automático |
| `sesco_processing.py` | Lógica ETL reutilizable | Importado por CLI y notebooks | CSV raw, config de recursos, CKAN | DataFrames + CSV en `data/processed/` | Extraído de notebook 01; usado en notebook 02 |
| `run_mvp_processing.py` | Ejecutar los 6 recursos MVP por consola | Regenerar dataset sin abrir Jupyter | `SESCO_RESOURCES_MVP` + CKAN | Unificado + validaciones por recurso | Alternativa scriptable a `02_modelo_unificado_sesco.ipynb` (parcial) |

---

## 4. `__init__.py`

**Estado actual:** el archivo **no existe** en `exploration/scripts/`.

**Función esperada:** convertir el directorio en un **paquete Python importable** (`exploration.scripts`), permitiendo imports del estilo:

```python
from exploration.scripts import sesco_processing
```

**Por qué importa:** sin `__init__.py`, los notebooks y scripts usan la convención actual de insertar el directorio en `sys.path`:

```python
SCRIPTS_DIR = EXPLORATION_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
import sesco_processing as sp
```

**Contenido esperado:** vacío o mínimo; **no debe contener lógica de negocio**.

---

## 5. `inspect_ckan_resources.py`

### Problema que resuelve

Los recursos SESCO en datos.gob.ar pueden cambiar de URL, nombre o fecha de modificación. Depender de URLs fijas en código es frágil. Este script **consulta el catálogo en vivo** y deja un inventario local actualizado.

### Funcionamiento

1. Llama a `https://datos.gob.ar/api/3/action/package_show?id=energia-produccion-petroleo-gas-sesco`.
2. Valida `success=True` en la respuesta JSON.
3. Extrae de cada recurso: `name`, `id`, `format`, `url`, `created`, `last_modified`, `size`.
4. Imprime la lista ordenada por nombre en consola.
5. Guarda el mismo contenido en CSV.

### Dataset CKAN

- **ID:** `energia-produccion-petroleo-gas-sesco`
- **Título habitual:** producción de petróleo y gas — SESCO

### Archivo generado

| Ruta | Descripción |
|------|-------------|
| `exploration/data/processed/ckan_resources.csv` | Inventario de recursos con metadata CKAN |

### Cuándo ejecutarlo

- Primera vez que se trabaja con el proyecto.
- Cuando falla `find_resource_by_name` en el procesamiento (recurso renombrado o nuevo).
- Periódicamente, para auditar qué publicó SESCO.

### Relación con el flujo completo

No descarga ni transforma datos de producción. Es **exploratorio y de inventario**. `sesco_processing.py` tiene su propia función `fetch_ckan_package()` para el pipeline; este script es la versión CLI legible para humanos.

---

## 6. `download_sesco_resource.py`

### Qué hace

Descarga **un único archivo** desde una URL directa (típicamente un CSV del dataset SESCO) hacia `exploration/data/raw/`.

### Cuándo se usa

- Prueba rápida de conectividad o formato de un recurso nuevo.
- Descarga manual de un CSV que aún no está en la configuración MVP.
- Depuración cuando se conoce la URL pero no se quiere correr todo el pipeline.

### Argumentos

```text
python exploration/scripts/download_sesco_resource.py <url> [nombre_archivo.csv]
```

| Argumento | Obligatorio | Descripción |
|-----------|-------------|-------------|
| `url` | Sí | URL directa del recurso |
| `filename` | No | Nombre local; si se omite, se infiere del path de la URL |

### Destino

`exploration/data/raw/<nombre_archivo>`

### Limitaciones

- **No** normaliza columnas ni construye el modelo común.
- **No** integra con `SESCO_RESOURCES_MVP`.
- **Sobrescribe** si se vuelve a descargar al mismo path (a diferencia de `download_csv` en `sesco_processing.py`, que omite si el archivo ya existe).
- No valida que el archivo pertenezca al dataset oficial.

---

## 7. `run_mvp_processing.py`

### Rol

Punto de entrada **por línea de comandos** para procesar los **6 recursos MVP** definidos en `SESCO_RESOURCES_MVP` y exportar el dataset unificado.

### Relación con notebooks

Equivalente scriptable a la parte principal de `02_modelo_unificado_sesco.ipynb` que invoca `sp.process_all_resources()` y `sp.export_unified()`.

**No incluye** (hoy) las exportaciones de la notebook 03 ni las visualizaciones de validación de la notebook 02:

- `get_valid_periods_by_group` sobre el unificado (notebook 02).
- `validate_unified_dataset` impreso en notebook 02.
- `build_totals_comparison_table` → en notebook 02 vía módulo; en notebook 03 hay lógica similar inline.
- `sesco_latest_periods_by_view.csv`, `sesco_dashboard_config.csv`, `sesco_totales_por_vista_resumen.csv` (notebook 03).

### Recursos procesados

| `resource_key` | Producto | Agrupador | Nombre CKAN esperado |
|----------------|----------|-----------|----------------------|
| `petroleo_provincia` | petroleo | provincia | Producción de petróleo promedio diaria por provincia |
| `gas_provincia` | gas | provincia | Producción de gas promedio diaria por provincia |
| `petroleo_cuenca` | petroleo | cuenca | Producción de petróleo promedio diaria por cuenca |
| `gas_cuenca` | gas | cuenca | Producción de gas promedio diaria por cuenca |
| `petroleo_empresa` | petroleo | empresa | Producción de petróleo promedio diaria por empresa |
| `gas_empresa` | gas | empresa | Producción de gas promedio diaria por empresa |

### Archivos generados

Por cada recurso (vía `process_resource`):

- `{resource_key}_model_clean.csv`
- `{resource_key}_periodos_resumen.csv`

Globales (vía `export_unified`):

- `sesco_produccion_model_clean.csv`
- `sesco_validaciones_resumen.csv`

### Dependencia con `sesco_processing.py`

Importa directamente:

```python
from sesco_processing import export_unified, process_all_resources
```

Toda la lógica de transformación reside en el módulo; el script solo define configuración MVP y `main()`.

---

## 8. `sesco_processing.py` — Módulo central

Es el **núcleo reutilizable** del ETL exploratorio SESCO.

| Aspecto | Descripción |
|---------|-------------|
| Origen | Lógica extraída de `01_exploracion_sesco.ipynb` y generalizada en `02_modelo_unificado_sesco.ipynb` |
| Rol | Evitar duplicar transformaciones en notebooks |
| Alcance | 6 CSV agregados MVP (provincia / cuenca / empresa × petróleo / gas) |
| Futuro | Funciones candidatas a migrar a un ETL formal con carga en PostgreSQL |

El módulo implementa una **mini-pipeline** con etapas encadenadas: descubrimiento CKAN → raw → modelo → limpieza → exclusión de período incompleto → export → unificación.

---

## 9. Constantes y configuración

| Constante | Propósito | Dónde se usa | Por qué existe |
|-----------|-----------|--------------|----------------|
| `PACKAGE_ID` | Identificador CKAN del dataset SESCO | `CKAN_URL`, documentación | Punto único de verdad del dataset oficial |
| `CKAN_URL` | Endpoint `package_show` | `fetch_ckan_package` | URL completa para metadata |
| `USER_AGENT` | Cabecera HTTP identificable | Todas las descargas HTTP | Buena práctica ante APIs públicas |
| `SCRIPT_DIR` | Directorio del módulo | Derivación de paths | Rutas relativas al código, no al CWD |
| `EXPLORATION_DIR` | Carpeta `exploration/` | Derivación de `RAW_DIR`, `PROCESSED_DIR` | Separar código de datos |
| `RAW_DIR` | `exploration/data/raw/` | `download_csv`, `process_resource` | CSV descargados sin transformar |
| `PROCESSED_DIR` | `exploration/data/processed/` | Todas las exportaciones | Salida analítica del MVP |
| `RESOURCE_FIELDS` | Campos CKAN a conservar | `list_resources`, `inspect_ckan_resources` | Subconjunto útil de metadata |
| `REQUIRED_MODEL_COLUMNS` | Columnas mínimas antes de preprocesar | `preprocess_model_df` | Fallar temprano si falta estructura |
| `EXPORT_COLUMNS` | Esquema del CSV final | `prepare_export_df` | Contrato estable para dashboard y unificado |
| `PERIOD_COLUMN_PRIORITY` | Orden de búsqueda de columna período | `detect_period_column` | CSV SESCO usan nombres distintos (`indice_tiempo`, etc.) |
| `DUPLICATE_KEY_COLUMNS` | Clave lógica de unicidad | `validate_unified_dataset` | Detectar filas duplicadas en el unificado |

### Configuración de recursos (externa al módulo)

La configuración MVP vive en `run_mvp_processing.py` (`SESCO_RESOURCES_MVP`) y se replica en la notebook 02. Cada entrada es un `dict` con:

| Clave | Significado |
|-------|-------------|
| `producto` | `"petroleo"` o `"gas"` |
| `agrupador_tipo` | `"provincia"`, `"cuenca"` o `"empresa"` |
| `nombre_recurso` | Nombre exacto esperado en CKAN (con fallback por keywords) |

---

## 10. Estructuras de datos

### `ProcessResult` (dataclass)

Representa el **resultado completo** del pipeline para un recurso individual.

| Atributo | Tipo | Descripción |
|----------|------|-------------|
| `resource_key` | `str` | Clave interna (ej. `petroleo_provincia`) |
| `config` | `dict` | Configuración del recurso |
| `df_model` | `pd.DataFrame` | Modelo sin preprocesamiento final |
| `df_model_clean` | `pd.DataFrame` | Tras `preprocess_model_df` (incluye último período posiblemente incompleto) |
| `df_model_clean_valid` | `pd.DataFrame` | Tras excluir período incompleto |
| `periodo_info` | `dict` | Resultado de `detectar_periodo_incompleto` |
| `validation` | `dict` | Resumen para `sesco_validaciones_resumen.csv` |
| `raw_path` | `Path` | Ruta del CSV raw usado |
| `export_path` | `Path` | Ruta del `{key}_model_clean.csv` |
| `observaciones` | `list[str]` | Notas (descarga, período excluido, errores) |

**Uso:** `process_resource` la devuelve; `process_all_resources` acumula una lista para concatenar y generar validaciones. Permite inspeccionar etapas intermedias en notebooks sin re-ejecutar todo.

---

## 11. Funciones de `sesco_processing.py`

### Funciones de acceso CKAN y descarga

#### `fetch_ckan_package`

- **Propósito:** Obtener el JSON completo del dataset SESCO desde CKAN.
- **Entradas:** `timeout` (60 s), `retries` (3), `backoff_s` (2.0) — reintentos ante HTTP 500 o errores de red.
- **Salida:** `dict` con el nodo `result` de CKAN.
- **Lógica resumida:** Request GET con `User-Agent` → parse JSON → validar `success` → reintentar con backoff.
- **Uso en el flujo:** Primera etapa de `list_resources` y `process_all_resources`.
- **Consideraciones:** Errores persistentes lanzan `RuntimeError`.

#### `list_resources`

- **Propósito:** Tabular los recursos del paquete CKAN.
- **Entradas:** `package` opcional (si es `None`, llama a `fetch_ckan_package`).
- **Salida:** `pd.DataFrame` con columnas `RESOURCE_FIELDS`.
- **Lógica resumida:** Proyecta cada recurso a un diccionario plano.
- **Uso:** Entrada de `find_resource_by_name` y `process_resource`.
- **Consideraciones:** No filtra por formato; el filtro CSV ocurre en `find_resource_by_name`.

#### `find_resource_by_name`

- **Propósito:** Resolver un recurso CKAN a partir del nombre oficial o keywords.
- **Entradas:** `resources_df`, `nombre_recurso`, `fallback_keywords` opcional.
- **Salida:** `pd.Series` del recurso elegido, o `None`.
- **Lógica resumida:** Match exacto normalizado → si falla, match por keywords → prioriza CSV, nombre con "promedio", `last_modified` más reciente.
- **Uso:** `process_resource` para cada recurso MVP.
- **Consideraciones:** La normalización ignora acentos y mayúsculas. Si hay homónimos, gana el más reciente.

#### `download_csv`

- **Propósito:** Descargar CSV remoto a `RAW_DIR` si no existe localmente.
- **Entradas:** `url`, `destination`, `timeout`.
- **Salida:** `Path` al archivo local.
- **Lógica resumida:** Crea directorios → si existe, retorna → si no, descarga bytes.
- **Uso:** `process_resource` cuando `download_if_missing=True`.
- **Consideraciones:** **No re-descarga** si el archivo ya está; para forzar actualización hay que borrar el raw manualmente.

#### `read_csv`

- **Propósito:** Leer CSV SESCO con encoding habitual de datos.gob.ar.
- **Entradas:** `path: Path`.
- **Salida:** `pd.DataFrame`.
- **Uso:** Tras localizar raw en `process_resource`.

---

### Funciones de normalización y detección de columnas

#### `normalize_text`

- **Propósito:** Comparación insensible a acentos y mayúsculas.
- **Uso:** Matching de nombres CKAN y detección de columnas.

#### `normalize_column_names`

- **Propósito:** Unificar encabezados a `snake_case` sin acentos.
- **Entradas:** DataFrame raw.
- **Salida:** Copia con columnas renombradas.
- **Lógica:** NFKD → quitar diacríticos → minúsculas → reemplazar no alfanuméricos por `_`.
- **Uso:** Primera transformación sobre raw en `process_resource`.

#### `detect_date_columns` / `detect_numeric_columns` / `detect_categorical_columns`

- **Propósito:** Clasificación heurística de columnas para detección automática.
- **Uso:** Soporte de `detect_period_column`, `find_production_column`, `find_group_column`.

#### `detect_period_column`

- **Propósito:** Elegir la columna principal de período.
- **Prioridad:** `indice_tiempo` → `periodo` → `fecha` → `anio_mes` → otra columna date-like que no sea solo `anio`/`mes`.
- **Salida:** nombre de columna o `None` (entonces `build_model_df` usa `anio`+`mes` en preprocesamiento).

#### `find_production_column`

- **Propósito:** Detectar columna de producción según producto.
- **Lógica:** Busca "produccion" + token de producto → fallback "prod*" → primer numérico excluyendo año/mes.

#### `find_group_column`

- **Propósito:** Detectar columna del agrupador (`provincia`, `cuenca`, `empresa`).
- **Lógica:** Substring del tipo en nombre de columna → primera categórica.

#### `infer_tipo_recurso`

- **Propósito:** Clasificar tipo de serie según nombre CKAN (`shale_tight`, `promedio_diario`, `serie_historica`).
- **Uso:** Columna `tipo_recurso` del modelo.

#### `infer_raw_filename`

- **Propósito:** Nombre de archivo en `data/raw/`.
- **Lógica:** Mapa legacy para `petroleo_provincia` → nombre histórico; si no, nombre desde URL o `{resource_key}.csv`.

---

### Construcción y limpieza del modelo

#### `build_model_df`

- **Propósito:** Armar el DataFrame con **esquema analítico común** a partir del CSV normalizado.
- **Entradas:** `df_norm`, `producto`, `agrupador_tipo`, `source_resource`, `tipo_recurso` opcional.
- **Salida:** DataFrame con columnas del modelo intermedio (`periodo`, `anio`, `mes`, `producto`, `agrupador_tipo`, `agrupador_nombre`, `tipo_recurso`, `produccion`, `source_resource`).
- **Lógica:** Detecta columnas → asigna metadatos fijos del config → copia valores.
- **Uso:** Núcleo semántico del ETL; misma estructura para los 6 recursos.
- **Consideraciones:** Lanza `ValueError` si no detecta producción o agrupador.

#### `preprocess_model_df`

- **Propósito:** Normalizar tipos, construir `periodo_str` / `periodo_dt`, eliminar filas inválidas.
- **Entradas:** `df_model` con `REQUIRED_MODEL_COLUMNS`.
- **Salida:** DataFrame limpio ordenado por período y agrupador.
- **Lógica resumida:**
  - Parsea `YYYY-M` o `YYYY-MM` en `periodo`.
  - Fallback `anio` + `mes` si el período no matchea regex.
  - `produccion` a numérico con `errors="coerce"`.
  - Drop de filas sin `periodo_dt`, `produccion` o `agrupador_nombre`.
- **Consideraciones:** **No** convierte nulos de producción a cero; los elimina por `dropna`.

---

### Períodos incompletos

#### `detectar_periodo_incompleto`

- **Propósito:** Identificar si el **último período** del recurso parece carga parcial.
- **Entradas:** `df_model_clean`, `threshold=0.5`, `verbose`.
- **Salida:** `dict` con `latest_period`, `latest_valid_period`, `excluded_period`, `ratio`, `periodos_validos`.
- **Lógica:** Suma producción por período → compara último vs penúltimo → si ratio < 50 %, excluye último período de `periodos_validos`.
- **Uso:** Por recurso en `process_resource`.
- **Consideraciones:** No borra filas del DataFrame original; la exclusión la aplica `apply_valid_periods`.

#### `apply_valid_periods`

- **Propósito:** Filtrar filas cuyo `periodo_dt` está en `periodo_info["periodos_validos"]`.
- **Uso:** Genera `df_model_clean_valid` para export y análisis.

#### `get_valid_periods_by_group`

- **Propósito:** Misma regla del 50 %, pero sobre el **dataset unificado** filtrado por `producto` + `agrupador_tipo`.
- **Uso:** Notebook 02 — evita un único corte global cuando cada vista tiene distinto último mes publicado.
- **Consideraciones:** **No** la invoca `run_mvp_processing.py`; el dashboard usa archivos de la notebook 03.

---

### Validación

#### `validate_resource_basic`

- **Propósito:** Fila de resumen por recurso para auditoría.
- **Salida:** `dict` con conteos, rangos de período, agrupadores, nulos, negativos, observaciones.
- **Uso:** `process_resource` → acumulado en `sesco_validaciones_resumen.csv`.

#### `validate_unified_dataset`

- **Propósito:** Chequeos globales del unificado: duplicados, cobertura por producto/tipo, rangos.
- **Salida:** `dict` con métricas y muestra de duplicados.
- **Uso:** Notebook 02 (`sp.validate_unified_dataset`). **No** la invoca el CLI.

---

### Exportación

#### `prepare_export_df`

- **Propósito:** Seleccionar `EXPORT_COLUMNS` y formatear `periodo_dt` como string `YYYY-MM-DD` para CSV.

#### `export_model_clean`

- **Propósito:** Escribir `{resource_key}_model_clean.csv` en `PROCESSED_DIR`.

#### `export_periodos_resumen`

- **Propósito:** Escribir `{resource_key}_periodos_resumen.csv` con producción total y cantidad de agrupadores por `periodo_str`.

#### `export_unified`

- **Propósito:** Escribir `sesco_produccion_model_clean.csv` y `sesco_validaciones_resumen.csv`.
- **Uso:** `run_mvp_processing.py` y notebook 02.

#### `format_number`

- **Propósito:** Formato numérico local (coma decimal) para salidas legibles en notebooks. **No** usada por el CLI.

#### `build_totals_comparison_table`

- **Propósito:** Tabla pivote con totales por período y producto en las tres vistas (provincia, cuenca, empresa) y diferencias porcentuales vs provincia.
- **Uso:** Notebook 02. **No suma vistas entre sí** — solo control de cobertura/consistencia.
- **Consideraciones:** Notebook 03 implementa lógica similar inline para exportar `sesco_totales_por_vista_resumen.csv`.

---

### Orquestación

#### `process_resource`

- **Propósito:** Pipeline end-to-end de **un** recurso MVP.
- **Entradas:** `resource_key`, `config`, `resources_df`, paths opcionales, `incomplete_threshold`, flags de descarga y verbose.
- **Salida:** `ProcessResult`.
- **Etapas:** find CKAN → raw → read → normalize → build → preprocess → detectar incompleto → filter → export individual + resumen → validation.
- **Consideraciones:** Si `download_if_missing=False` y falta raw, `FileNotFoundError`.

#### `process_all_resources`

- **Propósito:** Iterar `resources_config`, concatenar válidos, armar DataFrame de validaciones.
- **Salida:** `tuple[list[ProcessResult], pd.DataFrame unificado, pd.DataFrame validaciones]`.
- **Manejo de errores:** Captura excepciones por recurso, registra `ProcessResult` vacío con observación de error, continúa con el resto.
- **Inconsistencia detectada:** Contiene llamadas de depuración `print(i)` y `print(valid_frames)` que no aportan al pipeline productivo (ver sección 26).

---

## 12. Modelo común generado

Columnas exportadas (`EXPORT_COLUMNS`):

| Columna | Significado | Origen | Uso | ¿En dataset final? |
|---------|-------------|--------|-----|-------------------|
| `periodo_str` | Período `YYYY-MM` | `periodo` / `anio`+`mes` tras preproceso | Filtros, ejes, joins | Sí |
| `periodo_dt` | Primer día del mes como fecha | Derivado de `periodo_str` | Orden temporal, slider Streamlit | Sí (como fecha en CSV) |
| `anio` | Año calendario | De `periodo_dt` | Agregaciones anuales | Sí |
| `mes` | Mes (1–12) | De `periodo_dt` | Estacionalidad | Sí |
| `producto` | `petroleo` o `gas` | Config del recurso | Separación de análisis (unidades distintas) | Sí |
| `agrupador_tipo` | `provincia`, `cuenca` o `empresa` | Config | Vista analítica | Sí |
| `agrupador_nombre` | Nombre de la entidad | Columna detectada en CSV | Rankings, filtros, mapas | Sí |
| `tipo_recurso` | Clasificación de la serie | `infer_tipo_recurso` o default `promedio_diario` | Trazabilidad de tipo de publicación | Sí |
| `produccion` | Valor mensual publicado | Columna detectada en CSV | KPIs y gráficos | Sí |
| `source_resource` | Nombre oficial CKAN | Metadata del recurso | Trazabilidad | Sí |

Columnas intermedias **no exportadas:** `periodo`, `periodo_original` (solo durante preprocesamiento).

---

## 13. Reglas metodológicas implementadas

| Regla | Implementación |
|-------|----------------|
| No sumar provincia + cuenca + empresa | Vistas separadas en el modelo; `build_totals_comparison_table` solo compara, no agrega |
| Provincia, cuenca y empresa son vistas alternativas | `agrupador_tipo` distingue cada CSV fuente |
| Totales nacionales: una sola vista, preferentemente provincia | Documentado en notebooks y Streamlit; comparaciones usan provincia como referencia |
| Último período válido por `producto` + `agrupador_tipo` | `get_valid_periods_by_group`; archivos auxiliares en notebook 03 |
| No usar máximo global del dataset | Evita mezclar recursos con distintas fechas de corte |
| No comparar petróleo y gas como misma unidad | Filtro por `producto` en dashboard y análisis |
| No convertir nulos a cero sin justificación | `preprocess_model_df` usa `dropna`, no `fillna(0)` |
| Trazabilidad con `source_resource` | Propagado desde CKAN en `build_model_df` |
| Período incompleto: caída abrupta del último mes | Regla 50 % en `detectar_periodo_incompleto` / `get_valid_periods_by_group` |

---

## 14. Detección de períodos incompletos

### Problema detectado

SESCO publica el mes en curso antes de cerrarlo. El último período puede tener **producción muy inferior** al mes anterior (pocos días reportados), distorsionando KPIs de "último período" y variación mes a mes.

### Regla del 50 %

Para cada serie (recurso individual o grupo producto+agrupador):

1. Sumar `produccion` por `periodo_dt`.
2. Comparar total del último período vs penúltimo.
3. Si `último / penúltimo < 0.5`, marcar último como **incompleto**.
4. `latest_valid_period` pasa al penúltimo; el último queda en `excluded_period`.

### Funciones

| Función | Ámbito |
|---------|--------|
| `detectar_periodo_incompleto` | Un recurso (`process_resource`) |
| `get_valid_periods_by_group` | Subconjunto del unificado por producto y agrupador |

### Efecto en KPIs y gráficos

- Los CSV exportados (`*_model_clean.csv`, unificado) **ya excluyen** el período incompleto (vía `apply_valid_periods`).
- El dato original sigue en `df_model_clean` dentro de `ProcessResult`, pero no en exports.
- Streamlit lee `sesco_latest_periods_by_view.csv` para no recalcular el corte en la UI.

---

## 15. Validaciones

### Por recurso (`validate_resource_basic`)

| Validación | Detalle |
|------------|---------|
| Columnas esperadas | Implícita en `preprocess_model_df` (`REQUIRED_MODEL_COLUMNS`) |
| Nulos en producción | Conteo sobre `df_model` original |
| Filas limpias vs original | Antes/después de limpieza y filtro de períodos |
| Producción negativa | Conteo en datos válidos exportados |
| Períodos min/max | `periodo_min`, `latest_valid_period`, `periodo_max_disponible` |
| Cantidad de agrupadores | `nunique` de `agrupador_nombre` |
| Observaciones | Descargas, períodos excluidos, errores |

### Dataset unificado (`validate_unified_dataset`)

| Validación | Detalle |
|------------|---------|
| Duplicados | Sobre `DUPLICATE_KEY_COLUMNS` |
| Cobertura | Registros y agrupadores únicos por `producto` + `agrupador_tipo` |
| Rango temporal | `periodo_min` / `periodo_max` global del unificado |
| Muestra de duplicados | Hasta 20 filas para inspección |

### Notebook 03 (adicional, fuera del módulo)

Validaciones de negocio sobre el unificado ya exportado: negativos, huecos de períodos, consistencia de configuración para Streamlit.

---

## 16. Archivos generados

| Archivo | Generado por | Descripción | Uso posterior |
|---------|--------------|-------------|---------------|
| `ckan_resources.csv` | `inspect_ckan_resources.py` | Inventario CKAN | Auditoría de fuentes |
| `petroleo_provincia_model_clean.csv` | `export_model_clean` | Petróleo por provincia | Análisis puntual, unificado |
| `gas_provincia_model_clean.csv` | idem | Gas por provincia | idem |
| `petroleo_cuenca_model_clean.csv` | idem | Petróleo por cuenca | Mapas, unificado |
| `gas_cuenca_model_clean.csv` | idem | Gas por cuenca | idem |
| `petroleo_empresa_model_clean.csv` | idem | Petróleo por empresa | idem |
| `gas_empresa_model_clean.csv` | idem | Gas por empresa | idem |
| `*_periodos_resumen.csv` | `export_periodos_resumen` | Totales por período por recurso | Control de series |
| `sesco_produccion_model_clean.csv` | `export_unified` | Dataset unificado MVP | Streamlit, notebook 03 |
| `sesco_validaciones_resumen.csv` | `export_unified` | Una fila por recurso procesado | Notebook 03, auditoría |
| `sesco_latest_periods_by_view.csv` | Notebook 03 | Último período válido por vista | Streamlit KPIs |
| `sesco_dashboard_config.csv` | Notebook 03 | Min/max período y conteos por vista | Streamlit (cargado; uso parcial) |
| `sesco_totales_por_vista_resumen.csv` | Notebook 03 | Comparación provincia/cuenca/empresa | Análisis de consistencia |

Archivos geoespaciales (`cuencas_*.geojson`, etc.) provienen de `04_exploracion_geoespacial_cuencas.ipynb`, no de estos scripts.

---

## 17. Relación con notebooks

| Notebook | Rol | Qué aportó al módulo / scripts |
|----------|-----|--------------------------------|
| `01_exploracion_sesco.ipynb` | Exploración inicial de un recurso (petróleo provincia) | CKAN, normalización, `detectar_periodo_incompleto`, visualizaciones sobre `df_model_clean_valid` |
| `02_modelo_unificado_sesco.ipynb` | Generalización a 6 recursos | Importa `sesco_processing`; `process_all_resources`, `export_unified`, `validate_unified_dataset`, `get_valid_periods_by_group`, `build_totals_comparison_table` |
| `03_validacion_final_sesco.ipynb` | Validación pre-dashboard | Lee unificado; exporta `sesco_latest_periods_by_view.csv`, `sesco_dashboard_config.csv`, `sesco_totales_por_vista_resumen.csv` |
| `04_exploracion_geoespacial_cuencas.ipynb` | Capas para mapa | Independiente del ETL tabular; consume períodos desde processed |

**Lógica reusable hoy en `sesco_processing.py`:** todo el ETL por recurso, unificación, validaciones básicas y comparación de totales (función disponible; export de totales también en notebook 03).

**`run_mvp_processing.py`:** ejecuta el núcleo de la notebook 02 sin celdas de visualización ni exports de la notebook 03.

---

## 18. Relación con Streamlit

El dashboard (`exploration/streamlit_app/app.py`) es principalmente **capa de visualización**:

- **Consume** CSV procesados; no llama a `sesco_processing`.
- **No debe** reimplementar reglas del 50 % ni detección de columnas.
- La lógica pesada permanece en scripts/notebooks.

### Archivos que carga el dashboard

| Archivo | Uso en Streamlit |
|---------|------------------|
| `sesco_produccion_model_clean.csv` | Dataset principal, filtros, gráficos, tabla |
| `sesco_latest_periods_by_view.csv` | Último período válido por producto + agrupador |
| `sesco_dashboard_config.csv` | Cargado al inicio (metadatos por vista) |
| `cuencas_sedimentarias_consolidadas.geojson` | Mapa coroplético (notebook 04) |
| `cuencas_sesco_match_report.csv` | Panel de match de nombres |
| `cuencas_sesco_geo_coverage_summary.csv` | Resumen de cobertura geográfica |

**Flujo recomendado antes de abrir Streamlit:**

1. `python exploration/scripts/run_mvp_processing.py`
2. Ejecutar notebook `03_validacion_final_sesco.ipynb`
3. (Opcional) notebook 04 para capas geoespaciales
4. `streamlit run exploration/streamlit_app/app.py`

---

## 19. Cómo ejecutar los scripts

Ejecutar desde la **raíz del repositorio** (`OilAndGas/`):

```bash
# Inventario CKAN
python exploration/scripts/inspect_ckan_resources.py

# Pipeline MVP completo (6 recursos)
python exploration/scripts/run_mvp_processing.py

# Descarga puntual
python exploration/scripts/download_sesco_resource.py "https://..." nombre_opcional.csv
```

### Imports y `PYTHONPATH`

- `run_mvp_processing.py` hace `from sesco_processing import ...`. Python agrega el directorio del script (`exploration/scripts/`) a `sys.path[0]` al ejecutar el archivo, por lo que **funciona desde la raíz** sin configuración extra.
- Los notebooks insertan explícitamente `SCRIPTS_DIR` en `sys.path` antes de `import sesco_processing`.
- Si se importa desde otro paquete sin ajustar el path, puede fallar; alternativa:

```bash
set PYTHONPATH=exploration/scripts
python -c "import sesco_processing; print(sesco_processing.PACKAGE_ID)"
```

### Streamlit (referencia)

```bash
pip install streamlit plotly geopandas
streamlit run exploration/streamlit_app/app.py
```

(`streamlit` no está listado en `exploration/requirements.txt`; instalar aparte para el dashboard.)

---

## 20. Dependencias

### Usadas directamente por los scripts

| Dependencia | Uso |
|-------------|-----|
| **pandas** | Lectura CSV, transformaciones, export |
| **stdlib: urllib** | HTTP CKAN y descargas (no se usa `requests` en scripts) |
| **stdlib: pathlib** | Rutas portables |
| **stdlib: json, csv, re, time, unicodedata, argparse, dataclasses** | Soporte general |

### En `exploration/requirements.txt` pero no en scripts

| Paquete | Dónde se usa |
|---------|--------------|
| requests | Notebook `01_exploracion_sesco.ipynb` |
| matplotlib, jupyter, ipykernel | Notebooks |
| geopandas, shapely, folium | Notebook 04 y mapa Streamlit |
| plotly | Streamlit |

**geopandas/shapely:** no aplican a los scripts actuales de `exploration/scripts/`.

---

## 21–22. Docstrings y comentarios en código

La documentación inline de `sesco_processing.py` fue ampliada en español (módulo, `ProcessResult`, funciones públicas y auxiliares relevantes) siguiendo formato NumPy-style. Los otros scripts mantienen o amplían docstrings de módulo donde correspondía.

Ver el archivo fuente para el detalle línea a línea.

---

## 23. Alcance de cambios

Esta documentación y los docstrings **no modifican** reglas de procesamiento, nombres de columnas, paths ni comportamiento funcional.

---

## 24. Alineación código ↔ documentación

| Tema | Estado |
|------|--------|
| `__init__.py` | Documentado como esperado pero **ausente** |
| `requests` en requirements vs `urllib` en scripts | Documentado |
| `sesco_latest_periods_by_view.csv` etc. | Generados en notebook 03, no en CLI |
| `print(i)` / `print(valid_frames)` en `process_all_resources` | Código de depuración residual; no documentado como feature |
| `format_number` | Definida; uso principal en notebooks |
| Config MVP duplicada | `run_mvp_processing.py` y notebook 02 |

---

## 25. Resultado de esta entrega

- **Creado:** `exploration/documentacion_scripts_exploration.md`
- **Modificado:** `exploration/scripts/sesco_processing.py` (docstrings y comentarios)
- **Opcional:** docstrings breves en `inspect_ckan_resources.py`, `download_sesco_resource.py`, `run_mvp_processing.py`

---

## 26. Recomendaciones futuras

1. Agregar `exploration/scripts/__init__.py` vacío y migrar imports a paquete formal.
2. Eliminar `print` de depuración en `process_all_resources`.
3. Extraer `SESCO_RESOURCES_MVP` a un módulo `config.py` compartido con la notebook 02.
4. Hacer que `run_mvp_processing.py` opcionalmente genere también los CSV de la notebook 03 (o un script `run_dashboard_exports.py`).
5. Añadir `streamlit` a requirements del subproyecto exploration.
6. Tests unitarios para `detectar_periodo_incompleto`, `normalize_column_names` y `find_resource_by_name`.
7. Flag `--force-download` para refrescar raw sin borrado manual.
