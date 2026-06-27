# Monitor de Producción Hidrocarburífera Argentina — SESCO

Este proyecto es un tablero exploratorio desarrollado en Python y Streamlit para visualizar información pública de producción de petróleo y gas de Argentina.

El sector hidrocarburífero publica mensualmente estadísticas oficiales a través del Sistema de Estadísticas de Crudos y Productos (SESCO). Esos datos están disponibles en datos.gob.ar, pero distribuidos en varias tablas según provincia, cuenca sedimentaria y empresa operadora. Para quien quiere seguir la evolución del sector —analistas, estudiantes, periodistas o equipos técnicos— recorrer cada recurso por separado resulta poco práctico.

Este dashboard reúne esas fuentes en un único entorno interactivo, con el fin de ofrecer una lectura más directa de la producción nacional y sus tendencias recientes.

## Fuente de datos

Los datos provienen del dataset oficial "Producción de Petróleo y Gas (SESCO)", publicado en datos.gob.ar. La metadata de recursos se consulta mediante la API CKAN de datos.gob.ar para obtener las URLs vigentes de descarga.

## Objetivo

El objetivo del proyecto es facilitar el seguimiento de la producción hidrocarburífera argentina a partir de datos oficiales, priorizando claridad y exploración rápida.

El tablero está pensado para responder preguntas frecuentes sin necesidad de procesar los CSV originales: ¿cómo evolucionó la producción de petróleo o gas en los últimos meses?, ¿qué provincias o cuencas concentran mayor volumen?, ¿cómo se distribuye la producción entre empresas operadoras? Además, el mapa de cuencas sedimentarias ayuda a vincular esos números con la geografía del país.

No pretende reemplazar los informes oficiales del SESCO ni cubrir el detalle por pozo, área o yacimiento. Su aporte está en integrar los agregados mensuales más consultados en una vista filtrable, comparable y descargable.

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
