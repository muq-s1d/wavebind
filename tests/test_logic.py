import unittest

from wavebind import inject


class Keys(unittest.TestCase):
    def test_parse_aliases(self):
        self.assertEqual(inject.parse_keys("super+Page_Down"), ["Super_L", "Page_Down"])
        self.assertEqual(inject.parse_keys("Ctrl + alt+Left"), ["Control_L", "Alt_L", "Left"])

    def test_keysym(self):
        # values from xkbcommon-keysyms.h
        self.assertEqual(inject.keysym("Super_L"), 0xFFEB)
        self.assertEqual(inject.keysym("Page_Down"), 0xFF56)
        self.assertEqual(inject.keysym("XF86AudioPlay"), 0x1008FF14)
        self.assertEqual(inject.keysym("print"), 0xFF61)  # case-insensitive fallback
        with self.assertRaises(ValueError):
            inject.keysym("NotAKey")


if __name__ == "__main__":
    unittest.main()
