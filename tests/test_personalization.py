import sqlite3
from pathlib import Path
import tempfile
import unittest
from personalization import PersonalStore, edit_pairs


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'personal.sqlite3'
        self.store=PersonalStore(self.path)
        self.before='给我几张电图作为参考。'
        self.after='给我几张垫图作为参考。'

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def learn(self):
        self.store.observe_edit(self.before,self.after,'utterance-1')
        self.store.observe_edit(self.before,self.after,'utterance-2')

    def test_new_profile_preserves_correct_words_even_if_second_model_disagrees(self):
        for source,candidate in [('我想买书','我想卖书。'),('给我电图','给我垫图。'),
                ('上午十点不对下午三点','上午十点不要改成下午三点。'),('这个接扣很好','这个接口很好。')]:
            self.assertEqual(self.store.acoustic(source,candidate).text,source)
        self.assertEqual(self.store.acoustic('这个接口很好','这个接口很好。').text,'这个接口很好。')

    def test_frequent_recognition_is_never_its_own_training_label(self):
        for _ in range(6):
            self.store.observe_usage(self.before)
        self.assertFalse(self.store.rules())
        self.assertEqual(self.store.apply(self.before).text,self.before)
        self.assertEqual(self.store.phrases()[0]['count'],6)

    def test_two_independent_edits_required_and_exact_context_is_preserved(self):
        self.store.observe_edit(self.before,self.after,'utterance-1')
        self.assertEqual(self.store.apply(self.before).text,self.before)
        self.store.observe_edit(self.before,self.after,'utterance-1')
        self.assertEqual(self.store.rules()[0]['count'],1)
        self.store.observe_edit(self.before,self.after,'utterance-2')
        self.assertEqual(self.store.apply(self.before).text,self.after)
        self.assertEqual(self.store.apply('医生查看心电图作为参考。').text,'医生查看心电图作为参考。')
        self.assertEqual(self.store.apply('给我几张电图用于教学。').text,'给我几张电图用于教学。')

    def test_first_user_reversal_disables_rule_and_remembers_original(self):
        self.learn()
        result=self.store.observe_edit(self.after,self.before,'revert')
        self.assertEqual(result[0][0],'reverted')
        self.assertEqual(self.store.rules()[0]['status'],'disabled')
        self.assertEqual(self.store.apply(self.before).text,self.before)
        self.assertTrue(any(p['text']=='电图' and p['protected'] for p in self.store.phrases()))

    def test_explicit_rule_reversal_stops_at_once(self):
        self.store.add_rule('电图','垫图')
        self.store.observe_edit(self.after,self.before,'revert')
        self.assertEqual(self.store.rules()[0]['status'],'disabled')

    def test_profile_isolation_and_restart(self):
        first=self.store.active
        self.learn()
        second=self.store.create_profile('第二个人')
        self.store.select_profile(second)
        self.assertFalse(self.store.rules())
        self.assertEqual(self.store.apply(self.before).text,self.before)
        self.store.close()
        self.store=PersonalStore(self.path)
        self.assertEqual(self.store.active,second)
        self.store.select_profile(first)
        self.assertEqual(self.store.apply(self.before).text,self.after)

    def test_learning_off_does_not_record_but_existing_rules_still_work(self):
        self.learn()
        before=(self.store.rules(),self.store.phrases())
        self.store.set_learning(False)
        self.store.observe_edit('这个接扣','这个接口','new')
        self.store.observe_usage('全新的表达')
        self.assertEqual((self.store.rules(),self.store.phrases()),before)
        self.assertEqual(self.store.apply(self.before).text,self.after)

    def test_nonhomophone_edits_stay_pending_not_automatic_personal_facts(self):
        for n in range(4):
            self.store.observe_edit('下周去上海开会','下周去北京开会',str(n))
        self.assertEqual(self.store.rules()[0]['status'],'pending')
        self.assertEqual(self.store.apply('下周去上海开会').text,'下周去上海开会')

    def test_numbers_negation_and_commands_cannot_be_learned(self):
        for a,b in [('上午十点开会','上午三点开会'),('不要自动发送','需要自动发送'),('准备发宋','准备发送')]:
            self.assertFalse(edit_pairs(a,b))
        for a,b in [('上午十点','上午三点'),('不要发送','直接发送'),('发宋','发送')]:
            with self.assertRaises(ValueError):self.store.add_rule(a,b)

    def test_quotes_and_protected_terms_are_not_replaced(self):
        self.store.add_rule('电图','垫图')
        self.assertEqual(self.store.apply('他写“电图”和`电图`').text,'他写“电图”和`电图`')
        self.store.protect_phrase('心电图')
        self.assertEqual(self.store.apply('心电图与电图').text,'心电图与垫图')

    def test_conflicting_preferences_require_review(self):
        self.learn()
        for n in range(3):self.store.observe_edit(self.before,'给我几张店图作为参考。','other'+str(n))
        self.assertTrue(all(r['status']=='pending' for r in self.store.rules()))
        self.assertEqual(self.store.apply(self.before).text,self.before)

    def test_overlapping_explicit_rules_are_not_arbitrarily_selected(self):
        self.store.add_rule('电图','垫图')
        self.store.add_rule('电图','店图')
        self.assertEqual(self.store.apply('电图').text,'电图')

    def test_one_pass_never_cascades(self):
        self.store.add_rule('电图','垫图')
        self.store.add_rule('垫图','店图')
        self.assertEqual(self.store.apply('电图').text,'垫图')

    def test_deletion_and_noop_are_not_training(self):
        self.assertFalse(self.store.observe_edit(self.before,'','x'))
        self.assertFalse(self.store.observe_edit(self.before,self.before,'y'))
        self.assertFalse(self.store.rules())

    def test_long_transcript_is_not_saved(self):
        prefix='一段不会写入词库的独立长原文标记。'*70
        self.store.observe_edit(prefix+self.before,prefix+self.after,'x')
        self.assertLess(len(self.store.rules()[0]['source']),17)
        self.assertNotIn(prefix[:30].encode('utf-8'),self.path.read_bytes())

    def test_delete_clear_and_profile_rename(self):
        self.learn()
        key=self.store.rules()[0]['id']
        self.store.delete_rule(key)
        self.assertFalse(self.store.rules())
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM votes').fetchone()[0],0)
        self.store.rename('本人的用语')
        self.assertEqual(self.store.profiles()[0]['name'],'本人的用语')
        self.store.clear()
        self.assertFalse(self.store.phrases())

    def test_corrupt_database_is_preserved(self):
        bad=Path(self.folder.name)/'corrupt.sqlite3'
        bad.write_bytes(b'not a database')
        store=PersonalStore(bad)
        try:
            self.assertTrue(store.error)
            store.observe_usage('临时用语')
            self.assertEqual(bad.read_bytes(),b'not a database')
        finally:store.close()


if __name__=='__main__':unittest.main()
