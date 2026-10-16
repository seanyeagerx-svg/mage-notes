"""Regression checks for focus/process changes at the OS injection boundary."""
import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'Windows input adapter')
class ExternalInputGuards(unittest.TestCase):
    def setUp(self):
        import win_input
        self.adapter = win_input
        self.foreground = patch.object(win_input, 'foreground', return_value=101).start()
        self.control = patch.object(win_input, 'focused_control', return_value=202).start()
        self.process = patch.object(win_input, 'window_process', return_value=303).start()
        self.modifiers = patch.object(win_input, 'modifiers_down', return_value=False).start()
        self.send = patch.object(win_input.user32, 'SendInput', side_effect=lambda n, *args: n).start()
        self.addCleanup(patch.stopall)

    def write(self):
        return self.adapter.type_unicode('已有文字。🦞', 101, control=202, process=303)

    def test_matching_target_accepts_unicode(self):
        self.assertTrue(self.write())
        self.send.assert_called_once()

    def test_switch_to_another_field_is_rejected(self):
        self.control.return_value = 909
        self.assertFalse(self.write())
        self.send.assert_not_called()

    def test_same_handle_in_another_process_is_rejected(self):
        self.process.return_value = 404
        self.assertFalse(self.write())
        self.send.assert_not_called()

    def test_field_changes_while_input_batch_is_built(self):
        self.control.side_effect = [202, 909]
        self.assertFalse(self.write())
        self.send.assert_not_called()

    def test_process_changes_before_injection(self):
        self.process.side_effect = [303, 404]
        self.assertFalse(self.write())
        self.send.assert_not_called()

    def test_missing_target_never_types_into_foreground(self):
        self.assertFalse(self.adapter.type_unicode('不得无目标输入'))
        self.send.assert_not_called()

    def test_tail_correction_rechecks_field_before_backspace(self):
        self.control.side_effect = [202, 909]
        self.assertFalse(self.adapter.replace_owned_tail('本期', '本机', 101, 202, process=303))
        self.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
