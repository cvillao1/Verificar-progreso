import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from contextlib import contextmanager

from seguimiento_global import Progress, StopRun, HEADER, GlobalSheet, consult_rows, state_lock, run_update


class MemorySheet:
    def __init__(self, identities):
        self.records = copy.deepcopy(identities)
        self.cells = {1:{}}
        self.c = {"sheet_id":0}
        self.offline = False
        self.lose_reply = False
        self.writes = []

    request = GlobalSheet.request

    def identities(self):
        if self.offline:
            raise ConnectionError("offline")
        return copy.deepcopy(self.records)

    def snapshot(self, rows):
        if self.offline:
            raise ConnectionError("offline")
        return {r:copy.deepcopy(self.cells.get(r,{})) for r in [1]+list(rows)}

    def write(self, requests):
        if self.offline:
            raise ConnectionError("offline")
        self.writes.append(copy.deepcopy(requests))
        for request in requests:
            update = request["updateCells"]
            assert update["range"]["startColumnIndex"] == 11
            assert update["range"]["endColumnIndex"] == 12
            row = update["range"]["startRowIndex"] + 1
            self.cells[row] = update["rows"][0]["values"][0]["userEnteredValue"]
        if self.lose_reply:
            self.lose_reply = False
            raise ConnectionError("Se perdió la respuesta después de aplicar")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/".eduqairos-state"/"avance.json"
        self.cfg = {"spreadsheet_id":"example", "sheet_id":0,"course_title":"Curso prueba","last_row":999,"delay_seconds":1}
        self.ids = {2:{"cedula":"0123456789","name":"uno","email":"uno@example.com"},
                    3:{"cedula":"0987654321","name":"dos","email":"dos@example.com"},
                    4:{"cedula":"0234567890","name":"tres","email":"tres@example.com"}}
        self.sheet = MemorySheet(self.ids)

    def tearDown(self):
        self.temp.cleanup()

    def journal(self, identities=None, first=None):
        return Progress(self.path,self.cfg,identities or self.ids,first)

    def record(self,journal,row,value):
        journal.record(row,value,self.ids[row],self.sheet.cells.get(row,{}))

    def test_name_error_keeps_first_result_and_resumes_failed_student(self):
        p = self.journal()
        with patch('seguimiento_global.read_global', side_effect=[.4,StopRun('Nombre distinto')]), \
             patch('seguimiento_global.time.sleep'), self.assertRaises(StopRun):
            consult_rows(None,self.cfg,self.ids,[2,3,4],
                         on_result=lambda row,value:self.record(p,row,value))
        p.flush(self.sheet)
        self.assertEqual(self.sheet.cells[2],{"numberValue":.4})
        corrected=copy.deepcopy(self.ids)
        corrected[3]["name"]="nombre corregido"
        self.assertEqual(self.journal(corrected).remaining(corrected),[3,4])

    def test_offline_results_recovered_before_new_queries(self):
        p=self.journal()
        self.record(p,2,.6)
        self.record(p,3,1)
        self.sheet.offline=True
        with self.assertRaises(ConnectionError):
            p.flush(self.sheet)
        self.sheet.offline=False
        resumed=self.journal()
        self.assertTrue(resumed.resumed)
        self.assertEqual(resumed.flush(self.sheet),2)
        self.assertEqual(self.sheet.cells[2],{"numberValue":.6})
        self.assertEqual(resumed.remaining(self.ids),[4])

    def test_lost_write_reply_does_not_repeat_queries_or_rewrite(self):
        p=self.journal()
        self.record(p,2,.8)
        self.sheet.lose_reply=True
        with self.assertRaises(ConnectionError):
            p.flush(self.sheet)
        resumed=self.journal()
        self.assertEqual(resumed.flush(self.sheet),1)
        self.assertEqual(len(self.sheet.writes),1)
        self.assertEqual(resumed.remaining(self.ids),[3,4])

    def test_reordering_matches_cedula_fingerprint_not_old_row(self):
        p=self.journal()
        self.record(p,2,.2)
        reordered={10:self.ids[2],2:self.ids[3],3:self.ids[4]}
        self.sheet.records=copy.deepcopy(reordered)
        resumed=self.journal(reordered)
        resumed.flush(self.sheet)
        self.assertEqual(self.sheet.cells[10],{"numberValue":.2})
        self.assertNotIn(2,self.sheet.cells)
        self.assertEqual(resumed.remaining(reordered),[2,3])

    def test_changed_completed_identity_is_queried_again(self):
        p=self.journal()
        self.record(p,2,.4)
        changed=copy.deepcopy(self.ids)
        changed[2]["email"]="corregido@example.com"
        resumed=self.journal(changed)
        self.assertEqual(resumed.remaining(changed),[2,3,4])
        self.assertEqual(resumed.data["results"],{})

    def test_concurrent_percentage_edit_is_not_overwritten(self):
        p=self.journal()
        self.record(p,2,.4)
        self.sheet.cells[2]={"numberValue":.8}
        with self.assertRaises(StopRun):
            p.flush(self.sheet)
        self.assertEqual(self.sheet.writes,[])
        self.assertEqual(self.sheet.cells[2],{"numberValue":.8})
        self.assertFalse(self.journal().data["complete"])

    def test_completed_run_allows_next_fresh_update(self):
        p=self.journal()
        for row in self.ids:
            self.record(p,row,.4)
        p.flush(self.sheet)
        p.finish()
        next_run=self.journal()
        self.assertFalse(next_run.resumed)
        self.assertEqual(next_run.remaining(self.ids),[2,3,4])

    def test_from_row_scope_kept_when_resuming_without_flag(self):
        p=self.journal(first=3)
        self.record(p,3,.2)
        resumed=self.journal()
        self.assertEqual(resumed.remaining(self.ids),[4])
        with self.assertRaises(StopRun):
            self.journal(first=4)

    def test_different_course_cannot_reuse_unfinished_results(self):
        self.journal()
        cfg={**self.cfg,"course_title":"Otro curso"}
        with self.assertRaises(StopRun):
            Progress(self.path,cfg,self.ids)

    def test_missing_student_blocks_unsafe_resume(self):
        self.journal()
        with self.assertRaises(StopRun):
            self.journal({2:self.ids[2],3:self.ids[3]})

    def test_private_identifiers_are_not_saved_in_plaintext(self):
        p=self.journal()
        self.record(p,2,.4)
        raw=self.path.read_text(encoding="utf-8")
        for identity in self.ids.values():
            for value in identity.values():
                self.assertNotIn('"'+value+'"',raw)
        self.assertEqual(json.loads(raw)["results"][p.key(self.ids[2])]["value"],.4)

    def test_corrupt_file_is_not_replaced(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{bad',encoding='utf-8')
        with self.assertRaises(StopRun):
            self.journal()
        self.assertEqual(self.path.read_text(),'{bad')

    def test_failed_atomic_save_keeps_previous_file(self):
        p=self.journal()
        before=self.path.read_bytes()
        with patch('seguimiento_global.os.replace',side_effect=OSError('disk failure')),self.assertRaises(OSError):
            self.record(p,2,.4)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.journal().remaining(self.ids),[2,3,4])

    def test_second_run_cannot_share_recovery_file(self):
        with state_lock(self.path):
            with self.assertRaises(StopRun):
                with state_lock(self.path):
                    self.fail('Una segunda ejecución no debe entrar')
        with state_lock(self.path):
            pass

    def test_update_flushes_on_error_then_resumes_without_repeating_success(self):
        page=SimpleNamespace(set_default_timeout=lambda value:None,goto=lambda url:None)
        context=SimpleNamespace(new_page=lambda:page,close=lambda:None)
        browser=SimpleNamespace(new_context=lambda **kwargs:context,close=lambda:None)
        runtime=SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs:browser))
        @contextmanager
        def fake_playwright():
            yield runtime
        modules={'playwright':SimpleNamespace(),
                 'playwright.sync_api':SimpleNamespace(sync_playwright=fake_playwright)}
        cfg={**self.cfg,'timeout_ms':20000}
        args=SimpleNamespace(write=True,from_row=None,pause_on_error=False)
        with patch.dict('sys.modules',modules),patch('seguimiento_global.STATE_PATH',self.path), \
             patch('seguimiento_global.wait_for_login'),patch('seguimiento_global.time.sleep'), \
             patch('builtins.input',side_effect=AssertionError('No debe pedir confirmación')):
            with patch('seguimiento_global.read_global',side_effect=[.4,StopRun('Nombre distinto')]),self.assertRaises(StopRun):
                run_update(args,cfg,self.sheet,self.ids)
            self.assertEqual(self.sheet.cells[2],{'numberValue':.4})
            self.sheet.records[3]['name']='nombre corregido'
            with patch('seguimiento_global.read_global',side_effect=[.2,1]) as read:
                run_update(args,cfg,self.sheet,self.sheet.identities())
                self.assertEqual(read.call_count,2)
                self.assertEqual(read.call_args_list[0].args[2]['cedula'],self.ids[3]['cedula'])
            self.assertTrue(json.loads(self.path.read_text())['complete'])
            self.assertEqual(self.sheet.cells[4],{'numberValue':1})

    def test_keyboard_interrupt_also_preserves_successful_reading(self):
        p=self.journal()
        with patch('seguimiento_global.read_global',side_effect=[.6,KeyboardInterrupt()]), \
             patch('seguimiento_global.time.sleep'),self.assertRaises(KeyboardInterrupt):
            consult_rows(None,self.cfg,self.ids,[2,3,4],
                         on_result=lambda row,value:self.record(p,row,value))
        self.assertEqual(self.journal().remaining(self.ids),[3,4])


if __name__=='__main__':
    unittest.main()
