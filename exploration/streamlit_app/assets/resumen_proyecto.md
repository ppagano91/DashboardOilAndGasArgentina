# Monitor de Producción Hidrocarburífera Argentina — SESCO

Este proyecto es un tablero exploratorio desarrollado en Python y Streamlit para visualizar información pública de producción de petróleo y gas de Argentina.

## Fuente de datos

Los datos provienen del dataset oficial "Producción de Petróleo y Gas (SESCO)", publicado en datos.gob.ar. La metadata de recursos se consulta mediante la API CKAN de datos.gob.ar para obtener las URLs vigentes de descarga.

## Objetivo

El objetivo del proyecto es permitir una lectura rápida de la evolución mensual de producción de petróleo y gas, rankings por provincia, cuenca y empresa, y visualizaciones geográficas por cuencas sedimentarias.

## Alcance actual

El dashboard permite:
- consultar indicadores principales;
- filtrar por producto, agrupador y período;
- visualizar evolución temporal;
- analizar rankings;
- explorar producción por cuenca en un mapa;
- descargar datos procesados desde la aplicación, si está disponible.

## Metodología general

Los datos oficiales se descargan, normalizan y validan localmente. Las vistas por provincia, cuenca y empresa se tratan como formas alternativas de agrupar el mismo fenómeno y no se suman entre sí.

## Tecnologías utilizadas

- Python
- pandas
- Streamlit
- Plotly
- GeoPandas / Shapely
- CKAN API
- GitHub

## Autor

Patricio Pagano
