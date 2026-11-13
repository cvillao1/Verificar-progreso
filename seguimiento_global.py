"""Seguimiento global por interfaz administrativa. Destino exclusivo: columna L."""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

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
    print("Creado config_global.json. Configura el ID de la hoja, autorización y filas piloto antes de ejecutar.")


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
        require(isinstance(cfg["spreadsheet_id"], str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", cfg["spreadsheet_id"]))
                and cfg["spreadsheet_id"] != "TU_SPREADSHEET_ID"
                and type(cfg["sheet_id"]) is int and cfg["sheet_id"] >= 0
                and bool(cfg["sheet_name"]), "Configurar el destino autorizado en config_global.json.")
        require(Path("client_secret.json").is_file(), "Falta client_secret.json.")
        flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", [
            "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.metadata.readonly"])
        credentials = flow.run_local_server(port=0, access_type="online")
        self.api = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
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


def consult_rows(page, cfg, identities, selected):
    results = {}
    for index, row in enumerate(selected):
        print(f"Consultando fila {row}: {index+1}/{len(selected)}.")
        results[row] = read_global(page,cfg,identities[row])
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
    if args.from_row is not None:
        selected = select_from_row(identities, args.from_row, cfg["last_row"])
        print(f"Consulta limitada: {len(selected)} alumnos desde la fila {selected[0]} hasta la {selected[-1]}.")
    else:
        selected = sorted(identities)
    before = sheet.snapshot(selected)
    if args.write:
        sheet.probe(selected[0], before)
    from playwright.sync_api import sync_playwright
    results = {}
    with sync_playwright() as p:
        stage("abrir Chromium")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(accept_downloads=False)
        try:
            page = context.new_page()
            page.set_default_timeout(cfg["timeout_ms"])
            page.goto(URL)
            wait_for_login(page,cfg)
            results = consult_rows(page,cfg,identities,selected)
            if args.write:
                stage("escribir y verificar exclusivamente la columna L")
                sheet.commit(results,identities,before)
            else:
                print(f"Simulación completada: {len(results)} filas; no se escribió en Sheets.")
        except Exception:
            if args.pause_on_error:
                print(f"Pausa de diagnóstico. Paso: {LAST_STEP}.")
                input("El navegador queda abierto para revisar la ficha. Pulsa Enter cuando termines para cerrar. ")
            raise
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    try:
        main()
    except (StopRun, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"DETENIDO: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Cancelado. Si se interrumpió la escritura, revisa L antes de repetir.")
        sys.exit(130)
    except Exception as exc:
        print(f"DETENIDO ({type(exc).__name__}). No hay reintento automático.")
        print(f"Paso: {LAST_STEP}.")
        if LAST_STEP == "abrir Chromium":
            print("Comprueba Chromium: .\\.venv\\Scripts\\python.exe -m playwright install chromium")
        print("Si falló durante la escritura, revisa L antes de repetir. No compartas URLs de autorización.")
        sys.exit(1)
