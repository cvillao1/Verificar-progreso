# Descargar certificados de EduQairos

Programa separado del seguimiento de porcentajes. Lee la hoja sin modificarla y guarda un ZIP por alumno con su nombre completo. Comprueba los cinco certificados de módulos y el certificado final mediante el texto del PDF o el OCR local de Windows. No basta con que haya seis archivos.

## Preparación en Windows

Desde la carpeta del programa:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe descargar_certificados.py init-config
```

Conserva `client_secret.json` local del proyecto Google existente. La autorización solicita solo lectura de Sheets. No se guardan contraseñas, cookies ni tokens de acceso en el repositorio o en el avance.

En `config_certificados.json`, indica el ID de tu hoja y comprueba estas opciones:

| Opción | Uso |
| --- | --- |
| `authorized` | `true` cuando tienes autorización para descargar los certificados |
| `allow_final_certificate` | `true` si autorizas obtener el final faltante de alumnos con curso completo |
| `start_number` | `123`: número de la columna A, **no** fila de Sheets; actualmente corresponde a fila 124 |
| `expected_students` | `489`: cantidad leída del listado actual; si esperabas 491, comprueba los dos registros faltantes |
| `download_directory` | Ruta completa de Descargas → `RODRIGO TRIVIÑO` |
| `timeout_ms` | `60000`: espera de página y controles |
| `download_timeout_ms` | `180000`: espera de descarga |

Columnas esperadas: A número, B cédula como texto con diez dígitos, C nombre, D correo, E contraseña. Se rechazan identidades duplicadas o incompletas. No cambies los datos ni el orden del listado durante una ejecución.

**El programa antiguo de porcentajes usa otra distribución de columnas. No lo ejecutes sobre esta nueva distribución sin adaptarlo.** Este programa de certificados no lo modifica.

## Primera descarga y lote completo

Para comprobar la descarga del número 123 sin preguntas de «VALIDADO»:

```powershell
.\.venv\Scripts\python.exe descargar_certificados.py run --limit 1 --pause-on-error
```

Autoriza la lectura en Google e inicia sesión como administrador en el navegador que se abre. El programa detectará «Cerrar Sesión» y continuará automáticamente. Mantén esa ventana abierta.

Después de revisar el primer ZIP, para procesar desde el 123 hasta el final:

```powershell
.\.venv\Scripts\python.exe descargar_certificados.py run --pause-on-error
```

Los ZIP existentes completos se omiten, sin sobrescribirlos. Para un único alumno del lote guardado:

```powershell
.\.venv\Scripts\python.exe descargar_certificados.py run --only-number 123 --pause-on-error
```

Puedes cambiar `start_number` antes de iniciar un lote nuevo. Durante un lote pendiente debes conservar el inicio y `.certificados-state`; la reanudación identifica las cuentas por cédula, no por posición.

## Cuando solo hay cinco certificados

Primero se descarga y comprueba que sean los cinco módulos distintos. Se abre una sesión de alumno aislada usando el correo y contraseña de la hoja. Se verifica nombre y correo del perfil, el 100% y las cinco lecciones completadas. Solo entonces se pulsa «Obtener Certificado», se recoge el PDF descargado y se comprueba que el administrador muestre seis certificados. El ZIP final contendrá los cinco módulos y el final.

El inicio de sesión y los campos del perfil deben calibrarse con la interfaz real. Las opciones `selectors.student_*` y `selectors.profile_*` son controles iniciales pendientes de confirmar en una ejecución real. Si no coinciden, se detiene antes de emitir. Las pruebas locales verifican el comportamiento con páginas simuladas; no sustituyen esta comprobación real. No se han emitido certificados durante las pruebas de desarrollo.

El OCR exige nombre y tipo de certificado reconocibles. Si falla por diseño, ortografía o calidad del PDF, se detiene para revisar; no acepta nombres parecidos ni inventa certificados. Necesita el OCR de Windows con un idioma instalado. Los PDF permanecen en el equipo.

La búsqueda empieza por el nombre completo tal como aparece en la hoja. Solo si no obtiene una lista completa del alumno, prueba nombres o apellidos por separado, prefiriendo palabras sin caracteres especiales para evitar fallos del buscador con la ñ. Después exige todos los nombres y apellidos completos, aunque aparezcan en otro orden. Esta comprobación también se aplica a los PDF y al perfil; no acepta apellidos distintos o faltantes. El ZIP conserva el nombre de la hoja.

## Interrupciones y recuperación

Repite el mismo comando. La ejecución empieza directamente en el primer alumno pendiente: no vuelve a procesar los completados, no abre sus ZIP ni espera entre ellos. Solo comprueba en el avance que siguen completos y que su archivo existe. Si borraste un ZIP, vuelve a quedar pendiente. El avance se conserva en `.certificados-state/progreso.json`, con identificadores y huellas, sin correos ni contraseñas. No borres esa carpeta mientras el lote esté pendiente. Si modificaste manualmente un ZIP completado, debes revisarlo por separado.

Si falla el reconocimiento de los seis PDF, el ZIP sin aprobar se conserva en `.certificados-state/revision-NUMERO.zip` para diagnosticarlo. No se guarda como certificado completo ni se salta al alumno. Estos archivos son privados y están excluidos de Git.

Si ya se pulsó «Obtener Certificado» pero se perdió la conexión, en la siguiente ejecución se vuelve a consultar el administrador. Si hay seis, se descargan; si aún hay cinco, no se repite la emisión automáticamente: revisa la cuenta para evitar duplicados. Una discrepancia de identidad requiere corregir el dato real en la hoja.

## Archivos

Necesarios: `descargar_certificados.py`, `seguimiento_global.py` (funciones auxiliares), `ocr_certificados.ps1`, `config_certificados.json`, `client_secret.json` y las dependencias. Las plantillas y `test_certificados.py` pueden publicarse. La configuración privada, las credenciales, el avance y los ZIP/PDF están excluidos de Git.

Pruebas locales, sin acceder a cuentas reales:

```powershell
.\.venv\Scripts\python.exe -m unittest test_certificados -v
```
