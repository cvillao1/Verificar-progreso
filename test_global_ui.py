"""Prueba aislada: todas las páginas se simulan; no se contacta EduQairos ni Sheets."""
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from seguimiento_global import StopRun
from seguimiento_global import URL, read_global, wait_for_login

HTML = '''<!doctype html><html><body>
<input aria-label="Buscar estudiantes por nombre">
<section id="rows"></section>
<div role="dialog" data-state="closed" style="display:none"><h2></h2>
<article class="course"><p>Curso de prueba</p><span class="percentage"></span></article></div>
<script>
const accounts = {'uno@example.com':['Alumna Uno','20%'], 'dos@example.com':['Alumno Dos','100%']};
const input = document.querySelector('input'), rows = document.querySelector('#rows');
const dialog = document.querySelector('[role=dialog]');
function render(email) {
  rows.replaceChildren(); if(!accounts[email]) return;
  const row=document.createElement('div'); row.className='student';
  const name=document.createElement('p'); name.className='name'; name.textContent=accounts[email][0];
  const address=document.createElement('p'); address.className='email'; address.textContent=email;
  const view=document.createElement('button'); view.textContent='Ver';
  view.onclick=()=>{dialog.querySelector('h2').textContent=accounts[email][0];
    dialog.querySelector('.percentage').textContent=accounts[email][1];
    dialog.dataset.state='open'; dialog.style.display='block';};
  row.append(name,address,view); rows.append(row);
}
let timer; input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>render(input.value),150);});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){dialog.dataset.state='closed';dialog.style.display='none';}});
render('uno@example.com');
</script></body></html>'''


class UITests(unittest.TestCase):
    def test_detect_login_without_terminal_confirmation(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context()
            try:
                context.route('**/*',lambda route:route.abort())
                page=context.new_page()
                page.set_content('''<script>setTimeout(()=>{const b=document.createElement('button');
                    b.textContent='Cerrar Sesión'; document.body.append(b);},100);</script>''')
                with patch('builtins.input',side_effect=AssertionError('No debe pedir Enter')):
                    wait_for_login(page,{'login_timeout_ms':2000})
            finally:
                context.close()
                browser.close()

    def test_original_navigation_verifies_email_and_name(self):
        cfg={'delay_seconds':1,'timeout_ms':3000,'course_title':'Curso de prueba','selectors':{
            'search':{'by':'role','value':'textbox','name':'Buscar estudiantes por nombre','exact':False},
            'student_row':'.student','row_name':'.name','row_email':'.email',
            'view':{'by':'role','value':'button','name':'Ver'},
            'detail':'[role="dialog"][data-state="open"]','detail_name':'h2',
            'course':'.course','course_title':'p','course_percentage':'.percentage'}}
        loads=[]
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            context=browser.new_context()
            try:
                def fulfill(route):
                    loads.append(route.request.url)
                    route.fulfill(status=200,content_type='text/html',body=HTML)
                context.route('**/*',fulfill)
                page=context.new_page()
                page.set_default_timeout(3000)
                self.assertEqual(read_global(page,cfg,{'email':'uno@example.com','name':'alumna uno'}),.2)
                self.assertEqual(read_global(page,cfg,{'email':'dos@example.com','name':'alumno dos'}),1)
                self.assertEqual(loads,[URL,URL])
                with self.assertRaises(StopRun):
                    read_global(page,cfg,{'email':'uno@example.com','name':'nombre equivocado'})
                self.assertEqual(loads,[URL,URL,URL])
            finally:
                context.close()
                browser.close()


if __name__=='__main__':
    unittest.main()
