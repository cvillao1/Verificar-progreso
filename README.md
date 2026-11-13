# Seguimiento global de EduQairos en Windows

Consulta el porcentaje global de un curso desde la interfaz administrativa autorizada y lo guarda en Google Sheets. Busca cada cuenta por el correo de C y exige que coincidan el correo y el nombre de B. A conserva la cédula de la fila. Escribe solo en L, con encabezado `PROGRESO GLOBAL EDUQAIROS`; conserva F–J y la fórmula K.

El programa no obtiene los estados individuales de las cinco lecciones ni los deduce del porcentaje o los certificados. No utiliza una API de EduQairos ni exporta cookies o tokens de sesión. Requiere inicio de sesión manual en cada ejecución y autorización para automatizar estas consultas.

**La ejecución normal comienza directamente, sin pilotos ni preguntas de validación.** Autoriza Google e inicia sesión en EduQairos; el programa detecta la sesión abierta y continúa sin pedir Enter en la terminal. Las comprobaciones de identidad, curso y porcentaje siguen siendo automáticas.

## Archivos que deben estar en GitHub

```text
seguimiento_global.py       Programa independiente: consulta todos o desde una fila
config_global.example.json  Plantilla sin datos de tu hoja ni alumnos
requirements.txt            Dependencias de Python
README.md                   Esta guía
.gitignore                  Exclusiones de archivos locales
test_global.py              Pruebas de selección, datos y escritura acotada
test_global_ui.py           Prueba de navegador con páginas simuladas
```

En esta distribución `seguimiento_global.py` contiene las funciones auxiliares y no requiere `seguimiento.py`. La versión anterior de trabajo sí lo importaba; no borres `seguimiento.py` de una instalación antigua mientras siga usando aquella versión.

`seguimiento_global.antes-*.py`, `seguimiento_global.version-*.py` y `config_global.antes-*.json` son respaldos locales de cambios anteriores. No son programas adicionales que debas ejecutar. `.venv` contiene las librerías, `__pycache__` archivos temporales de Python y `.qodo` pertenece a una herramienta del editor. No necesitas subirlos.

## Instalación desde cero

En PowerShell abierto dentro de esta carpeta, con Python 3.12 o posterior instalado:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe seguimiento_global.py init-config
```

`init-config` copia la plantilla a `config_global.json` si no existe. Nunca reemplaza una configuración existente. No hace falta activar el entorno ni cambiar la política de PowerShell.

Prepara un cliente OAuth de escritorio en un proyecto de Google Cloud con Google Sheets API y Google Drive API habilitadas. Configura tu cuenta como usuario de prueba si corresponde. Guarda el archivo descargado únicamente en tu equipo como `client_secret.json`, al lado del programa. Referencia: [guía oficial de Google](https://developers.google.com/workspace/sheets/api/quickstart/python).

## Configuración local

Edita `config_global.json`, no el archivo de ejemplo:

- `authorized`: pon `true` solo si tu cuenta y las condiciones del sitio permiten las consultas automatizadas.
- `spreadsheet_id`: ID de tu hoja, la parte de su URL entre `/d/` y `/edit`.
- `sheet_id` y `sheet_name`: ID numérico y nombre exacto de la pestaña autorizada.
- `expected_students`: cantidad total del listado; en la instalación original, 489. No la reduzcas a 245 para consultar un tramo.
- `last_row`: límite máximo de lectura de la hoja, no fila inicial. Se conserva en 999 si la pestaña tiene al menos esas filas.
- `course_title`: título exacto del curso.
- `delay_seconds`: duración de cada pausa, actualmente 1 segundo. El flujo habitual recarga la página para cada alumno y conserva ambas pausas.
- `login_timeout_ms`: tiempo para iniciar sesión, por defecto 300000 milisegundos (5 minutos).
- `login_ready`: control visible que indica sesión iniciada; por defecto, el texto exacto `Cerrar Sesión` observado en el sitio. No pulsa ese control.
- `selectors`: controles de la interfaz. La plantilla contiene los observados en la instalación original; pueden necesitar ajustes si cambia la plataforma.

Si tu configuración anterior contiene `pilot_rows`, se ignora; ya no tienes que mantener esos números ni elegir alumnos piloto.

La columna L debe estar vacía o contener el encabezado exacto y porcentajes numéricos compatibles. Si cambias L1 por una fecha u otro título, el programa se detiene para no sobrescribir otra columna. Pon la hora de actualización en una nota o fuera de L.

```powershell
.\.venv\Scripts\python.exe seguimiento_global.py check-sheet
```

Autoriza Google con la cuenta que tiene permiso Editor. Este comando comprueba A–C, la cantidad total de alumnos, cédulas y correos únicos, L y permisos sin modificar la hoja. Nunca lee la columna D de contraseñas. No guarda `token.json`; las credenciales de Google permanecen en memoria.

## Revisar todos los alumnos

```powershell
.\.venv\Scripts\python.exe seguimiento_global.py all --write
```

Tras autorizar Google e iniciar sesión en EduQairos, consulta a todos en orden de fila sin pedir confirmaciones de porcentajes. Mantiene las comprobaciones exactas de identidad y curso. Una identidad ausente o ambigua, o un porcentaje ausente, detienen la ejecución; no los convierte en 0%.

## Comenzar desde la fila 246 hacia abajo

No necesitas modificar el código ni buscar un número de línea. **Cambia el número que sigue a `--from-row` en el comando**:

```powershell
.\.venv\Scripts\python.exe seguimiento_global.py all --from-row 246 --write
```

Incluye la fila 246 y todas las posteriores con alumno, hasta el límite configurado. Si la última fila de alumnos es 490, consulta 245 alumnos. No consulta cuentas de filas anteriores. La lectura de A–C sigue abarcando el listado para validar identidades y detectar cambios; esto no equivale a consultar esas cuentas en EduQairos.

Ejemplo para comenzar desde otra fila:

```powershell
.\.venv\Scripts\python.exe seguimiento_global.py all --from-row 300 --write
```

`--from-row` es un número de fila de Sheets, no un porcentaje, contador de alumnos ni fila de un archivo Excel distinto. No se configura en el código ni en `last_row`. Sin esa opción, `all` revisa todo el listado. Solo puede combinarse con `all`.

La selección es por posición, **no omite automáticamente los 100% dentro del tramo**. Si vuelves a ordenar, comprueba dónde comienzan los pendientes y cambia ese número. El modo experimental `pending` no forma parte de esta versión. Para simular el tramo, quita `--write`.

## Durante la ejecución

Mantén el equipo encendido y las ventanas abiertas. No edites ni ordenes la hoja mientras consulta. Al terminar, vuelve a comprobar A–C y L y aplica los resultados del lote en una actualización. Existe una pequeña ventana entre la comprobación final y la escritura: evita cambios concurrentes.

Los resultados se guardan **al finalizar el lote**, no alumno por alumno. Si se detiene antes de escribir, los nuevos resultados de ese intento no se conservan ni se reanudan automáticamente. Si falla durante o después del envío, revisa L antes de repetir: la actualización puede haberse aplicado.

`--pause-on-error` mantiene el navegador abierto para ver la ficha. La terminal muestra el paso del fallo; pulsa Enter cuando termines de revisarlo para cerrar y obtener el mensaje final. No hay reintentos automáticos. La pausa de un segundo puede aumentarse si el sitio o la red necesitan más tiempo; no hay consultas paralelas.

## Preparar el repositorio

Publica esta carpeta limpia, no la instalación completa de Downloads. No subas `client_secret.json`, `config_global.json`, `config.json`, respaldos de configuración, `.venv`, sesiones, capturas ni listas de alumnos. `.gitignore` excluye los archivos locales más comunes. Los únicos JSON del repositorio deben ser plantillas revisadas sin secretos o información privada.

Puedes preparar los archivos con una lista explícita:

```powershell
git init
git add .gitignore README.md requirements.txt config_global.example.json seguimiento_global.py test_global.py test_global_ui.py
git status --short
```

Revisa los nombres antes de crear un commit o publicar. Si un archivo privado ya estaba incluido en Git, añadirlo a `.gitignore` no lo elimina del seguimiento ni del historial. Referencia: [ignorar archivos en GitHub](https://docs.github.com/en/get-started/git-basics/ignoring-files). Esta entrega no crea ni publica un repositorio.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_global.py
.\.venv\Scripts\python.exe -m unittest -v test_global_ui.py
```

La segunda prueba necesita Chromium y usa páginas simuladas con datos ficticios. Las pruebas no consultan el sitio ni Google Sheets y no se ejecutan durante una actualización normal. Revisa muestras de resultados al configurar la instalación o si cambia la interfaz.
