#!/usr/bin/env python3
"""
test_patch.py

Unit test for patch_geyser.py constant pool replacement and verification.
"""

import os
import struct
import tempfile
import unittest
import zipfile

from patch_geyser import TARGET_CLASS, OLD_UTF8_BYTES, NEW_UTF8_BYTES, inspect_jar, patch_jar, parse_constant_pool


class TestGeyserPatch(unittest.TestCase):
    def setUp(self):
        # Construct a synthetic minimal class file with a valid constant pool
        # Magic (4) + Minor (2) + Major (2) + CP Count (2)
        # Entry 1: Utf8 "dummy"
        # Entry 2: Utf8 "asCraftMirror"
        # Entry 3: Class referencing entry 1
        cp_entries = [
            b"\x01\x00\x05dummy",
            OLD_UTF8_BYTES,
            b"\x07\x00\x01",
        ]
        cp_count = len(cp_entries) + 1  # 1-indexed

        header = struct.pack(">IHHH", 0xCAFEBABE, 0, 65, cp_count)
        body = b"".join(cp_entries)
        # Trailing dummy class data: access_flags (2), this_class (2), super_class (2), interfaces_count (2), fields_count (2), methods_count (2), attributes_count (2)
        tail = struct.pack(">HHHHHHH", 0x0021, 3, 0, 0, 0, 0, 0)
        self.mock_class_data = header + body + tail

    def test_parse_constant_pool(self):
        pool, offset = parse_constant_pool(self.mock_class_data)
        self.assertEqual(len(pool), 4)
        self.assertEqual(pool[1], ("Utf8", "dummy"))
        self.assertEqual(pool[2], ("Utf8", "asCraftMirror"))

    def test_patch_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_jar = os.path.join(tmpdir, "Geyser-Spigot.jar")
            output_jar = os.path.join(tmpdir, "Geyser-Spigot-patched.jar")

            # Create synthetic jar
            with zipfile.ZipFile(input_jar, "w") as z:
                z.writestr(TARGET_CLASS, self.mock_class_data)
                z.writestr("test.txt", "hello")

            # Check status before patch
            status, _ = inspect_jar(input_jar)
            self.assertEqual(status, "vulnerable")

            # Patch
            res = patch_jar(input_jar, output_jar)
            self.assertTrue(res)
            self.assertTrue(os.path.exists(output_jar))

            # Check status after patch
            status_post, patched_bytes = inspect_jar(output_jar)
            self.assertEqual(status_post, "already_patched")

            # Verify patched constant pool
            pool, _ = parse_constant_pool(patched_bytes)
            self.assertEqual(pool[2], ("Utf8", "asBukkitMirror"))

            # Verify non-target files intact
            with zipfile.ZipFile(output_jar, "r") as z:
                self.assertEqual(z.read("test.txt"), b"hello")


if __name__ == "__main__":
    unittest.main()
