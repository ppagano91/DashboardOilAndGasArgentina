# Revisión de documentación — exploration SESCO

**Fecha de auditoría:** 2026-06-01  
**Alcance:** carpeta `exploration/` del repositorio `OilAndGas`  
**Método:** inspección de archivos en disco, lectura de código/notebooks y análisis con pandas sobre CSV locales (sin modificar artefactos del proyecto).

**Documentación de referencia usada para comparar:**

| Fuente | Ubicación | Observación |
|--------|-----------|-------------|
| README de exploration | `exploration/README.md` | Única documentación dedicada versionada en el repo para esta sección |
| Documento citado por el usuario | *"Documentación de la sección exploration — Monitor Hidrocarburífero SESCO"* | **No encontrado** en el repositorio (ni en `docs/`). La comparación incluye el checklist del encargo de auditoría, que describe el contenido esperado de ese documento externo |
| README raíz | `README.md` | Mención breve de `exploration/` |
| Plan MVP | `docs/mvp-plan.md` | Alcance general del producto, no detalle de exploration |

---

## 1. Resumen ejecutivo

El estado real de `exploration/` **supera ampliamente** lo descrito en `exploration/README.md`. En disco existen:

- **3 notebooks** (no 1): exploración inicial, modelo unificado de 6 recursos y validación final.
- **4 scripts Python** (no 2): incluyen el módulo central `sesco_processing.py` y el runner `run_mvp_processing.py`.
- **18 CSV procesados** relevantes para el MVP (12 por recurso + 6 auxiliares/unificado), más `ckan_resources.csv`.
- **Dashboard Streamlit** en `exploration/streamlit_app/app.py`.
- **6 CSV raw** descargados para los recursos MVP.

La documentación versionada (`exploration/README.md`) quedó en una fase inicial (solo notebook 01, dos scripts, sin mencionar datasets unificados ni reglas metodológicas). Si el documento externo del usuario describe el pipeline completo (notebooks 02–03, unificado, Streamlit, reglas), **ese contenido refleja mejor la realidad del proyecto que el README actual del repo**.

**Hallazgos críticos:**

| Tema | Estado |
|------|--------|
| Estructura de carpetas esperada | Completa (salvo dependencias Streamlit no pinneadas en `requirements.txt`) |
| Dataset unificado | Existe: 33 912 filas, 10 columnas, 0 duplicados en clave lógica, 1 valor negativo marginal |
| Archivos auxiliares dashboard | Generados por notebook 03; **Streamlit usa solo 2 de 3** (`sesco_dashboard_config.csv` se carga pero no se consume) |
| Reglas metodológicas | Implementadas en scripts/notebooks/Streamlit con matices (ver sección 9) |
| README `exploration/` | **Desactualizado** respecto al código y datos locales |

---

## 2. Estructura real encontrada

### 2.1 Carpetas requeridas

| Ruta | ¿Existe? | Contenido principal |
|------|----------|------------------------|
| `exploration/` | Sí | `README.md`, `requirements.txt`, `.venv/` (local), subcarpetas abajo |
| `exploration/notebooks/` | Sí | 3 notebooks `.ipynb`, `_out.txt` (auxiliar de ejecución) |
| `exploration/scripts/` | Sí | 4 módulos `.py` + `__init__.py` |
| `exploration/data/raw/` | Sí | 6 CSV de recursos MVP + `.gitkeep` |
| `exploration/data/processed/` | Sí | 19 CSV (incl. `ckan_resources.csv`) + `.gitkeep` |
| `exploration/streamlit_app/` | Sí | `app.py` |

### 2.2 Árbol resumido (excluye `.venv`)

```
exploration/
├── README.md
├── requirements.txt
├── notebooks/
│   ├── 01_exploracion_sesco.ipynb
│   ├── 02_modelo_unificado_sesco.ipynb
│   ├── 03_validacion_final_sesco.ipynb
│   └── _out.txt
├── scripts/
│   ├── __init__.py
│   ├── download_sesco_resource.py
│   ├── inspect_ckan_resources.py
│   ├── sesco_processing.py
│   └── run_mvp_processing.py
├── data/
│   ├── raw/          (6 CSV MVP + .gitkeep)
│   └── processed/    (19 CSV + .gitkeep)
└── streamlit_app/
    └── app.py
```

### 2.3 Diferencias vs `exploration/README.md`

| Elemento documentado en README | Realidad |
|--------------------------------|----------|
| Solo `01_exploracion_sesco.ipynb` | También `02_modelo_unificado_sesco.ipynb`, `03_validacion_final_sesco.ipynb` |
| Scripts: `inspect_ckan_resources.py`, `download_sesco_resource.py` | Además `sesco_processing.py`, `run_mvp_processing.py` |
| Sin `streamlit_app/` en el diagrama | Existe `exploration/streamlit_app/app.py` |
| Flujo recomendado: inspect → notebook 01 | Flujo real completo: 01 → 02 (o `run_mvp_processing.py`) → 03 → Streamlit |
| `requirements.txt` sin Streamlit/Plotly | Streamlit documentado solo como `pip install` manual en README |

### 2.4 Raw (`exploration/data/raw/`)

| Archivo | Recurso inferido |
|---------|------------------|
| `produccion_petroleo_promedio_diaria_por_provincia.csv` | Petróleo por provincia |
| `produccin-de-gas-promedio-diaria-por-provincia.csv` | Gas por provincia |
| `produccin-de-petrleo-promedio-diaria-por-cuenca.csv` | Petróleo por cuenca |
| `produccin-de-gas-promedio-diaria-por-cuenca.csv` | Gas por cuenca |
| `produccin-de-petrleo-promedio-diaria-por-empresa.csv` | Petróleo por empresa |
| `produccin-de-gas-promedio-diaria-por-empresa.csv` | Gas por empresa |

**Nota:** nombres con caracteres corruptos (`produccin`, `petrleo`) por inferencia de URL CKAN; el pipeline los resuelve por clave de recurso, no solo por nombre de archivo.

---

## 3. Revisión de notebooks

### 3.1 Resumen comparativo

| Notebook | ¿Existe? | Coincide con README | Rol en el pipeline |
|----------|----------|---------------------|-------------------|
| `exploration/notebooks/01_exploracion_sesco.ipynb` | Sí | Parcial (única mencionada) | Exploración CKAN + primer recurso (petróleo provincia) |
| `exploration/notebooks/02_modelo_unificado_sesco.ipynb` | Sí | No documentada en README | Procesa 6 recursos MVP vía `sesco_processing` |
| `exploration/notebooks/03_validacion_final_sesco.ipynb` | Sí | No documentada en README | Validación final + CSV auxiliares para Streamlit |

---

### 3.2 `01_exploracion_sesco.ipynb`

| Aspecto | Detalle |
|---------|---------|
| **Objetivo aparente** | Explorar el dataset SESCO en CKAN, identificar recursos MVP, descargar CSV, normalizar hacia modelo analítico, gráficos exploratorios y exportación del **primer** recurso (petróleo por provincia). |
| **Secciones markdown principales** | 1. Importar librerías · 2. Consultar API CKAN · 3. Validar respuesta · 4–6. DataFrame de recursos · 7. Recursos candidatos · 8. Recursos MVP · 9. Descargar CSV · 10–11. Leer/inspeccionar · 12. Funciones auxiliares · 13–14. Normalización y preprocesamiento · 15. Validaciones · 16. Gráficos (evolución, ranking Top 15, Top 5) · 17. Exportación · 18. Conclusiones |
| **Outputs / archivos generados** | Lógica de exportación hacia `{resource_key}_model_clean.csv` y `{resource_key}_periodos_resumen.csv` (en ejecución típica: `petroleo_provincia_*`). También puede generar/usar `ckan_resources.csv` vía flujo CKAN. |
| **vs documentación** | **Coincide** con README como notebook principal de exploración. **No documenta** que el procesamiento masivo migró a notebook 02 + `sesco_processing.py`. |
| **Observaciones** | Contiene lógica duplicada respecto a `sesco_processing.py` (comentario en el propio módulo: *"Extraído de 01_exploracion_sesco.ipynb"*). Sigue siendo válida como laboratorio; no es la fuente única del pipeline actual. |

---

### 3.3 `02_modelo_unificado_sesco.ipynb`

| Aspecto | Detalle |
|---------|---------|
| **Objetivo aparente** | Procesar en lote los **6 recursos MVP** (petróleo/gas × provincia/cuenca/empresa), unificar, validar y graficar. |
| **Secciones markdown principales** | 1. Configuración e imports · 2. Recursos MVP · 3. CKAN y listado · 4. Procesar 6 recursos · 5. Exportar unificado y validaciones · 6. Validaciones del unificado · 7. Gráficos exploratorios · 8. Resumen |
| **Outputs / archivos generados** | Por recurso: `{key}_model_clean.csv`, `{key}_periodos_resumen.csv`. Globales: `sesco_produccion_model_clean.csv`, `sesco_validaciones_resumen.csv`. Comparativa de vistas vía `build_totals_comparison_table` (en memoria; el CSV `sesco_totales_por_vista_resumen.csv` se exporta en notebook 03). |
| **vs documentación** | **No mencionada** en `exploration/README.md`. Es el paso central del MVP actual. |
| **Observaciones** | Importa `sesco_processing` desde `exploration/scripts/`. Output documentado en celdas: **33 912 filas** en unificado. Incluye comentarios explícitos: *"NO sumar provincia+cuenca+empresa"*. |

---

### 3.4 `03_validacion_final_sesco.ipynb`

| Aspecto | Detalle |
|---------|---------|
| **Objetivo aparente** | Validar el dataset unificado antes del dashboard; generar tablas auxiliares para no recalcular reglas en Streamlit. |
| **Secciones markdown principales** | Título + validaciones numeradas en código (estructura, duplicados, períodos por vista, negativos, comparación de totales entre vistas) · **Conclusiones y reglas para Streamlit** |
| **Outputs / archivos generados** | `sesco_latest_periods_by_view.csv`, `sesco_dashboard_config.csv`, `sesco_totales_por_vista_resumen.csv` (requiere existencia previa de unificado + `sesco_validaciones_resumen.csv`) |
| **vs documentación** | **Ausente** en README. Alineada con un documento externo completo si este describe archivos auxiliares y reglas. |
| **Observaciones** | Conclusiones explícitas: usar una sola `agrupador_tipo` por análisis; último período válido por `producto + agrupador_tipo`; no comparar petróleo y gas en el mismo eje sin base 100. |

---

## 4. Revisión de scripts

### 4.1 Inventario (`exploration/scripts/`)

| Archivo | ¿En README? | Propósito |
|---------|-------------|-----------|
| `inspect_ckan_resources.py` | Sí | Lista recursos CKAN → `ckan_resources.csv` |
| `download_sesco_resource.py` | Sí | Descarga un recurso por URL |
| `sesco_processing.py` | **No** | Pipeline reutilizable CKAN → modelo → CSV |
| `run_mvp_processing.py` | **No** | CLI: procesa 6 recursos y exporta unificado |
| `__init__.py` | No | Paquete vacío |

### 4.2 `sesco_processing.py` — funciones principales

| Función / clase | Rol |
|-----------------|-----|
| `ProcessResult` | Dataclass con resultado por recurso |
| `normalize_text`, `fetch_ckan_package`, `list_resources`, `find_resource_by_name` | CKAN y matching de recursos |
| `infer_raw_filename`, `download_csv`, `read_csv` | I/O raw |
| `normalize_column_names`, `detect_*_column`, `find_production_column`, `find_group_column` | Detección de esquema |
| `build_model_df`, `preprocess_model_df` | Modelo analítico común |
| `detectar_periodo_incompleto`, `apply_valid_periods`, `get_valid_periods_by_group` | Períodos válidos (umbral 50 %) |
| `validate_resource_basic`, `validate_unified_dataset` | Validaciones |
| `export_model_clean`, `export_periodos_resumen`, `export_unified` | Exportación CSV |
| `build_totals_comparison_table` | Comparación entre vistas **sin sumarlas** |
| `process_resource`, `process_all_resources` | Orquestación MVP |

**Constantes relevantes:** `EXPORT_COLUMNS`, `DUPLICATE_KEY_COLUMNS`, `PACKAGE_ID`.

### 4.3 Coincidencia con documentación

- **README:** solo describe los dos scripts iniciales → **incompleto**.
- **Lógica no mencionada en README pero crítica:** exclusión de último período incompleto, clave de duplicados, export unificado, comparación inter-vistas.

### 4.4 `run_mvp_processing.py`

Equivalente por línea de comandos a la celda principal de `02_modelo_unificado_sesco.ipynb`:

```bash
python exploration/scripts/run_mvp_processing.py
```

(Desde `exploration/scripts/` o con `PYTHONPATH` adecuado.)

---

## 5. Revisión de datasets procesados

**Directorio:** `exploration/data/processed/`  
**Versionado git:** ignorado (`.gitignore`); archivos **presentes localmente** en la máquina auditada.

### 5.1 Tabla de archivos solicitados

| Archivo | Existe | Filas | Cols | Columnas principales | Descripción inferida | En `exploration/README.md` |
|---------|--------|-------|------|------------------------|----------------------|----------------------------|
| `gas_empresa_model_clean.csv` | Sí | 12 347 | 10 | `periodo_str`, `periodo_dt`, `anio`, `mes`, `producto`, `agrupador_tipo`, `agrupador_nombre`, `tipo_recurso`, `produccion`, `source_resource` | Producción diaria gas por empresa (limpia, períodos válidos) | No |
| `gas_empresa_periodos_resumen.csv` | Sí | 208 | 3 | `periodo_str`, `produccion_total`, `cantidad_agrupadores` | Totales por período | No |
| `gas_provincia_model_clean.csv` | Sí | 2 488 | 10 | (mismo esquema) | Gas por provincia | No |
| `gas_provincia_periodos_resumen.csv` | Sí | 207 | 3 | idem resumen | Resumen temporal gas provincia | No |
| `gas_cuenca_model_clean.csv` | Sí | 2 059 | 10 | idem modelo | Gas por cuenca | No |
| `gas_cuenca_periodos_resumen.csv` | Sí | 208 | 3 | idem resumen | Resumen gas cuenca | No |
| `petroleo_empresa_model_clean.csv` | Sí | 12 507 | 10 | idem modelo | Petróleo por empresa | No |
| `petroleo_empresa_periodos_resumen.csv` | Sí | 208 | 3 | idem resumen | Resumen petróleo empresa | No |
| `petroleo_provincia_model_clean.csv` | Sí | 2 498 | 10 | idem modelo | Petróleo por provincia | No |
| `petroleo_provincia_periodos_resumen.csv` | Sí | 204 | 3 | idem resumen | Resumen petróleo provincia | No |
| `petroleo_cuenca_model_clean.csv` | Sí | 2 013 | 10 | idem modelo | Petróleo por cuenca | No |
| `petroleo_cuenca_periodos_resumen.csv` | Sí | 203 | 3 | idem resumen | Resumen petróleo cuenca | No |
| `sesco_produccion_model_clean.csv` | Sí | 33 912 | 10 | idem modelo | **Unión** de los 6 `*_model_clean` | No |
| `sesco_validaciones_resumen.csv` | Sí | 6 | 13 | `resource_key`, `producto`, `agrupador_tipo`, conteos, `latest_valid_period`, `excluded_period`, … | Una fila por recurso MVP | No |
| `sesco_latest_periods_by_view.csv` | Sí | 6 | 3 | `producto`, `agrupador_tipo`, `latest_valid_period` | Último período válido por vista | No |
| `sesco_totales_por_vista_resumen.csv` | Sí | 416 | 7 | `producto`, `periodo_str`, `total_provincia`, `total_cuenca`, `total_empresa`, diffs % | Comparación lado a lado por período (no suma cruzada) | No |
| `sesco_dashboard_config.csv` | Sí | 6 | 5 | `producto`, `agrupador_tipo`, `periodo_min`, `latest_valid_period`, `cantidad_agrupadores` | Metadatos por vista para UI | No |
| `ckan_resources.csv` | Sí | 41 | 7 | `name`, `id`, `format`, `url`, … | Catálogo CKAN | Sí (implícito vía script inspect) |

**Comprobación de consistencia:** 2 498 + 2 488 + 2 013 + 2 059 + 12 507 + 12 347 = **33 912** = filas del unificado.

### 5.2 `sesco_validaciones_resumen.csv` (detalle por recurso)

| resource_key | latest_valid_period | excluded_period | Agrupadores | Negativos |
|--------------|---------------------|-----------------|-------------|-----------|
| petroleo_provincia | 2025-12 | 2026-01 | 15 | 0 |
| gas_provincia | 2026-03 | 2026-04 | 15 | 0 |
| petroleo_cuenca | 2025-11 | — | 17 | 0 |
| gas_cuenca | 2026-04 | 2026-05 | 17 | 0 |
| petroleo_empresa | 2026-04 | 2026-05 | 127 | 1 |
| gas_empresa | 2026-04 | 2026-05 | 127 | 0 |

---

## 6. Revisión del dataset unificado

**Archivo:** `exploration/data/processed/sesco_produccion_model_clean.csv`

### 6.1 Esquema y volumen

| Métrica | Valor |
|---------|-------|
| Filas | 33 912 |
| Columnas | 10 (`periodo_str`, `periodo_dt`, `anio`, `mes`, `producto`, `agrupador_tipo`, `agrupador_nombre`, `tipo_recurso`, `produccion`, `source_resource`) |
| Productos | `petroleo`, `gas` |
| `agrupador_tipo` | `provincia`, `cuenca`, `empresa` |
| `tipo_recurso` | `promedio_diario` (uniforme en muestra auditada) |
| Nulos | **0** en todas las columnas exportadas |
| Duplicados (`periodo_str`, `producto`, `agrupador_tipo`, `agrupador_nombre`, `tipo_recurso`) | **0** |
| `produccion` negativa | **1** registro |

### 6.2 Período mínimo y máximo por `producto` + `agrupador_tipo`

| producto | agrupador_tipo | min | max |
|----------|----------------|-----|-----|
| gas | cuenca | 2009-01 | 2026-04 |
| gas | empresa | 2009-01 | 2026-04 |
| gas | provincia | 2009-01 | 2026-03 |
| petroleo | cuenca | 2009-01 | 2025-11 |
| petroleo | empresa | 2009-01 | 2026-04 |
| petroleo | provincia | 2009-01 | 2025-12 |

Los máximos por vista coinciden con `sesco_latest_periods_by_view.csv` (tras exclusión de períodos incompletos en el pipeline).

### 6.3 Registro con producción negativa

| Campo | Valor |
|-------|-------|
| periodo | 2018-10 |
| producto / agrupador | petróleo / empresa |
| agrupador_nombre | PETROFARO S.A. |
| produccion | -0.000323 |

Origen en fuente SESCO; no se filtra en `preprocess_model_df` (solo se eliminan nulos en columnas clave).

### 6.4 Coincidencia con lo documentado

- Si el documento externo describe un **modelo largo unificado** con trazabilidad `source_resource` y 6 vistas → **coincide**.
- `exploration/README.md` **no menciona** este archivo → brecha de documentación versionada.

---

## 7. Revisión de archivos auxiliares

### 7.1 `sesco_latest_periods_by_view.csv`

| Aspecto | Detalle |
|---------|---------|
| Contenido | Último período válido por combinación `producto` + `agrupador_tipo` |
| Uso en Streamlit | **Sí** — función `get_latest_valid_period()` en `app.py` |
| Documentación README | No |

### 7.2 `sesco_dashboard_config.csv`

| Aspecto | Detalle |
|---------|---------|
| Contenido | `periodo_min`, `latest_valid_period`, `cantidad_agrupadores` por vista |
| Uso en Streamlit | **Cargado** en `load_data()` pero **`config_df` no se usa** en el resto de `app.py` |
| Utilidad potencial | Filtros, tooltips, validación de cobertura sin recalcular |
| Documentación README | No |

### 7.3 `sesco_totales_por_vista_resumen.csv`

| Aspecto | Detalle |
|---------|---------|
| Contenido | Por `producto` y `periodo_str`, totales paralelos `total_provincia`, `total_cuenca`, `total_empresa` y diferencias porcentuales vs provincia |
| Uso en Streamlit | **No** referenciado en `app.py` |
| Utilidad | Auditoría / QA de cobertura entre vistas (notebook 03) |
| Documentación README | No |

**Conclusión auxiliares:** sirven para Streamlit **en parte**. Solo `sesco_latest_periods_by_view.csv` está integrado operativamente. Si la documentación externa afirma que los tres alimentan el dashboard, es **imprecisa** respecto al código actual.

---

## 8. Revisión del dashboard Streamlit

**Archivo:** `exploration/streamlit_app/app.py`  
**Ejecución documentada:** `streamlit run exploration/streamlit_app/app.py`

### 8.1 CSV leídos

| Archivo | ¿Leído? | ¿Usado? |
|---------|---------|---------|
| `sesco_produccion_model_clean.csv` | Sí | Sí (tabla base) |
| `sesco_latest_periods_by_view.csv` | Sí | Sí (KPIs y rankings) |
| `sesco_dashboard_config.csv` | Sí | **No** (variable sin uso) |

### 8.2 Filtros (sidebar)

- `producto` (selectbox)
- `agrupador_tipo` (selectbox, dependiente de producto)
- `agrupador_nombre` (multiselect, default hasta 8)
- Rango de períodos (slider sobre datos filtrados)
- Top N para ranking (5, 10, 15, 20)

### 8.3 KPIs

1. Último período válido (desde `latest_df`)
2. Total último período (suma de `produccion` en slice válido)
3. Principal agrupador (máximo en último período)
4. Variación % vs período anterior inmediato
5. Cantidad de agrupadores seleccionados

### 8.4 Gráficos

| Gráfico | Tipo | Notas |
|---------|------|-------|
| Evolución mensual | Línea Plotly | Suma `produccion` por período dentro de **una** vista (`producto` + `agrupador_tipo`) |
| Ranking Top N | Barras horizontales | Último período válido por vista |
| Top 5 evolución | Líneas múltiples | Últimos 12 períodos ≤ último válido |

### 8.5 Reglas metodológicas en UI

- Texto en “Notas metodológicas” alinea con notebooks 03 (no sumar vistas, período válido por vista, productos separados).
- Filtro obligatorio de `producto` y `agrupador_tipo` antes de agregar → **evita mezclar provincia+cuenca+empresa**.
- **No** mezcla petróleo y gas en un mismo gráfico (usuario elige un producto).
- Usa `latest_valid_period` externo por vista, coherente con pipeline.

### 8.6 vs documentación

| Tema | README | Código |
|------|--------|--------|
| Existencia del dashboard | Mencionado (instalación manual streamlit/plotly) | Implementado |
| Archivos de entrada | No detalla | 3 CSV, 1 sin uso |
| KPIs y gráficos | No detalla | Implementación completa MVP |

---

## 9. Validación de reglas metodológicas

| Regla | Estado | Evidencia |
|-------|--------|-----------|
| No sumar provincia + cuenca + empresa para totales | **Aplicada** | `build_totals_comparison_table` documenta no sumar; notebook 02 y Streamlit agregan dentro de un solo `agrupador_tipo`; notas en UI |
| Usar último período válido por `producto` + `agrupador_tipo` | **Aplicada** | `detectar_periodo_incompleto` / `get_valid_periods_by_group`; `sesco_latest_periods_by_view.csv`; Streamlit `get_latest_valid_period` |
| Excluir períodos incompletos | **Aplicada** | Umbral 50 % vs período anterior; varios `excluded_period` en `sesco_validaciones_resumen.csv` |
| No comparar petróleo y gas en el mismo eje como equivalentes | **Aplicada** | Streamlit: un producto por sesión; notebook 02 usa base 100 para comparación tendencial |
| Conservar trazabilidad vía `source_resource` | **Aplicada** | Columna en modelo, export y tabla Streamlit |
| No convertir nulos a cero automáticamente en producción | **Aplicada** (con matiz) | `preprocess_model_df` hace `dropna` en `produccion`, no `fillna(0)`. `fillna` solo en construcción de `periodo_str` desde año/mes |

**Matiz:** un valor negativo infinitesimal permanece en datos (no se fuerza a cero).

---

## 10. Diferencias detectadas

### 10.1 Documentación correcta (o mayormente correcta)

- Objetivo general de exploration: entender datos SESCO antes de backend/frontend (`exploration/README.md`, `README.md` raíz).
- Fuente oficial CKAN / datos.gob.ar.
- Scripts `inspect_ckan_resources.py` y `download_sesco_resource.py` y su propósito.
- Existencia de un MVP Streamlit y comando de ejecución.
- Datos raw/processed no versionados en git.

### 10.2 Documentación incompleta

- Notebooks `02` y `03` y su orden en el flujo.
- Módulo `sesco_processing.py` y `run_mvp_processing.py`.
- Los 12 CSV por recurso + 4 CSV globales/auxiliares.
- Reglas metodológicas y umbral de período incompleto.
- `requirements.txt` sin `streamlit` ni `plotly`.
- Uso real vs nominal de CSV auxiliares en Streamlit.

### 10.3 Documentación imprecisa

- Diagrama de estructura (solo un notebook).
- Si el documento externo indica que **todos** los auxiliares alimentan Streamlit → impreciso (`config` cargado pero no usado; `totales` no integrado).
- README raíz lista `data/` en raíz del repo; los datos MVP están bajo `exploration/data/`.

### 10.4 Existe en el proyecto, no documentado en README versionado

- Pipeline completo de 6 recursos.
- Dataset unificado y validaciones.
- Notebook de validación final y exports auxiliares.
- Comparación inter-vistas (`sesco_totales_por_vista_resumen.csv`).
- Archivo `exploration/notebooks/_out.txt`.
- Entorno `.venv` local bajo `exploration/`.

### 10.5 Documentado pero no existe / no verificable

| Ítem | Notas |
|------|-------|
| Documento titulado *"Monitor Hidrocarburífero SESCO"* | No localizado en repo; no se pudo contrastar frase por frase |
| `exploration/scripts/` solo con 2 scripts | **Incorrecto** en README: hay 4 scripts útiles |
| Un solo notebook | **Incorrecto** en README |

### 10.6 Recomendaciones de actualización (síntesis)

Ver sección 11.

---

## 11. Recomendaciones para actualizar la documentación

1. **Reescribir `exploration/README.md`** con el árbol real, los 3 notebooks en orden y el diagrama de flujo: CKAN → raw → `run_mvp_processing` o nb02 → nb03 → Streamlit.
2. **Documentar `sesco_processing.py`** como fuente de verdad del modelo (columnas `EXPORT_COLUMNS`, clave de duplicados, umbral 50 %).
3. **Tabla de artefactos en `data/processed/`** con nombre, filas esperadas (~), generador (notebook/script) y consumidor (Streamlit / solo QA).
4. **Aclarar integración Streamlit:** qué CSV usa cada KPI; marcar `sesco_dashboard_config.csv` como *pendiente de uso* o implementar su uso; indicar que `sesco_totales_por_vista_resumen.csv` es para validación, no para UI.
5. **Añadir `streamlit` y `plotly`** a `exploration/requirements.txt` o un `requirements-streamlit.txt`.
6. **Incorporar el documento externo** al repo (p. ej. `docs/exploration-sesco.md`) si es la referencia oficial del equipo.
7. **Documentar el registro negativo** y la política (filtrar, abs, mantener).
8. **Unificar nombres de archivos raw** o documentar la convención `infer_raw_filename`.
9. **Próximos pasos:** alinear con `docs/mvp-plan.md` (API, ETL backend) indicando qué partes de exploration ya cubren el MVP de visualización.

---

## 12. Conclusión

| Pregunta | Respuesta |
|----------|-----------|
| ¿La documentación versionada (`exploration/README.md`) refleja bien `exploration/`? | **No.** Describe una fase inicial (~notebook 01 + 2 scripts). El proyecto real es un pipeline analítico completo con unificado, validación y Streamlit. |
| ¿El estado del código/datos coincide con un documento externo tipo “Monitor Hidrocarburífero SESCO” (según checklist del encargo)? | **Probablemente sí en lo sustancial**, salvo detalles de integración de CSV auxiliares en Streamlit y dependencias no pinneadas. **No se pudo verificar** el archivo externo literal por ausencia en el repo. |
| ¿Qué corregir? | README de exploration, diagrama de carpetas, inventario de scripts/notebooks, tabla de CSV, uso real de auxiliares en Streamlit. |
| ¿Qué agregar? | Reglas metodológicas, flujo nb01→02→03, esquema del unificado, comandos CLI `run_mvp_processing.py`, validaciones y períodos excluidos por recurso. |
| ¿Qué está bien? | Implementación técnica coherente con las reglas declaradas en notebook 03 y notas de Streamlit; dataset unificado consistente; trazabilidad `source_resource`; separación de productos y vistas en la UI. |

**Veredicto:** el **proyecto real está más avanzado que la documentación versionada**. Priorizar actualizar `exploration/README.md` (o añadir `docs/exploration-sesco.md`) antes de retomar `backend/`/`frontend/`, usando este informe como checklist de verificación.

---

*Informe generado por auditoría estática y análisis local de CSV. No se modificaron código, notebooks, CSV ni README del proyecto.*
