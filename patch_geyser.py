#!/usr/bin/env python3
"""
patch_geyser.py

Patches Geyser-Spigot bytecode to resolve the Paper 26.3+ reflection startup crash:
java.lang.IllegalArgumentException: Couldn't find asBukkitCopy or asCraftMirror method on CraftItemStack
    at org.geysermc.geyser.platform.spigot.shaded.org.incendo.cloud.bukkit.parser.ItemStackParser$ModernParser.<clinit>

Root cause:
incendo.cloud searches for CraftItemStack.asCraftMirror(ItemStack) as fallback when
asBukkitCopy has a modern signature mismatch. In CraftBukkit/Paper, the method
is named asBukkitMirror(ItemStack).

This patch modifies constant pool entry #336 in ModernParser.class:
  \x01\x00\x0dasCraftMirror -> \x01\x00\x0easBukkitMirror
"""

import os
import sys
import shutil
import struct
import zipfile
import argparse

TARGET_CLASS = (
    "org/geysermc/geyser/platform/spigot/shaded/org/incendo/cloud/bukkit/parser/ItemStackParser$ModernParser.class"
)

# JVM CONSTANT_Utf8 tags: tag (0x01) + length (uint16) + string
OLD_UTF8_BYTES = b"\x01\x00\x0dasCraftMirror"
NEW_UTF8_BYTES = b"\x01\x00\x0easBukkitMirror"


def parse_constant_pool(class_bytes: bytes):
    """
    Parses the constant pool of a compiled Java class file to verify bytecode validity.
    Returns (pool_entries, end_offset).
    """
    if len(class_bytes) < 10:
        raise ValueError("Class file too small")

    magic, minor, major, cp_count = struct.unpack(">IHHH", class_bytes[:10])
    if magic != 0xCAFEBABE:
        raise ValueError(f"Invalid class magic: 0x{magic:08X}")

    pool = [None]
    idx = 10
    i = 1
    while i < cp_count:
        if idx >= len(class_bytes):
            raise ValueError("Unexpected EOF while parsing constant pool")
        tag = class_bytes[idx]
        idx += 1
        if tag == 1:  # CONSTANT_Utf8
            length, = struct.unpack(">H", class_bytes[idx:idx + 2])
            idx += 2
            val = class_bytes[idx:idx + length].decode("utf-8", errors="replace")
            idx += length
            pool.append(("Utf8", val))
        elif tag in (3, 4):  # Integer, Float
            idx += 4
            pool.append(("Primitive4",))
        elif tag in (5, 6):  # Long, Double (takes 2 pool entries)
            idx += 8
            pool.append(("Primitive8",))
            pool.append(None)
            i += 1
        elif tag in (7, 8):  # Class, String
            idx += 2
            pool.append(("Ref1",))
        elif tag in (9, 10, 11, 12):  # Field, Method, InterfaceMethod, NameAndType
            idx += 4
            pool.append(("Ref2",))
        elif tag in (15,):  # MethodHandle
            idx += 3
            pool.append(("MethodHandle",))
        elif tag in (16, 19, 20):  # MethodType, Module, Package
            idx += 2
            pool.append(("Ref1",))
        elif tag in (17, 18):  # Dynamic, InvokeDynamic
            idx += 4
            pool.append(("Dynamic",))
        else:
            raise ValueError(f"Unknown constant pool tag: {tag} at byte {idx - 1}")
        i += 1

    return pool, idx


def inspect_jar(jar_path: str):
    """
    Inspects a Geyser-Spigot jar to check if it contains the target class
    and whether it is vulnerable, patched, or unknown.
    """
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"Jar not found: {jar_path}")

    with zipfile.ZipFile(jar_path, "r") as z:
        namelist = z.namelist()
        if TARGET_CLASS not in namelist:
            return "not_found", None

        class_bytes = z.read(TARGET_CLASS)
        if NEW_UTF8_BYTES in class_bytes:
            return "already_patched", class_bytes
        elif OLD_UTF8_BYTES in class_bytes:
            return "vulnerable", class_bytes
        else:
            return "unknown_state", class_bytes


def patch_jar(input_jar: str, output_jar: str):
    """
    Extracts, patches the target class file in-memory, and writes a new jar.
    """
    status, class_bytes = inspect_jar(input_jar)
    if status == "not_found":
        raise RuntimeError(
            f"Could not find target class '{TARGET_CLASS}' in {input_jar}. "
            "Ensure you provided a valid Geyser-Spigot jar."
        )
    if status == "already_patched":
        print(f"[*] {input_jar} is ALREADY patched with 'asBukkitMirror'. Nothing to do.")
        return False
    if status != "vulnerable":
        raise RuntimeError(
            f"Could not find expected pattern 'asCraftMirror' in {TARGET_CLASS}. Class state: {status}"
        )

    # Perform substitution
    patched_class = class_bytes.replace(OLD_UTF8_BYTES, NEW_UTF8_BYTES)

    # Sanity-check the patched class file by parsing its constant pool
    pool, _ = parse_constant_pool(patched_class)
    found_replacement = any(item and item[0] == "Utf8" and item[1] == "asBukkitMirror" for item in pool)
    if not found_replacement:
        raise RuntimeError("Integrity check failed: 'asBukkitMirror' missing from patched constant pool.")

    print(f"[*] Bytecode patch verified: successfully parsed {len(pool)} constant pool entries.")

    # Rebuild jar preserving ZIP entry metadata
    temp_output = output_jar + ".tmp"
    try:
        with zipfile.ZipFile(input_jar, "r") as zin, zipfile.ZipFile(
            temp_output, "w", compression=zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == TARGET_CLASS:
                    data = patched_class
                    print(f"[*] Patched {item.filename}")
                zout.writestr(item, data)

        if os.path.exists(output_jar):
            os.remove(output_jar)
        os.rename(temp_output, output_jar)
        print(f"[+] Successfully generated patched jar: {output_jar}")
        return True
    finally:
        if os.path.exists(temp_output):
            try:
                os.remove(temp_output)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Patch Geyser-Spigot bytecode for Paper 26.3+ CraftItemStack reflection compatibility."
    )
    parser.add_argument("input_jar", help="Path to original Geyser-Spigot.jar")
    parser.add_argument(
        "output_jar",
        nargs="?",
        default=None,
        help="Path to save patched jar (defaults to <name>-patched.jar unless --in-place)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Patch input jar in-place (creates a .bak backup first)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check status of jar without modifying it",
    )

    args = parser.parse_args()

    if not os.path.exists(args.input_jar):
        print(f"[-] Error: input file not found: {args.input_jar}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        status, _ = inspect_jar(args.input_jar)
        print(f"Jar status: {status}")
        if status == "vulnerable":
            print("Status: VULNERABLE (needs patch: contains 'asCraftMirror')")
            sys.exit(1)
        elif status == "already_patched":
            print("Status: PATCHED (contains 'asBukkitMirror')")
            sys.exit(0)
        elif status == "not_found":
            print("Status: TARGET NOT FOUND (not a standard Geyser-Spigot build)")
            sys.exit(2)
        else:
            print("Status: UNKNOWN")
            sys.exit(3)

    if args.in_place:
        backup_path = args.input_jar + ".bak"
        if not os.path.exists(backup_path):
            shutil.copy2(args.input_jar, backup_path)
            print(f"[*] Created backup at {backup_path}")
        patch_jar(backup_path, args.input_jar)
    else:
        output_jar = args.output_jar
        if not output_jar:
            base, ext = os.path.splitext(args.input_jar)
            output_jar = f"{base}-patched{ext}"
        patch_jar(args.input_jar, output_jar)


if __name__ == "__main__":
    main()
