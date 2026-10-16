import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from draft_store import DraftStore


class DraftRecoveryTests(unittest.TestCase):
    def test_preserves_exact_multiline_text_refs_and_intentional_clear(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft.json';store=DraftStore(path)
            original=dict(dictation='我手动改好的 skill。\n\n',workflow='制作色卡\n',refs=[str(Path(folder)/'图.png')])
            self.assertTrue(store.save(original))
            self.assertEqual(DraftStore(path).load(),original)
            snapshot=path.read_bytes()
            self.assertFalse(store.save(original))
            self.assertEqual(path.read_bytes(),snapshot)
            cleared=dict(original,dictation='')
            store.save(cleared)
            self.assertEqual(DraftStore(path).load(),cleared)
            self.assertEqual(DraftStore.read(store.backup),original)

    def test_damaged_latest_uses_valid_backup_without_losing_damaged_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft.json';store=DraftStore(path)
            first=dict(dictation='第一个草稿',workflow='生图',refs=[])
            store.save(first);store.save(dict(first,dictation='第二个草稿'))
            damaged=b'{partial-json'
            path.write_bytes(damaged)
            recovered=DraftStore(path)
            self.assertEqual(recovered.load(),first)
            self.assertIsNotNone(recovered.error)
            recovered.save(first)
            self.assertEqual(DraftStore.read(path),first)
            self.assertEqual(DraftStore.read(store.backup),first)
            damaged_files=list(path.parent.glob('draft-unreadable-*.json'))
            self.assertEqual(len(damaged_files),1)
            self.assertEqual(damaged_files[0].read_bytes(),damaged)

    def test_failed_atomic_replace_keeps_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft.json';store=DraftStore(path)
            original=dict(dictation='已有草稿',workflow='',refs=[])
            store.save(original)
            import os
            replace=os.replace
            def refuse_latest(source,target):
                if Path(target)==path:raise PermissionError('simulated locked file')
                return replace(source,target)
            with patch('draft_store.os.replace',refuse_latest):
                with self.assertRaises(PermissionError):
                    store.save(dict(original,dictation='新草稿'))
            self.assertEqual(DraftStore(path).load(),original)
            self.assertEqual(store.last,original)
            self.assertEqual(list(path.parent.glob('*.tmp')),[])

    def test_bom_legacy_and_invalid_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'draft.json'
            path.write_text(json.dumps({'dictation':'原文\n','workflow':'色卡'}),'utf-8-sig')
            self.assertEqual(DraftStore.read(path),dict(dictation='原文\n',workflow='色卡',refs=[]))
            for data in ({'refs':'not-a-list'},{'dictation':None},{'schema_version':2},{'refs':[1]}):
                with self.assertRaises(ValueError):DraftStore(path).save(data)


if __name__=='__main__':unittest.main()
