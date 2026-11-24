"""Descarga de certificados por interfaz autorizada. No modifica Google Sheets."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import unicodedata
from urllib.parse import urlparse
from zipfile import ZipFile, ZIP_DEFLATED

from seguimiento_global import StopRun, require, digest, state_lock

CONFIG = Path('config_certificados.json')
STATE = Path('.certificados-state/progreso.json')
ADMIN = 'https://eduqairos.com/admin/certificados'
COURSE = 'https://eduqairos.com/curso/ley_igualdad_salarial'
COURSE_TITLE = 'Ley Orgánica para la Igualdad Laboral entre Mujeres y Hombres'
LABELS = [
    'Derechos Laborales de Mujeres y Hombres',
    'Igualdad de Género',
    'Erradicación de Violencia y No Discriminación en una Empresa o Centro de Trabajo',
    'Sensibilización Cultural para la Valoración Justa de los Puestos',
    'Sensibilización de igualdad de género en espacios laborales',
    COURSE_TITLE,
]
STEP = 'inicio'


def text_key(text):
    value = ''.join(c for c in unicodedata.normalize('NFKD',str(text)).casefold()
                    if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w\s]',' ',value).split())


def step(label):
    global STEP
    STEP = label


def name_key(name):
    return tuple(sorted(text_key(name).split()))


def name_in_text(name,text):
    words=text_key(text).split()
    expected=name_key(name)
    return any(tuple(sorted(words[i:i+len(expected)]))==expected
               for i in range(len(words)-len(expected)+1))


def search_terms(name):
    # Primero el nombre de la hoja. Si falla, una palabra permite encontrar
    # las dos ordenaciones sin depender de cómo el buscador trata la ñ.
    words=list(dict.fromkeys(str(name).split()))
    full=' '.join(str(name).split())
    return list(dict.fromkeys([full]+sorted(words,key=lambda w:(not w.isascii(),-len(w)))))


def one(locator,label):
    require(locator.count() == 1 and locator.is_visible(),f'Control ausente o ambiguo: {label}.')
    return locator


def locate(scope,spec):
    require(bool(spec),'Falta configurar un control de la interfaz.')
    if isinstance(spec,str):
        return scope.locator(spec)
    exact=spec.get('exact',True)
    if spec['by']=='role':
        return scope.get_by_role(spec['value'],**({'name':spec['name'],'exact':exact} if 'name' in spec else {}))
    if spec['by']=='label':
        return scope.get_by_label(spec['value'],exact=exact)
    if spec['by']=='text':
        return scope.get_by_text(spec['value'],exact=exact)
    if spec['by']=='placeholder':
        return scope.get_by_placeholder(spec['value'],exact=exact)
    raise StopRun('Tipo de selector no reconocido.')


def email_key(value):
    require(isinstance(value,str) and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value.strip()),'Correo inválido.')
    return value.strip().casefold()


def filename(name):
    value=' '.join(str(name).split())
    require(value and not re.search(r'[<>:"/\\|?*\x00-\x1f]',value)
            and not value.endswith(('.', ' ')) and len(value)<=160,'Nombre no apto para un ZIP de Windows; revisar sin renombrar automáticamente.')
    require(value.split('.')[0].upper() not in {'CON','PRN','AUX','NUL'} | {f'{x}{i}' for x in ['COM','LPT'] for i in range(1,10)},'Nombre reservado de Windows.')
    return value+'.zip'


def roster_records(rows):
    records=[]
    errors=[]
    numbers,ids,emails,names,paths={},{},{},{},{}
    for row,raw in enumerate(rows,2):
        values=(raw+['']*5)[:5]
        if not any(str(v).strip() for v in values):
            continue
        try:
            number=int(values[0])
            require(str(values[0]).strip() in {str(number),str(float(number))} and number>=1,'Número inválido.')
            document=str(values[1]).strip()
            require(re.fullmatch(r'[0-9]{10}',document),'Cédula inválida.')
            name=' '.join(str(values[2]).split())
            require(bool(name),'Falta nombre.')
            email=email_key(values[3])
            password=values[4]
            require(isinstance(password,str) and bool(password),'Falta contraseña institucional.')
            output=filename(name)
            for mapping,key,label in [(numbers,number,'número'),(ids,document,'cédula'),(emails,email,'correo'),
                                      (names,name_key(name),'nombre'),(paths,output.casefold(),'nombre de archivo')]:
                require(key not in mapping,f'{label} duplicado con la fila {mapping.get(key)}.')
                mapping[key]=row
            records.append({'row':row,'number':number,'cedula':document,'name':name,'email':email,'password':password})
        except (StopRun,ValueError,TypeError) as error:
            message=str(error) if isinstance(error,StopRun) else 'Número inválido.'
            errors.append(f'Fila {row}: {message}')
    require(not errors,'Revisar el listado:\n'+'\n'.join(errors))
    require(records,'El listado está vacío.')
    return sorted(records,key=lambda r:r['number'])


def read_roster(cfg):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    import httplib2
    require(Path('client_secret.json').is_file(),'Falta client_secret.json.')
    credentials=InstalledAppFlow.from_client_secrets_file('client_secret.json',[
        'https://www.googleapis.com/auth/spreadsheets.readonly']).run_local_server(port=0,access_type='online')
    api=build('sheets','v4',http=AuthorizedHttp(credentials,http=httplib2.Http(timeout=30)),cache_discovery=False)
    meta=api.spreadsheets().get(spreadsheetId=cfg['spreadsheet_id'],fields='sheets(properties)').execute()
    sheets=[s['properties'] for s in meta['sheets'] if s['properties']['sheetId']==cfg['sheet_id']]
    require(len(sheets)==1 and sheets[0]['title']==cfg['sheet_name'],'Pestaña incorrecta.')
    require(cfg['last_row']<=sheets[0]['gridProperties']['rowCount'],'Límite fuera de la hoja.')
    prefix="'"+cfg['sheet_name'].replace("'","''")+"'!"
    data=api.spreadsheets().values().get(spreadsheetId=cfg['spreadsheet_id'],
        range=prefix+f'A1:E{cfg["last_row"]}',valueRenderOption='UNFORMATTED_VALUE').execute().get('values',[])
    require(data and [text_key(x) for x in data[0][1:5]]==['cedula','nombre','correo','contrasena'],
            'Se esperan A número, B cédula, C nombre, D correo y E contraseña. No se modificó la hoja.')
    records=roster_records(data[1:])
    require(len(records)==cfg['expected_students'],f'Hay {len(records)} registros; la configuración espera {cfg["expected_students"]}.')
    print(f'Listado leído: {len(records)} alumnos. Credenciales solo en memoria; hoja de solo lectura.')
    return records


def ocr_image(image):
    require(os.name=='nt','Para PDF de imagen se necesita el OCR local de Windows.')
    script=Path(__file__).with_name('ocr_certificados.ps1')
    require(script.is_file(),'Falta ocr_certificados.ps1.')
    with tempfile.TemporaryDirectory(prefix='eduq-ocr-') as temporary:
        path=Path(temporary)/'image.png'
        image.convert('RGB').save(path)
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script.read_text(encoding='utf-8')],
            env={**os.environ,'EDUQAIROS_OCR_IMAGE':str(path)},capture_output=True,text=True,
            encoding='utf-8',errors='replace',timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        require(result.returncode==0 and result.stdout.strip(),'No fue posible leer el PDF mediante OCR local. No se dará por completo.')
        return result.stdout


def inspect_pdf(content,expected_name,ocr=None):
    from pypdf import PdfReader
    require(content.startswith(b'%PDF-') and len(content)<=25_000_000,'La descarga no es un PDF válido o excede el límite.')
    reader=PdfReader(io.BytesIO(content),strict=True)
    require(not reader.is_encrypted and len(reader.pages)==1,'Se esperaba un certificado PDF de una página sin cifrado.')
    page=reader.pages[0]
    text=page.extract_text() or ''
    if not text.strip():
        images=list(page.images)
        require(len(images)==1,'El PDF tiene una estructura de imagen diferente; revisar antes de aceptar.')
        text=(ocr or ocr_image)(images[0].image)
    normalized=text_key(text)
    require(name_in_text(expected_name,text),'El nombre completo reconocido en el certificado no coincide. Se admite otro orden, pero no palabras faltantes o distintas.')
    found=[i for i,label in enumerate(LABELS) if text_key(label) in normalized]
    require(found,'No se pudo reconocer la lección o el curso del PDF.')
    # El título del módulo 5 contiene "Igualdad de Género"; elegir el más específico.
    if 4 in found and 1 in found:
        found.remove(1)
    if 5 in found and len(found)>1:
        found.remove(5)
    require(len(found)==1,'El PDF no identifica un único tipo de certificado.')
    return found[0]


def inspect_zip(path,name,expected_kinds,validator=inspect_pdf):
    try:
        with ZipFile(path) as archive:
            items=[item for item in archive.infolist() if not item.is_dir()]
            require(len(items)==len(expected_kinds) and len({i.filename for i in items})==len(items),
                    'Cantidad de PDF inesperada o entradas duplicadas en el ZIP.')
            require(sum(i.file_size for i in items)<=100_000_000,'ZIP demasiado grande.')
            require(all(Path(i.filename).suffix.lower()=='.pdf' and 0<i.file_size<=25_000_000 for i in items),
                    'El ZIP debe contener únicamente los certificados PDF esperados.')
            require(archive.testzip() is None,'ZIP incompleto o corrupto.')
            seen=set()
            hashes=set()
            for item in items:
                content=archive.read(item)
                fingerprint=hashlib.sha256(content).hexdigest()
                require(fingerprint not in hashes,'El ZIP repite el mismo PDF.')
                kind=validator(content,name)
                require(kind not in seen,'El ZIP repite un módulo o el certificado final.')
                seen.add(kind)
                hashes.add(fingerprint)
            require(seen==set(expected_kinds),'Faltan módulos o certificado final; contar archivos no basta.')
            return True
    except StopRun:
        raise
    except Exception:
        raise StopRun('No se pudo validar el ZIP o sus PDF. No se dará por completo.') from None


def file_hash(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


class Ledger:
    def __init__(self,cfg,records,start):
        self.path=STATE
        scope=digest([cfg['spreadsheet_id'],cfg['sheet_id'],COURSE])
        if self.path.exists():
            self.data=json.loads(self.path.read_text(encoding='utf-8'))
            require(self.data.get('version')==1 and self.data.get('scope')==scope,'Hay otro lote de certificados pendiente; no se mezclará.')
            require(self.data.get('start')==start,'Conserva el número inicial del lote pendiente.')
        else:
            self.data={'version':1,'scope':scope,'salt':secrets.token_hex(16),'start':start,'queue':[], 'records':{}}
            self.data['queue']=[self.key(r) for r in records if r['number']>=start]
            require(self.data['queue'],'No hay alumnos desde ese número.')
            self.save()
        mapping={self.key(r):r for r in records}
        require(all(k in mapping for k in self.data['queue']),'Falta un alumno o cambió su cédula; revisar el lote antes de continuar.')
        self.selected=[mapping[k] for k in self.data['queue']]

    def key(self,record):
        return digest([self.data['salt'],record['cedula']])

    def account(self,record):
        return digest([self.data['salt'],record['cedula'],text_key(record['name']),record['email']])

    def status(self,record):
        return self.data['records'].get(self.key(record),{})

    def set(self,record,**values):
        status=self.data['records'].setdefault(self.key(record),{})
        status.update(values)
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as handle:
            json.dump(self.data,handle,ensure_ascii=False,allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,self.path)


def admin_rows(page,cfg,record,expected_count=None):
    step('buscar certificados por nombre')
    page.goto(ADMIN,wait_until='domcontentloaded')
    search=locate(page,cfg['selectors']['admin_search'])
    search.wait_for(state='visible')
    terms=iter(search_terms(record['name']))
    query=next(terms)
    one(search,'buscador de certificados').fill(query)
    expected_name=name_key(record['name'])
    deadline=time.monotonic()+cfg['timeout_ms']/1000
    query_deadline=time.monotonic()+min(12,cfg['timeout_ms']/2000)
    while time.monotonic()<deadline:
        rows=page.get_by_role('row').filter(has=page.get_by_role('button',name='Descargar',exact=True))
        visible=[row for row in rows.all() if row.is_visible()]
        good=[]
        for row in visible:
            cells=row.get_by_role('cell')
            if cells.count()<5:
                continue
            if name_key(cells.nth(cfg['table']['name_index']).inner_text())!=expected_name:
                continue
            title=text_key(cells.nth(cfg['table']['course_index']).inner_text())
            require(title==text_key(COURSE_TITLE) or title.startswith(text_key(COURSE_TITLE)+' '),
                    'Hay certificados de otro curso; no se descargará el grupo mezclado.')
            code=cells.nth(cfg['table']['id_index']).inner_text()
            match=re.search(r'CERT\s*-\s*(\d+)',code,re.I)
            require(match is not None,'No se pudo leer el identificador de certificado.')
            good.append(match[1])
        info=page.get_by_text(re.compile(r'^\s*\d+ certificados?\s*[·•]\s*Página\s+\d+\s+de\s+\d+\s*$',re.I))
        if info.count()==1:
            match=re.search(r'(\d+) certificados?.*Página\s+(\d+)\s+de\s+(\d+)',info.inner_text(),re.I)
            if match and int(match[1])==len(visible)==len(good) and int(match[2])==int(match[3])==1:
                require(len(set(good))==len(good),'Identificadores de certificado duplicados.')
                if len(good) in {5,6} and (expected_count is None or len(good)==expected_count):
                    return good
                # Tras emitir, la lista puede tardar en actualizarse en el servidor.
                if expected_count==6 and len(good)==5:
                    page.reload(wait_until='domcontentloaded')
                    search=locate(page,cfg['selectors']['admin_search'])
                    search.wait_for(state='visible')
                    one(search,'buscador de certificados').fill(query)
        if time.monotonic()>=query_deadline:
            query=next(terms,None)
            if query is None:
                break
            one(search,'buscador de certificados').fill(query)
            query_deadline=time.monotonic()+min(6,cfg['timeout_ms']/1000)
        page.wait_for_timeout(250)
    raise StopRun('No se encontró una lista completa de cinco o seis certificados del mismo nombre completo. Se probaron palabras del nombre; revisar coincidencias y paginación.')


def download_bulk(page,path,count,cfg):
    step('descargar el ZIP filtrado')
    control=page.get_by_role('button',name=re.compile(r'^Descargar todos los filtrados\s*\('+str(count)+r'\)$',re.I))
    one(control,'Descargar todos los filtrados')
    with page.expect_download(timeout=cfg['download_timeout_ms']) as event:
        control.click()
    event.value.save_as(path)
    require(event.value.failure() is None,'La descarga no finalizó correctamente.')


def field_value(control,label):
    control.wait_for(state='visible')
    one(control,label)
    tag=control.evaluate('el=>el.tagName.toLowerCase()')
    return control.input_value() if tag in {'input','textarea'} else control.inner_text()


def generate_final(browser,cfg,record,ledger,download_path):
    require(not ledger.status(record).get('generation_attempted'),
            'Ya hubo un intento de emisión sin confirmación. Se revisó el administrador y aún hay cinco: comprobar manualmente antes de volver a emitir.')
    require(cfg.get('allow_final_certificate') is True,'La emisión final está desactivada en la configuración.')
    step('abrir sesión aislada del alumno')
    context=browser.new_context(accept_downloads=True)
    try:
        page=context.new_page()
        page.set_default_timeout(cfg['timeout_ms'])
        page.goto(COURSE,wait_until='domcontentloaded')
        email=locate(page,cfg['selectors']['student_email'])
        if email.count()==0:
            entry=locate(page,cfg['selectors']['student_login_entry'])
            entry.wait_for(state='visible')
            one(entry,'entrada al inicio de sesión del alumno').click()
        step('iniciar sesión del alumno con las credenciales de la hoja')
        email=locate(page,cfg['selectors']['student_email'])
        email.wait_for(state='visible')
        require(urlparse(page.url).hostname=='eduqairos.com','El formulario no pertenece a EduQairos; no se introducirán credenciales.')
        one(email,'correo de acceso').fill(record['email'])
        one(locate(page,cfg['selectors']['student_password']),'contraseña de acceso').fill(record['password'])
        one(locate(page,cfg['selectors']['student_login_submit']),'iniciar sesión').click()
        step('comprobar identidad de la cuenta del alumno')
        profile=page.get_by_text('Mi perfil',exact=True)
        profile.wait_for(state='visible')
        one(profile,'Mi perfil').click()
        found_email=field_value(locate(page,cfg['selectors']['profile_email']),'correo del perfil')
        found_name=field_value(locate(page,cfg['selectors']['profile_name']),'nombre completo del perfil')
        require(email_key(found_email)==record['email'] and name_key(found_name)==name_key(record['name']),
                'El perfil del alumno no coincide con la fila de la hoja. No se emitió el certificado.')
        page.goto(COURSE,wait_until='domcontentloaded')
        step('comprobar 100% y cinco lecciones completas')
        control=page.get_by_role('button',name='Obtener Certificado',exact=True)
        control.wait_for(state='visible')
        # Contenedor que reúne el progreso, las cinco lecciones y el botón observado.
        card=control.locator('xpath=ancestor::div[.//*[normalize-space(text())="Tu progreso"]][1]')
        one(card,'tarjeta de progreso del alumno')
        text=text_key(card.inner_text())
        require(re.search(r'\b100\b',text) and '5 de 5 lecciones completadas' in text
                and 'curso completado' in text,'El alumno no muestra 100% y 5/5 lecciones completas. No se emitirá nada.')
        one(control,'Obtener Certificado')
        # Se registra antes del clic para no repetir una emisión cuyo resultado se perdió.
        ledger.set(record,generation_attempted=True,account=ledger.account(record))
        step('solicitar y descargar el certificado final')
        with page.expect_download(timeout=cfg['download_timeout_ms']) as event:
            control.click()
        event.value.save_as(download_path)
        require(event.value.failure() is None,'Se solicitó el final, pero no se confirmó la descarga. Se comprobará el administrador al reanudar.')
        require(inspect_pdf(Path(download_path).read_bytes(),record['name'])==5,'La descarga del final no pasó la validación.')
    finally:
        context.close()


def combine(partial,final,path):
    with ZipFile(partial) as source,ZipFile(path,'x',compression=ZIP_DEFLATED) as target:
        for item in source.infolist():
            if not item.is_dir():
                target.writestr(item.filename,source.read(item))
        require('06_CERTIFICADO_FINAL.pdf' not in source.namelist(),'Nombre interno repetido.')
        target.write(final,'06_CERTIFICADO_FINAL.pdf')


def process_student(browser,page,cfg,record,ledger,destination):
    output=destination/filename(record['name'])
    status=ledger.status(record)
    if output.exists():
        step('comprobar ZIP existente sin sobrescribir')
        if not (status.get('done') and status.get('file_hash')==file_hash(output)
                and status.get('account')==ledger.account(record)):
            inspect_zip(output,record['name'],set(range(6)))
        ledger.set(record,done=True,file_hash=file_hash(output),account=ledger.account(record))
        print(f'Número {record["number"]}: ZIP completo existente, omitido.')
        return
    require(not status.get('done') or status.get('account')==ledger.account(record),
            'Cambió la identidad de una descarga ya completada; requiere revisión.')
    codes=admin_rows(page,cfg,record)
    staging=destination/'.certificados-staging'
    staging.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lote-',dir=staging) as temporary:
        folder=Path(temporary)
        package=folder/'descarga.zip'
        download_bulk(page,package,len(codes),cfg)
        if len(codes)==5:
            step('comprobar que faltaba únicamente el certificado final')
            inspect_zip(package,record['name'],set(range(5)))
            final=folder/'final.pdf'
            generate_final(browser,cfg,record,ledger,final)
            step('confirmar la aparición de seis certificados en el administrador')
            new_codes=admin_rows(page,cfg,record,expected_count=6)
            require(len(new_codes)==6 and set(codes).issubset(new_codes),'No se confirmaron los seis certificados después de emitir. Revisar antes de otro intento.')
            combined=folder/'completo.zip'
            combine(package,final,combined)
            package=combined
        step('validar los seis PDF y guardar el ZIP con el nombre del alumno')
        inspect_zip(package,record['name'],set(range(6)))
        require(not output.exists(),'Ya apareció un ZIP con ese nombre; no se sobrescribirá.')
        os.link(package,output)  # Publicación completa y exclusiva en el mismo volumen.
        ledger.set(record,done=True,file_hash=file_hash(output),account=ledger.account(record))
        print(f'Número {record["number"]}: ZIP de seis certificados guardado y verificado.')


def init_config():
    require(not CONFIG.exists(),'config_certificados.json ya existe; se conservó.')
    cfg=json.loads(Path('config_certificados.example.json').read_text(encoding='utf-8'))
    previous=Path('config_global.json')
    if previous.exists():
        original=json.loads(previous.read_text(encoding='utf-8-sig'))
        for key in ['spreadsheet_id','sheet_id','sheet_name','last_row']:
            cfg[key]=original[key]
    cfg['download_directory']=str(Path.home()/'Downloads'/'RODRIGO TRIVIÑO')
    with CONFIG.open('x',encoding='utf-8') as handle:
        json.dump(cfg,handle,ensure_ascii=False,indent=2)
    print('Creado config_certificados.json. No se cambió la configuración de porcentajes.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['init-config','check','inspect','run'])
    parser.add_argument('--from-number',type=int,help='Número de la columna A, no número de fila.')
    parser.add_argument('--limit',type=int,help='Limitar alumnos de esta ejecución; por ejemplo 1 para la primera prueba.')
    parser.add_argument('--only-number',type=int,help='Procesar únicamente este número del lote guardado.')
    parser.add_argument('--pause-on-error',action='store_true')
    args=parser.parse_args()
    require(not os.environ.get('DEBUG') and not os.environ.get('PWDEBUG'),'Desactiva DEBUG/PWDEBUG para no registrar credenciales.')
    if args.mode=='init-config':
        init_config()
        return
    cfg=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    require(cfg.get('authorized') is True,'Confirmar la autorización en config_certificados.json.')
    start=args.from_number if args.from_number is not None else cfg['start_number']
    require(start>=1 and (args.limit is None or args.limit>=1),'Número inicial o límite inválido.')
    records=read_roster(cfg)
    selected=[r for r in records if r['number']>=start]
    require(selected,'No hay alumnos desde el número indicado.')
    print(f'Inicio: número {start} de A; primer registro en fila {selected[0]["row"]}. Selección: {len(selected)} alumnos.')
    if args.mode=='check':
        print('Comprobación terminada. No se inició sesión de alumno, emitió ni descargó ningún certificado.')
        return
    from playwright.sync_api import sync_playwright
    destination=Path(cfg['download_directory']).expanduser().resolve()
    with state_lock(STATE),sync_playwright() as p:
        browser=p.chromium.launch(headless=False)
        admin=browser.new_context(accept_downloads=True)
        try:
            page=admin.new_page()
            page.set_default_timeout(cfg['timeout_ms'])
            page.goto(ADMIN,wait_until='domcontentloaded')
            print('Inicia sesión como administrador. Continuará cuando aparezca Cerrar Sesión.')
            page.get_by_text('Cerrar Sesión',exact=True).wait_for(state='visible',timeout=300000)
            page.goto(ADMIN,wait_until='domcontentloaded')
            if args.mode=='inspect':
                print('Inspector local: selecciona controles sin grabar credenciales. Resume cerrará esta inspección.')
                page.pause()
                return
            ledger=Ledger(cfg,records,start)
            destination.mkdir(parents=True,exist_ok=True)
            selected=[r for r in ledger.selected if args.only_number is None or r['number']==args.only_number]
            require(selected,'El número solicitado no pertenece al lote.')
            selected=selected[:args.limit] if args.limit else selected
            for index,record in enumerate(selected,1):
                print(f'Procesando número {record["number"]} ({index}/{len(selected)}).')
                process_student(browser,page,cfg,record,ledger,destination)
                time.sleep(cfg['delay_seconds'])
            print('Tramo terminado. Los ZIP correctos se conservaron y el avance quedó guardado.')
        except (Exception,KeyboardInterrupt) as error:
            message=str(error) if isinstance(error,StopRun) else type(error).__name__
            print(f'DETENIDO: {message}. Paso: {STEP}. No se muestran credenciales.')
            print('Conserva .certificados-state y los ZIP. Repite el comando para omitir los completos y retomar el pendiente.')
            if args.pause_on_error and isinstance(error,Exception):
                input('Revisa la página. Enter cierra esta ejecución: ')
            raise
        finally:
            admin.close()
            browser.close()


if __name__=='__main__':
    try:
        main()
    except (StopRun,FileNotFoundError,json.JSONDecodeError) as error:
        print(f'DETENIDO: {error}')
        raise SystemExit(1)
    except KeyboardInterrupt:
        print('Cancelado. Los ZIP terminados y el avance permanecen guardados.')
        raise SystemExit(130)
    except Exception as error:
        print(f'DETENIDO ({type(error).__name__}). Paso: {STEP}. No hay reintento automático.')
        raise SystemExit(1)
