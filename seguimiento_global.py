"""Seguimiento global por interfaz administrativa. Destino exclusivo: columna L."""
import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from contextlib import contextmanager, nullcontext

class StopRun(Exception):
    pass

def require(ok, message):
    if not ok:
        raise StopRun(message)

def norm(value):
    return " ".join(str(value).split()).casefold()

def cedula(value):
    # No convertir a entero ni completar ceros: una identidad ambigua se rechaza.
    require(isinstance(value, str) and bool(re.fullmatch(r"[0-9]{10}", value.strip())),
            "Cédula inválida: debe ser texto de 10 dígitos. Revisar la fila indicada.")
    return value.strip()

def unique(locator, label):
    require(locator.count() == 1, f"Selector {label}: se esperaba exactamente un elemento.")
    require(locator.is_visible(), f"Selector {label}: elemento no visible.")
    return locator

class Sheet:
    def values(self, area, option="FORMATTED_VALUE"):
            return self.api.spreadsheets().values().get(spreadsheetId=self.sid, range=self.prefix + area,
                        valueRenderOption=option).execute().get("values", [])

    def write(self, requests):
            self.api.spreadsheets().batchUpdate(spreadsheetId=self.sid, body={"requests": requests}).execute()

HEADER = "PROGRESO GLOBAL EDUQAIROS"
CONFIG = Path("config_global.json")
URL = "https://eduqairos.com/admin/estudiantes"
LAST_STEP = "inicio"
STATE_PATH = Path(".eduqairos-state/avance.json")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@contextmanager
def state_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise StopRun("Ya hay otra ejecución usando el archivo de recuperación. Cierra esa ejecución antes de continuar.") from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Progress:
    """Registro local atómico. Sin identificaciones personales ni credenciales en texto."""
    def __init__(self, path, cfg, identities, first_row=None):
        self.path = Path(path)
        scope = digest([cfg["spreadsheet_id"], cfg["sheet_id"], norm(cfg["course_title"]), URL])
        previous = None
        if self.path.exists():
            try:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise StopRun("No se pudo leer el archivo de recuperación. Consérvalo para revisarlo; no se reemplazó.") from None
            require(isinstance(previous, dict) and previous.get("version") == 1
                    and type(previous.get("complete")) is bool, "Archivo de recuperación no reconocido; no se reemplazó.")
        self.resumed = bool(previous and not previous["complete"])
        if self.resumed:
            require(previous.get("scope") == scope, "Hay una ejecución pendiente para otra hoja o curso. Conserva su archivo de recuperación.")
            require(first_row is None or previous.get("first_row") == first_row,
                    "Hay una ejecución pendiente con otro inicio. Repite el comando anterior o usa all --write sin --from-row para reanudarla.")
            self.data = previous
            self.validate_file()
        else:
            start = first_row if first_row is not None else 2
            selected = select_from_row(identities, start, cfg["last_row"])
            self.data = {"version":1, "scope":scope, "salt":secrets.token_hex(16),
                         "first_row":start, "queue":[], "results":{}, "complete":False}
            self.data["queue"] = [self.key(identities[r]) for r in selected]
            self.save()
        self.mapping(identities)

    def validate_file(self):
        d = self.data
        valid_key = lambda k: isinstance(k,str) and re.fullmatch(r"[0-9a-f]{64}",k) is not None
        require(isinstance(d.get("salt"),str) and re.fullmatch(r"[0-9a-f]{32}",d["salt"]) is not None
                and isinstance(d.get("queue"),list) and d["queue"] and all(valid_key(k) for k in d["queue"])
                and len(set(d["queue"])) == len(d["queue"]) and isinstance(d.get("results"),dict),
                "Estructura del archivo de recuperación inválida.")
        for key,result in d["results"].items():
            require(key in d["queue"] and isinstance(result,dict) and valid_key(result.get("identity"))
                    and type(result.get("value")) in {int,float} and 0 <= result["value"] <= 1
                    and type(result.get("applied")) is bool and isinstance(result.get("before"),dict),
                    "Resultado local inválido; no se enviará a Sheets.")
            before = result["before"]
            require(not before or (set(before) == {"numberValue"} and type(before["numberValue"]) in {int,float}
                                   and 0 <= before["numberValue"] <= 1), "Valor anterior de recuperación inválido.")

    def key(self, identity):
        return digest([self.data["salt"], identity["cedula"]])

    def fingerprint(self, identity):
        return digest([self.data["salt"], identity["cedula"], identity["name"], identity["email"]])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def mapping(self, identities):
        rows = {self.key(identity):row for row,identity in identities.items()}
        require(all(key in rows for key in self.data["queue"]),
                "Falta un alumno de la ejecución pendiente o cambió su cédula. Revisa la correspondencia antes de reanudar.")
        changed = False
        for key,result in list(self.data["results"].items()):
            row = rows[key]
            if result["identity"] != self.fingerprint(identities[row]):
                print(f"Fila {row}: cambió el nombre o correo; se consultará de nuevo y no se reutilizará el porcentaje anterior.")
                del self.data["results"][key]
                changed = True
        if changed:
            self.save()
        return rows

    def remaining(self, identities):
        rows = self.mapping(identities)
        return [rows[key] for key in self.data["queue"] if key not in self.data["results"]]

    def record(self, row, value, identity, before):
        require(type(value) in {int,float} and 0 <= value <= 1, "Porcentaje inválido; no se guardó localmente.")
        key = self.key(identity)
        require(key in self.data["queue"], "Alumno fuera de la selección original.")
        self.data["results"][key] = {"value":value, "identity":self.fingerprint(identity),
                                     "before":before, "row_hint":row, "applied":False}
        self.save()

    def flush(self, sheet):
        pending = [k for k,v in self.data["results"].items() if not v["applied"]]
        if not pending:
            return 0
        identities = sheet.identities()
        rows = self.mapping(identities)
        pending = [k for k,v in self.data["results"].items() if not v["applied"]]
        if not pending:
            return 0
        before = sheet.snapshot([rows[k] for k in pending])
        require(before.get(1,{}) in ({},{"stringValue":HEADER}), "Cambió el encabezado L; se conservaron los resultados locales.")
        requests = []
        for key in pending:
            result, row = self.data["results"][key], rows[key]
            expected = {"numberValue":result["value"]}
            current = before.get(row,{})
            # Una escritura pudo aplicarse aunque se perdiera la respuesta de Google.
            require(current == result["before"] or current == expected,
                    f"L{row} fue editada después de leerla. Los resultados están guardados localmente; no se sobrescribió ese dato.")
            if current != expected:
                requests.append(sheet.request(row,expected,True))
        if requests or before.get(1) != {"stringValue":HEADER}:
            sheet.write([sheet.request(1,{"stringValue":HEADER})] + requests)
        after = sheet.snapshot([rows[k] for k in pending])
        confirmed = []
        for key in pending:
            value = self.data["results"][key]["value"]
            if after.get(rows[key]) == {"numberValue":value}:
                confirmed.append(key)
        for key in confirmed:
            self.data["results"][key]["applied"] = True
        self.save()
        require(after.get(1) == {"stringValue":HEADER} and len(confirmed) == len(pending),
                "No se confirmó toda la escritura; se conservaron los resultados para comprobarlos en la próxima ejecución.")
        print(f"Guardados y verificados {len(confirmed)} resultados en L. Recuperación local actualizada.")
        return len(confirmed)

    def finish(self):
        require(len(self.data["results"]) == len(self.data["queue"])
                and all(r["applied"] for r in self.data["results"].values()), "Quedan alumnos o escrituras pendientes; se conservará la recuperación.")
        self.data["complete"] = True
        self.save()


def show_error(error):
    if isinstance(error,StopRun):
        print(f"DETENIDO: {error}")
    else:
        print(f"DETENIDO ({type(error).__name__}). Paso: {LAST_STEP}.")


def stage(label):
    global LAST_STEP
    LAST_STEP = label


def email_key(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value.strip())),
            "Correo ausente o con formato inválido.")
    # No quitar puntos, sufijos + ni alterar el identificador de la cuenta.
    return value.strip().casefold()


def build_records(values, expected):
    records, issues, emails, ids = {}, [], {}, {}
    for row, cells in enumerate(values, 2):
        fields = (cells + ["", "", ""])[:3]
        if not any(str(v).strip() for v in fields):
            continue
        try:
            document = cedula(fields[0])
        except StopRun:
            issues.append(f"A{row}: cédula ausente o inválida")
            continue
        name = norm(fields[1])
        if not name:
            issues.append(f"B{row}: falta nombre completo")
        try:
            email = email_key(fields[2])
        except StopRun:
            issues.append(f"C{row}: correo ausente o inválido")
            continue
        if email in emails:
            issues.append(f"C{row} y C{emails[email]}: correo duplicado")
        if document in ids:
            issues.append(f"A{row} y A{ids[document]}: cédula duplicada")
        emails[email], ids[document] = row, row
        records[row] = {"cedula":document, "name":name, "email":email}
    require(not issues, "Revisar el listado (no se muestran datos personales):\n" + "\n".join(issues))
    require(len(records) == expected, f"Se encontraron {len(records)} alumnos; se esperaban {expected}.")
    return records


def check_account(observed_name, observed_email, expected):
    require(email_key(observed_email) == expected["email"], "El correo de la cuenta no coincide con la columna C.")
    require(norm(observed_name) == expected["name"], "El nombre de la cuenta no coincide con la columna B; revisar manualmente.")


def use_email_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if cfg.get("identity_mode") == "email":
        print("La configuración ya usa correo. No se cambió ningún archivo.")
        return
    backup = Path("config_global.antes-correo.json")
    require(not backup.exists(), "Ya existe el respaldo config_global.antes-correo.json; revisar antes de migrar.")
    with backup.open("x", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    cfg["identity_mode"] = "email"
    cfg["selectors"].setdefault("row_email", "")
    for key in ["row_cedula", "edit", "identity_dialog", "identity_cedula", "identity_close"]:
        cfg["selectors"].pop(key, None)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Configuración adaptada a correo, con respaldo. Se conservaron hoja, curso, pilotos y selectores existentes.")


def parse_percentage(text):
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*%\s*", text)
    require(match is not None, "El control seleccionado no contiene un único porcentaje.")
    value = float(match[1].replace(",", "."))
    require(0 <= value <= 100, "Porcentaje fuera del intervalo 0–100.")
    return value / 100


def init_config():
    require(not CONFIG.exists(), "config_global.json ya existe; no se reemplazó.")
    template = Path("config_global.example.json")
    require(template.is_file(), "Falta config_global.example.json.")
    cfg = json.loads(template.read_text(encoding="utf-8-sig"))
    with CONFIG.open("x", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("Creado config_global.json. Configura el ID de la hoja y la autorización antes de ejecutar.")


def locate(scope, spec):
    require(bool(spec), "Falta un selector observado en la interfaz.")
    if isinstance(spec, str):
        return scope.locator(spec)
    method = spec.get("by")
    value = spec.get("value", "")
    require(bool(value), "Selector vacío.")
    if method == "role":
        return scope.get_by_role(value, name=spec["name"], exact=spec.get("exact", True))
    if method == "text":
        return scope.get_by_text(value, exact=True)
    if method == "placeholder":
        return scope.get_by_placeholder(value, exact=True)
    if method == "label":
        return scope.get_by_label(value, exact=True)
    raise StopRun("Tipo de selector no admitido. Usar CSS, role, text, placeholder o label.")


def identity_text(locator):
    element = unique(locator, "cédula")
    tag = element.evaluate("el => el.tagName.toLowerCase()")
    return (element.input_value() if tag in {"input", "textarea"} else element.inner_text()).strip()


class GlobalSheet(Sheet):
    # Solo se reutilizan lectura de A, normalización y transporte oficial de Google.
    def __init__(self, cfg):
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from google_auth_httplib2 import AuthorizedHttp
        import httplib2
        require(isinstance(cfg["spreadsheet_id"], str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", cfg["spreadsheet_id"]))
                and cfg["spreadsheet_id"] != "TU_SPREADSHEET_ID"
                and type(cfg["sheet_id"]) is int and cfg["sheet_id"] >= 0
                and bool(cfg["sheet_name"]), "Configurar el destino autorizado en config_global.json.")
        require(Path("client_secret.json").is_file(), "Falta client_secret.json.")
        flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", [
            "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.metadata.readonly"])
        credentials = flow.run_local_server(port=0, access_type="online")
        timeout = cfg.get("google_timeout_seconds",30)
        require(type(timeout) in {int,float} and 0 < timeout <= 300, "google_timeout_seconds debe estar entre 0 y 300 segundos.")
        transport = AuthorizedHttp(credentials,http=httplib2.Http(timeout=timeout))
        self.api = build("sheets", "v4", http=transport, cache_discovery=False)
        drive = build("drive", "v3", http=transport, cache_discovery=False)
        self.c, self.sid = cfg, cfg["spreadsheet_id"]
        permissions = drive.files().get(fileId=self.sid, fields="capabilities(canEdit)", supportsAllDrives=True).execute()
        require(permissions.get("capabilities", {}).get("canEdit") is True, "La cuenta de Google no puede editar.")
        metadata = self.api.spreadsheets().get(spreadsheetId=self.sid, fields="sheets(properties,protectedRanges)").execute()
        found = [s for s in metadata["sheets"] if s["properties"]["sheetId"] == cfg["sheet_id"]]
        require(len(found) == 1 and found[0]["properties"]["title"] == cfg["sheet_name"], "Pestaña incorrecta.")
        grid = found[0]["properties"]["gridProperties"]
        require(2 <= cfg["last_row"] <= grid["rowCount"] and grid["columnCount"] >= 12, "Rango fuera de la hoja.")
        self.protections = found[0].get("protectedRanges", [])
        self.prefix = "'" + cfg["sheet_name"].replace("'", "''") + "'!"
        require(norm(self.values("A1")[0][0]) in {"cedula", "cédula"}, "A1 no corresponde a cédula.")
        require([norm(v) for v in self.values("B1:C1")[0]] == ["nombre", "correo"], "B/C no corresponden a Nombre/Correo.")

    def identities(self):
        records = build_records(self.values(f'A2:C{self.c["last_row"]}'), self.c["expected_students"])
        print(f"Listado validado: {len(records)} alumnos, cédulas y correos únicos; nombres completos presentes.")
        return records

    def snapshot(self, rows=None):
        # Lectura acotada de L completa para detectar si contiene otros datos.
        data = self.api.spreadsheets().get(spreadsheetId=self.sid,
            ranges=[self.prefix + f'L1:L{self.c["last_row"]}'], includeGridData=True,
            fields="sheets(data(startRow,rowData(values(userEnteredValue,dataValidation))))").execute()
        cells = {r:{} for r in range(1, self.c["last_row"] + 1)}
        for block in data["sheets"][0].get("data", []):
            for index, row in enumerate(block.get("rowData", []), block.get("startRow", 0) + 1):
                cells[index] = (row.get("values") or [{}])[0]
        header = cells[1].get("userEnteredValue", {})
        require(header in ({}, {"stringValue":HEADER}), "L ya tiene otro encabezado. No se sobrescribirá.")
        if not header:
            require(not any(c.get("userEnteredValue") for r,c in cells.items() if r > 1),
                    "L tiene datos sin encabezado; elegir otro destino requiere adaptar el programa.")
        for row, cell in cells.items():
            require(not cell.get("dataValidation"), f"L{row} tiene validación; revisar antes de escribir.")
            if row == 1:
                continue
            value = cell.get("userEnteredValue", {})
            require(not value or (set(value) == {"numberValue"} and 0 <= value["numberValue"] <= 1),
                    f"L{row} contiene otro dato o una fórmula; no se sobrescribirá.")
        for row in [1] + list(rows or []):
            for p in self.protections:
                if p.get("warningOnly") or p.get("requestingUserCanEdit") is True:
                    continue
                area = p.get("range", {})
                overlap = (area.get("startRowIndex",0) <= row - 1 < area.get("endRowIndex",10**9)
                           and area.get("startColumnIndex",0) <= 11 < area.get("endColumnIndex",10**9))
                require(not overlap, f"L{row} tiene protección no editable.")
        return {r:c.get("userEnteredValue", {}) for r,c in cells.items()}

    def request(self, row, value, percentage=False):
        cell = {"userEnteredValue":value}
        fields = "userEnteredValue"
        if percentage:
            cell["userEnteredFormat"] = {"numberFormat":{"type":"PERCENT", "pattern":"0.##%"}}
            fields += ",userEnteredFormat.numberFormat"
        return {"updateCells":{"range":{"sheetId":self.c["sheet_id"], "startRowIndex":row-1,
               "endRowIndex":row, "startColumnIndex":11, "endColumnIndex":12},
               "rows":[{"values":[cell]}], "fields":fields}}

    def probe(self, row, before):
        require(self.snapshot([row]) == before, "L cambió antes de comprobar escritura.")
        self.write([self.request(row, before[row])])
        require(self.snapshot([row]) == before, "L cambió durante la prueba de escritura.")
        print("Permiso de escritura real comprobado reescribiendo el mismo valor de una celda en L.")

    def commit(self, results, identities, before):
        require(self.identities() == identities, "Cambió el listado, su orden, los nombres o los correos; detener.")
        require(self.snapshot(list(results)) == before, "L cambió durante la consulta; no se sobrescribirá.")
        requests = [self.request(1, {"stringValue":HEADER})]
        requests += [self.request(r, {"numberValue":value}, True) for r,value in results.items()]
        self.write(requests)
        after = self.snapshot(list(results))
        require(after[1] == {"stringValue":HEADER}, "No se confirmó el encabezado de L.")
        for row,value in results.items():
            require(abs(after[row].get("numberValue", -1) - value) < 1e-9,
                    f"No se confirmó el resultado en L{row}; revisar antes de repetir.")
        print(f"Actualizadas y verificadas {len(results)} filas exclusivamente en L.")


def select_from_row(identities, first_row, last_row):
    require(type(first_row) is int and 2 <= first_row <= last_row,
            "La fila inicial debe estar entre 2 y el límite de filas configurado.")
    selected = sorted(row for row in identities if row >= first_row)
    require(bool(selected), "No hay alumnos a partir de la fila indicada.")
    return selected


def validate(cfg):
    require(cfg.get("authorized") is True and cfg.get("students_url") == URL, "Autorización o URL incorrecta.")
    require(type(cfg.get("expected_students")) is int and cfg["expected_students"] > 0, "Configurar la cantidad esperada de alumnos.")
    require(cfg.get("delay_seconds",0) >= 1, "Usar al menos un segundo entre consultas.")
    require(cfg.get("identity_mode") == "email", "Ejecuta use-email para cambiar la configuración a correo.")
    keys = ["search", "student_row", "row_name", "view", "detail", "detail_name", "course", "course_title", "course_percentage"]
    keys += ["row_email"]
    for key in keys:
        require(bool(cfg["selectors"].get(key)), f"Falta configurar el control observado: {key}.")


def wait_for_login(page, cfg):
    stage("esperar el inicio de sesión manual en EduQairos")
    print("Inicia sesión en EduQairos. La consulta comenzará automáticamente cuando aparezca la sesión abierta.")
    ready = cfg.get("login_ready", {"by":"text", "value":"Cerrar Sesión"})
    locate(page, ready).wait_for(state="visible", timeout=cfg.get("login_timeout_ms",300000))


def consult_rows(page, cfg, identities, selected, on_result=None):
    results = {}
    for index, row in enumerate(selected):
        print(f"Consultando fila {row}: {index+1}/{len(selected)}.")
        results[row] = read_global(page,cfg,identities[row])
        if on_result is not None:
            on_result(row,results[row])
        time.sleep(cfg["delay_seconds"])
    return results


def read_global(page, cfg, identity):
    s = cfg["selectors"]
    stage("abrir la página de estudiantes")
    page.goto(URL, wait_until="domcontentloaded")
    stage("localizar el campo de búsqueda de estudiantes")
    search = locate(page, s["search"])
    search.wait_for(state="visible")
    unique(search, "búsqueda").fill(identity["email"])
    stage("esperar el resultado de la búsqueda por correo")
    page.wait_for_timeout(int(cfg["delay_seconds"] * 1000))
    rows = locate(page, s["student_row"])
    rows.first.wait_for(state="visible")
    candidates = [r for r in rows.all() if r.is_visible()]
    # Exigir un único resultado visible, sin elegir el primero ni hacer coincidencia difusa.
    require(len(candidates) == 1, "Búsqueda por correo ausente o ambigua; no se asignará 0%.")
    row = candidates[0]
    stage("comprobar nombre y correo del resultado")
    name = norm(unique(locate(row,s["row_name"]), "nombre del resultado").inner_text())
    observed_email = unique(locate(row,s["row_email"]), "correo del resultado").inner_text()
    check_account(name, observed_email, identity)
    stage("abrir Ver del alumno identificado")
    unique(locate(row,s["view"]), "Ver").click()
    detail = locate(page,s["detail"])
    stage("esperar la ventana de progreso")
    detail.wait_for(state="visible")
    unique(detail, "ficha de progreso")
    stage("comprobar nombre en la ventana de progreso")
    require(norm(unique(locate(detail,s["detail_name"]), "nombre de ficha").inner_text()) == name,
            "La ficha de progreso no corresponde al resultado verificado.")
    courses = locate(detail,s["course"])
    stage("identificar el curso exacto en la ventana de progreso")
    # La ficha puede abrir antes de que termine de cargar la inscripción.
    # Esperar la tarjeta evita interpretar una lista transitoriamente vacía como ausencia.
    courses.first.wait_for(state="visible")
    matches = [c for c in courses.all() if c.is_visible() and
               norm(unique(locate(c,s["course_title"]), "título del curso").inner_text()) == norm(cfg["course_title"])]
    if len(matches) != 1:
        print(f"Diagnóstico de curso: {sum(c.is_visible() for c in courses.all())} tarjetas visibles; {len(matches)} coincidencias con el curso configurado.")
    require(len(matches) == 1, "Curso no inscrito o ambiguo; no se asignará 0%.")
    control = locate(matches[0],s["course_percentage"])
    stage("leer el porcentaje global del curso")
    control.wait_for(state="visible")
    return parse_percentage(unique(control, "porcentaje del curso").inner_text())


def run_update(args, cfg, sheet, identities):
    journal = Progress(STATE_PATH,cfg,identities,args.from_row) if args.write else None
    if journal is not None:
        if journal.resumed:
            print("Se encontró una ejecución interrumpida. Recuperando resultados antes de consultar alumnos nuevos.")
            try:
                journal.flush(sheet)
            except (Exception,KeyboardInterrupt):
                print("No se completó la recuperación en Sheets. Los porcentajes siguen en .eduqairos-state; repite el comando cuando resuelvas la conexión o el conflicto.")
                raise
            identities = sheet.identities()
        selected = journal.remaining(identities)
        if not selected:
            journal.finish()
            print("La ejecución pendiente quedó completada y verificada. No fue necesario abrir EduQairos.")
            return
        print(f"Pendientes en esta ejecución: {len(selected)}. Siguiente alumno: fila {selected[0]}.")
    else:
        selected = select_from_row(identities,args.from_row if args.from_row is not None else 2,cfg["last_row"])
    before = sheet.snapshot(selected)
    require(sheet.identities() == identities, "El listado cambió al iniciar. Repite sin ordenar ni editar mientras se ejecuta.")
    callback = (lambda row,value:journal.record(row,value,identities[row],before[row])) if journal else None
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        stage("abrir Chromium")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=False)
        try:
            page = context.new_page()
            page.set_default_timeout(cfg["timeout_ms"])
            page.goto(URL)
            wait_for_login(page,cfg)
            results = consult_rows(page,cfg,identities,selected,on_result=callback)
            if journal is not None:
                stage("escribir y verificar exclusivamente la columna L")
                journal.flush(sheet)
                journal.finish()
                print(f"Ejecución completada: {len(journal.data['queue'])} alumnos consultados y guardados en L.")
            else:
                print(f"Simulación completada: {len(results)} filas; no se escribió en Sheets ni se creó recuperación local.")
        except (Exception,KeyboardInterrupt) as error:
            failed_step = LAST_STEP
            if journal is not None:
                try:
                    journal.flush(sheet)
                except (Exception,KeyboardInterrupt) as save_error:
                    label = str(save_error) if isinstance(save_error,StopRun) else type(save_error).__name__
                    print(f"No se pudo confirmar el guardado parcial en Sheets ({label}). Los resultados siguen en el archivo local de recuperación.")
                print("Repite el mismo comando: se reanudará sin repetir los alumnos ya leídos y conservados correctamente.")
            stage(failed_step)
            show_error(error)
            if args.pause_on_error and isinstance(error,Exception):
                print(f"Pausa de diagnóstico. Paso: {LAST_STEP}.")
                input("El navegador queda abierto para revisar la ficha. Pulsa Enter cuando termines para cerrar. ")
            raise
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["init-config","use-email","inspect","check-sheet","all"])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--from-row", type=int, help="Con all, consultar solo desde esta fila de Sheets, incluida.")
    parser.add_argument("--pause-on-error", action="store_true", help="Conservar la página abierta al fallar para revisarla manualmente.")
    args = parser.parse_args()
    if args.from_row is not None and args.mode != "all":
        parser.error("--from-row solo se puede usar con all.")
    require(not os.environ.get("DEBUG") and not os.environ.get("PWDEBUG"), "Desactivar DEBUG y PWDEBUG.")
    if args.mode == "init-config":
        require(not args.write, "init-config no admite --write.")
        init_config()
        return
    if args.mode == "use-email":
        require(not args.write, "use-email no admite --write.")
        use_email_config()
        return
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    require(cfg.get("authorized") is True and cfg.get("students_url") == URL, "Confirmar autorización y URL.")
    if args.mode == "inspect":
        require(not args.write, "inspect no admite --write.")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(accept_downloads=False)
            try:
                page = context.new_page()
                page.goto(URL)
                input("Inicia sesión manualmente. Vuelve aquí y pulsa Enter para abrir el inspector. ")
                page.pause()
            finally:
                context.close()
                browser.close()
        return
    if args.mode != "check-sheet":
        validate(cfg)
    else:
        require(not args.write, "check-sheet no admite --write.")
    sheet = GlobalSheet(cfg)
    identities = sheet.identities()
    if args.mode == "check-sheet":
        sheet.snapshot(list(identities))
        print("L disponible, nombres, correos, cédulas y permiso de edición comprobados. No se modificó la hoja.")
        return
    with state_lock(STATE_PATH) if args.write else nullcontext():
        run_update(args,cfg,sheet,identities)


if __name__ == "__main__":
    try:
        main()
    except (StopRun, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"DETENIDO: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Cancelado. Si usaste --write, repite el comando para recuperar los resultados y continuar. Conserva .eduqairos-state.")
        sys.exit(130)
    except Exception as exc:
        print(f"DETENIDO ({type(exc).__name__}). No hay reintento automático.")
        print(f"Paso: {LAST_STEP}.")
        if LAST_STEP == "abrir Chromium":
            print("Comprueba Chromium: .\\.venv\\Scripts\\python.exe -m playwright install chromium")
        print("Si usaste --write, conserva .eduqairos-state y repite el comando para recuperar el avance. No compartas URLs de autorización.")
        sys.exit(1)
