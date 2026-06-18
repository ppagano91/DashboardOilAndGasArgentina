# CKAN Explorer — datos.gob.ar

Herramienta interna de exploración para el proyecto **Monitor de Producción Hidrocarburífera Argentina (SESCO)**.

Permite consultar la [Action API CKAN](https://datos.gob.ar/api/3/action) desde el navegador, visualizar respuestas JSON y facilitar el trabajo de exploración y mantenimiento del ETL.

> **No reemplaza** el portal oficial [datos.gob.ar](https://datos.gob.ar).  
> **No es** un Swagger ni documentación oficial de la API.  
> Es una utilidad de desarrollo local para entender qué devuelve cada endpoint que usa el proyecto.

## Objetivo

- Probar endpoints CKAN sin escribir scripts ad hoc.
- Obtener IDs de datasets y resources (p. ej. `energia-produccion-petroleo-gas-sesco`).
- Inspeccionar URLs de descarga de CSV antes de integrarlas al pipeline.
- Generar snippets Python con `requests` para reproducir consultas.

## Endpoints soportados

| Acción | Descripción breve |
|--------|-------------------|
| `status_show` | Estado de la API |
| `package_search` | Búsqueda de datasets |
| `package_show` | Detalle de un dataset |
| `package_list` | Lista de datasets |
| `resource_show` | Detalle de un resource |
| `resource_search` | Búsqueda de resources |
| `organization_list` | Lista de organismos |
| `organization_show` | Detalle de un organismo |
| `group_list` | Lista de grupos/categorías |
| `group_show` | Detalle de un grupo |
| `tag_list` | Lista de etiquetas |
| `package_autocomplete` | Autocompletado de datasets |
| `organization_autocomplete` | Autocompletado de organismos |
| `group_autocomplete` | Autocompletado de grupos |
| `tag_autocomplete` | Autocompletado de etiquetas |

## Cómo ejecutarla

Desde la raíz del repositorio:

```bash
pip install streamlit requests pandas
streamlit run exploration/ckan_explorer/app.py
```

La app abre en el navegador (por defecto `http://localhost:8501`).

## Ejemplos de consultas

### Dataset SESCO (flujo típico)

1. **package_show** con `dataset_id = energia-produccion-petroleo-gas-sesco`  
   → lista de resources con URLs de descarga.

2. **package_search** con `q = sesco` y `rows = 10`  
   → datasets relacionados con SESCO.

3. **resource_search** con `query = name:sesco`  
   → resources cuyo nombre contiene “sesco”.

### Otros ejemplos

- **status_show** — verificar que la API responde.
- **organization_show** con `id = energia` — organismo publicador del dataset energético.
- **package_autocomplete** con `q = petroleo` — sugerencias de datasets.

## Flujo recomendado para SESCO

```
package_search → package_show → resources[] → CSV → pandas
```

1. Buscar o abrir directamente el dataset SESCO con `package_show`.
2. Revisar la tabla de `resources` y copiar la URL del CSV deseado.
3. Descargar y procesar con el pipeline existente (`sesco_processing.py`, notebooks, etc.).

## Notas

- Timeout de red: 20 segundos.
- No requiere autenticación (API pública).
- No persiste resultados en base de datos.
- No modifica el pipeline SESCO ni el dashboard Streamlit principal.
