import unittest
from types import SimpleNamespace
from unittest.mock import patch
from seguimiento_global import StopRun
from seguimiento_global import GlobalSheet, HEADER, parse_percentage, build_records, check_account, email_key, locate, select_from_row, consult_rows, wait_for_login


class GlobalTests(unittest.TestCase):
    def test_queries_all_percentages_without_manual_confirmation(self):
        identities={2:{},3:{},4:{},5:{}}
        with patch('builtins.input',side_effect=AssertionError('No debe pedir confirmación')), \
             patch('seguimiento_global.read_global',side_effect=[0,.2,1,.8]) as read, \
             patch('seguimiento_global.time.sleep'):
            self.assertEqual(consult_rows(object(),{'delay_seconds':1},identities,[2,3,4,5]),{2:0,3:.2,4:1,5:.8})
            self.assertEqual(read.call_count,4)

    def test_login_waits_for_visible_session_without_terminal_input(self):
        calls=[]
        control=SimpleNamespace(wait_for=lambda **kwargs:calls.append(kwargs))
        page=SimpleNamespace(get_by_text=lambda value,exact:control)
        with patch('builtins.input',side_effect=AssertionError('No debe pedir Enter')):
            wait_for_login(page,{})
        self.assertEqual(calls,[{'state':'visible','timeout':300000}])
    def test_from_246_includes_246_and_excludes_all_earlier_rows(self):
        rows=select_from_row({r:{} for r in range(2,491)},246,999)
        self.assertEqual(rows,list(range(246,491)))
        self.assertEqual(len(rows),245)

    def test_from_row_preserves_real_sheet_positions_and_skips_blanks(self):
        self.assertEqual(select_from_row({250:{},2:{},246:{},490:{}},246,999),[246,250,490])

    def test_from_row_rejects_invalid_limits(self):
        for first in [0,1,-1,1000,2.5]:
            with self.assertRaises(StopRun):
                select_from_row({246:{}},first,999)

    def test_from_row_rejects_empty_selection(self):
        with self.assertRaises(StopRun):
            select_from_row({2:{},245:{}},246,999)




    def test_search_can_match_observed_partial_accessible_name(self):
        calls=[]
        scope=SimpleNamespace(get_by_role=lambda value,**kwargs:calls.append((value,kwargs)))
        locate(scope,{"by":"role","value":"textbox","name":"Buscar estudiantes por nombre","exact":False})
        self.assertFalse(calls[-1][1]["exact"])
        locate(scope,{"by":"role","value":"button","name":"Ver"})
        self.assertTrue(calls[-1][1]["exact"])

    def test_email_and_name_must_both_match(self):
        expected={"email":"student@example.com","name":"alumna ejemplo"}
        check_account(" ALUMNA  EJEMPLO ","Student@example.com",expected)
        for name,email in [("otra alumna","student@example.com"),("alumna ejemplo","other@example.com")]:
            with self.assertRaises(StopRun):
                check_account(name,email,expected)

    def test_no_gmail_alias_merging(self):
        self.assertNotEqual(email_key("a.b@example.com"),email_key("ab@example.com"))
        self.assertNotEqual(email_key("a+one@example.com"),email_key("a@example.com"))

    def test_records_preserve_rows_and_cedula(self):
        records=build_records([["0123456789","Alumno Ejemplo","student@example.com"],[],
                               ["0987654321","Otra Alumna","another@example.com"]],2)
        self.assertEqual(set(records),{2,4})
        self.assertEqual(records[2]["cedula"],"0123456789")

    def test_duplicate_email_and_missing_fields_block(self):
        bad_lists=[[["0123456789","Uno","same@example.com"],["0987654321","Dos","SAME@example.com"]],
                   [["0123456789","Uno",""]],[["0123456789","","one@example.com"]],
                   [["","Uno","one@example.com"]]]
        for values in bad_lists:
            with self.assertRaises(StopRun) as caught:
                build_records(values,len(values))
            self.assertNotIn("example.com",str(caught.exception))

    def fake_sheet(self, values):
        sheet=GlobalSheet.__new__(GlobalSheet)
        sheet.c={"sheet_id":0,"last_row":4}
        sheet.sid="fake"
        sheet.prefix="'Hoja 1'!"
        sheet.protections=[]
        data={"sheets":[{"data":[{"rowData":[{"values":[{"userEnteredValue":v}]} for v in values]}]}]}
        sheet.api=SimpleNamespace(spreadsheets=lambda:SimpleNamespace(
            get=lambda **kwargs:SimpleNamespace(execute=lambda:data)))
        return sheet

    def test_empty_destination_accepted(self):
        self.assertEqual(self.fake_sheet([{}]).snapshot([2]),{1:{},2:{},3:{},4:{}})

    def test_other_header_or_data_is_rejected(self):
        for values in [[{"stringValue":"OTRO DATO"}],[{},{"numberValue":.6}],
                       [{"stringValue":HEADER},{"formulaValue":"=1"}]]:
            with self.assertRaises(StopRun):
                self.fake_sheet(values).snapshot([2])

    def test_existing_global_results_accepted(self):
        cells=self.fake_sheet([{"stringValue":HEADER},{"numberValue":0},{"numberValue":1}]).snapshot([2,3])
        self.assertEqual(cells[2],{"numberValue":0})

    def test_protected_l_is_rejected(self):
        sheet=self.fake_sheet([{}])
        sheet.protections=[{"range":{"startColumnIndex":11,"endColumnIndex":12},"requestingUserCanEdit":False}]
        with self.assertRaises(StopRun):
            sheet.snapshot([2])

    def test_percentage_values(self):
        for text,expected in [("0%",0),("60%",.6),("100%",1),(" 12,5 % ",.125)]:
            self.assertAlmostEqual(parse_percentage(text),expected)

    def test_missing_or_unrelated_text_is_not_zero(self):
        for text in ["", "Sin curso", "Completado", "100", "6 certificados 100%", "101%", "-1%"]:
            with self.subTest(text=text), self.assertRaises(StopRun):
                parse_percentage(text)

    def test_requests_can_only_write_l(self):
        sheet=GlobalSheet.__new__(GlobalSheet)
        sheet.c={"sheet_id":0}
        for row,value,percent in [(1,{"stringValue":HEADER},False),(489,{"numberValue":.6},True)]:
            request=sheet.request(row,value,percent)["updateCells"]
            self.assertEqual(request["range"]["startColumnIndex"],11)
            self.assertEqual(request["range"]["endColumnIndex"],12)
            self.assertEqual(request["rows"][0]["values"][0]["userEnteredValue"],value)
            self.assertNotIn("formulaValue",value)

    def test_identity_reorder_blocks_commit(self):
        sheet=GlobalSheet.__new__(GlobalSheet)
        sheet.identities=lambda:{3:"0123456789"}
        sheet.write=lambda requests:self.fail("No debe escribir")
        with self.assertRaises(StopRun):
            sheet.commit({2:.6},{2:"0123456789"},{1:{},2:{}})

    def test_concurrent_l_change_blocks_commit(self):
        sheet=GlobalSheet.__new__(GlobalSheet)
        sheet.identities=lambda:{2:"0123456789"}
        sheet.snapshot=lambda rows:{1:{"stringValue":HEADER},2:{"numberValue":.8}}
        sheet.write=lambda requests:self.fail("No debe sobrescribir")
        with self.assertRaises(StopRun):
            sheet.commit({2:.6},{2:"0123456789"},{1:{"stringValue":HEADER},2:{"numberValue":.4}})


if __name__ == "__main__":
    unittest.main()
