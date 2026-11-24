"""Pruebas locales con identidades ficticias; no acceden a la web ni a Sheets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from zipfile import ZipFile
import descargar_certificados as c


NAME='PERSONA EJEMPLO PRUEBA'


def record(number=123):
    return {'number':number,'row':number+1,'cedula':f'{number:010d}',
            'name':NAME,'email':'ejemplo@example.test','password':'clave-ficticia'}


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.cfg={'spreadsheet_id':'ficticio','sheet_id':0}

    def tearDown(self):
        self.temp.cleanup()

    def ledger(self, records=None):
        with patch.object(c,'STATE',self.root/'state'/'avance.json'):
            return c.Ledger(self.cfg,records or [record()],123)

    def zip(self,kinds):
        path=self.root/'test.zip'
        with ZipFile(path,'w') as z:
            for i,kind in enumerate(kinds):
                z.writestr(f'{i}.pdf',str(kind).encode())
        return path

    def test_roster_uses_ordinal_not_row_and_accepts_formula_values(self):
        rows=[[123,'0000000123',NAME,'ejemplo@example.test','clave']]
        r=c.roster_records(rows)[0]
        self.assertEqual((r['number'],r['row']),(123,2))

    def test_duplicate_identity_stops_without_disclosing_password(self):
        rows=[[123,'0000000123',NAME,'ejemplo@example.test','SECRETO'],
              [124,'0000000124',NAME,'otro@example.test','SECRETO']]
        with self.assertRaises(c.StopRun) as caught:
            c.roster_records(rows)
        self.assertNotIn('SECRETO',str(caught.exception))

    def test_invalid_filenames_stop(self):
        for name in ['CON','../persona','A:B','NUL.pdf','persona.']:
            with self.subTest(name=name), self.assertRaises(c.StopRun):
                c.filename(name)

    def test_six_different_types_required(self):
        validator=lambda content,name:int(content)
        self.assertTrue(c.inspect_zip(self.zip(range(6)),NAME,set(range(6)),validator))
        with self.assertRaises(c.StopRun):
            c.inspect_zip(self.zip([0,1,2,3,4,4]),NAME,set(range(6)),validator)

    def test_five_must_be_modules_not_final(self):
        with self.assertRaises(c.StopRun):
            c.inspect_zip(self.zip([0,1,2,3,5]),NAME,set(range(5)),lambda b,n:int(b))

    def test_incomplete_archive_rejected(self):
        with self.assertRaises(c.StopRun):
            c.inspect_zip(self.zip([0,1]),NAME,set(range(6)),lambda b,n:int(b))

    def test_pdf_name_and_specific_module(self):
        page=Mock()
        page.extract_text.return_value=NAME+' '+c.LABELS[4]
        reader=Mock(is_encrypted=False,pages=[page])
        with patch('pypdf.PdfReader',return_value=reader):
            self.assertEqual(c.inspect_pdf(b'%PDF-test',NAME),4)
            with self.assertRaises(c.StopRun):
                c.inspect_pdf(b'%PDF-test','OTRA PERSONA')

    def test_pdf_image_uses_local_ocr(self):
        page=Mock()
        page.extract_text.return_value=''
        page.images=[Mock(image='fake-image')]
        with patch('pypdf.PdfReader',return_value=Mock(is_encrypted=False,pages=[page])):
            self.assertEqual(c.inspect_pdf(b'%PDF-test',NAME,lambda _:NAME+' '+c.COURSE_TITLE),5)

    def test_full_name_can_change_order_but_cannot_lose_words(self):
        original='ALICIA BEATRIZ PEREZ MUÑOZ'
        self.assertEqual(c.name_key(original),c.name_key('PEREZ MUÑOZ ALICIA BEATRIZ'))
        self.assertTrue(c.name_in_text(original,'Otorgado a PEREZ MUÑOZ ALICIA BEATRIZ por participar'))
        self.assertFalse(c.name_in_text(original,'ALICIA BEATRIZ PEREZ GOMEZ'))
        self.assertFalse(c.name_in_text(original,'ALICIA BEATRIZ PEREZ'))
        self.assertFalse(c.name_in_text(original,'ALICIA BEATRIZ OTRO PEREZ MUÑOZ'))
        self.assertEqual(c.search_terms(original)[:2],[original,'BEATRIZ'])

    def test_resume_survives_row_and_ordinal_sorting(self):
        ledger=self.ledger()
        ledger.set(record(),done=True)
        changed=record(400)
        changed['cedula']=record()['cedula']
        changed['row']=401
        restored=self.ledger([changed])
        self.assertTrue(restored.status(changed)['done'])

    def test_state_has_no_names_emails_passwords(self):
        ledger=self.ledger()
        ledger.set(record(),generation_attempted=True,account=ledger.account(record()))
        data=ledger.path.read_text()
        for secret in [NAME,record()['email'],record()['password'],record()['cedula']]:
            self.assertNotIn(secret,data)

    def test_generation_attempt_is_not_repeated(self):
        ledger=self.ledger()
        ledger.set(record(),generation_attempted=True)
        browser=Mock()
        with self.assertRaises(c.StopRun):
            c.generate_final(browser,{},record(),ledger,self.root/'final.pdf')
        browser.new_context.assert_not_called()

    def test_existing_zip_is_not_overwritten_or_queried(self):
        ledger=self.ledger()
        path=self.root/c.filename(NAME)
        path.write_bytes(b'previous')
        ledger.set(record(),done=True,file_hash=c.file_hash(path),account=ledger.account(record()))
        with patch.object(c,'admin_rows') as admin:
            c.process_student(Mock(),Mock(),self.cfg,record(),ledger,self.root)
            admin.assert_not_called()
        self.assertEqual(path.read_bytes(),b'previous')

    def test_wrong_five_modules_never_triggers_generation(self):
        ledger=self.ledger()
        def download(page,path,count,cfg):
            path.write_bytes(b'invalid')
        with patch.object(c,'admin_rows',return_value=list('12345')), \
             patch.object(c,'download_bulk',side_effect=download), \
             patch.object(c,'inspect_zip',side_effect=c.StopRun('invalid')), \
             patch.object(c,'generate_final') as emit:
            with self.assertRaises(c.StopRun):
                c.process_student(Mock(),Mock(),self.cfg,record(),ledger,self.root)
            emit.assert_not_called()


class BrowserTests(unittest.TestCase):
    """Interfaz ficticia: descarga real de navegador; ninguna llamada a EduQairos."""
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start()
        cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def test_admin_requires_exact_name_and_all_six_rows(self):
        page=self.browser.new_page()
        rows=''.join(f'<tr><td></td><td>CERT-{i}</td><td>{NAME}</td><td>{c.COURSE_TITLE}</td><td>fecha</td><td><button>Descargar</button></td></tr>' for i in range(6))
        html=f'<input><table><tbody>{rows}</tbody></table><p>6 certificados · Página 1 de 1</p><button onclick="location.href=\'/download\'">Descargar todos los filtrados (6)</button>'
        def route_fixture(route):
            if route.request.url.endswith('/download'):
                route.fulfill(content_type='application/zip',headers={'Content-Disposition':'attachment; filename=test.zip'},body=b'zip-fixture')
            else:
                route.fulfill(content_type='text/html; charset=utf-8',body=html)
        page.route('**/*',route_fixture)
        cfg={'timeout_ms':500,'download_timeout_ms':2000,'selectors':{'admin_search':{'by':'role','value':'textbox'}},'table':{'name_index':2,'course_index':3,'id_index':1}}
        try:
            self.assertEqual(c.admin_rows(page,cfg,record()),list('012345'))
            self.assertEqual(page.get_by_role('textbox').input_value(),NAME)
            with tempfile.TemporaryDirectory() as folder:
                target=Path(folder)/'test.zip'
                c.download_bulk(page,target,6,cfg)
                self.assertEqual(target.read_bytes(),b'zip-fixture')
            with self.assertRaises(c.StopRun):
                c.admin_rows(page,cfg,{**record(),'name':'OTRO ALUMNO'})
        finally:
            page.close()

    def test_final_requires_matching_profile_and_completed_course(self):
        cfg=json.loads(Path('config_certificados.example.json').read_text(encoding='utf-8'))
        cfg.update(allow_final_certificate=True,timeout_ms=1500,download_timeout_ms=1500)
        for correct_identity,complete in [(False,True),(True,False),(True,True)]:
            emitted=[]
            def handler(route):
                request=route.request
                if request.url.endswith('/emit'):
                    emitted.append(True)
                    route.fulfill(status=200,content_type='application/pdf',headers={'Content-Disposition':'attachment; filename=final.pdf'},body=b'%PDF-fixture')
                    return
                if request.url.endswith('/profile'):
                    email=record()['email'] if correct_identity else 'otro@example.test'
                    html=f'<input type="email" value="{email}"><label>Nombre completo<input value="{NAME}"></label>'
                elif request.url.endswith('/login'):
                    html='<input type="email"><input type="password"><button onclick="sessionStorage.ok=1;location.href=\'/home\'">Iniciar sesión</button>'
                elif request.url.endswith('/home'):
                    html='<a href="/profile">Mi perfil</a>'
                else:
                    progress='100%' if complete else '80%'
                    lessons='5 de 5' if complete else '4 de 5'
                    html=f'''<a href="/login">Iniciar sesión</a>
                    <div id="card"><h3>Tu progreso</h3><span>{progress}</span><p>{lessons} lecciones completadas</p><div><p>¡Curso completado!</p><button onclick="location.href='/emit'">Obtener Certificado</button></div></div>
                    <script>if(!sessionStorage.ok)document.getElementById('card').remove()</script>'''
                route.fulfill(content_type='text/html; charset=utf-8',body=html)
            with tempfile.TemporaryDirectory() as folder:
                browser_contexts_before=len(self.browser.contexts)
                # Route at browser-context creation, keeping the real isolation behavior.
                class RoutedBrowser:
                    def new_context(inner,**kwargs):
                        context=self.browser.new_context(**kwargs)
                        context.route('**/*',handler)
                        return context
                ledger=Mock()
                ledger.status.return_value={}
                with patch.object(c,'inspect_pdf',return_value=5):
                    if correct_identity and complete:
                        c.generate_final(RoutedBrowser(),cfg,record(),ledger,Path(folder)/'final.pdf')
                        self.assertEqual(len(emitted),1)
                        ledger.set.assert_called_once()
                    else:
                        with self.assertRaises(c.StopRun):
                            c.generate_final(RoutedBrowser(),cfg,record(),ledger,Path(folder)/'final.pdf')
                        self.assertEqual(emitted,[])
                        ledger.set.assert_not_called()
                self.assertEqual(len(self.browser.contexts),browser_contexts_before)

    def test_search_handles_enye_and_mixed_name_order(self):
        name='ALICIA BEATRIZ PEREZ MUÑOZ'
        page=self.browser.new_page()
        names=[name]*5+['PEREZ MUÑOZ ALICIA BEATRIZ']
        rows=''.join(f'<tr><td></td><td>CERT-{i}</td><td>{n}</td><td>{c.COURSE_TITLE}</td><td>fecha</td><td><button>Descargar</button></td></tr>' for i,n in enumerate(names))
        html=f'''<input oninput="let ok=this.value==='BEATRIZ';document.querySelector('tbody').hidden=!ok;document.querySelector('p').hidden=!ok">
        <table><tbody hidden>{rows}</tbody></table><p hidden>6 certificados · Página 1 de 1</p>'''
        page.route('**/*',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=html))
        cfg={'timeout_ms':1500,'selectors':{'admin_search':{'by':'role','value':'textbox'}},'table':{'name_index':2,'course_index':3,'id_index':1}}
        try:
            self.assertEqual(c.admin_rows(page,cfg,{**record(),'name':name}),list('012345'))
            self.assertEqual(page.get_by_role('textbox').input_value(),'BEATRIZ')
        finally:
            page.close()


if __name__=='__main__':
    unittest.main()
