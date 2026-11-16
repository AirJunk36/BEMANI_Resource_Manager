"""Legacy graphics integration tests. Real game files are opened read-only."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))
ROOT = Path(os.environ.get("IIDX_GAME_ROOT", HOME.parent))

from Crypto.Cipher import AES, Blowfish
from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.afp import AP2
from iidxviewer.legacy_codecs import decrypt_cbc_cts, gc_image, inflate, named_key, read_file, txp2_bytes
from iidxviewer.legacy_idx import LegacyAtlas, parse_index
from iidxviewer.resources import open_package
from iidxviewer.service import ResourceService


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LegacyCodecTests(unittest.TestCase):
    def test_afp_fixed_point_matrix_colour_and_fps_precision(self):
        parser = AP2(bytes(56), "matrix-fixture")
        flags = 0x100 | 0x200 | 0x400 | 0x800
        record = struct.pack("<IHH6i4h", flags, 1, 2, 1024, 2048, -512, 256, 400, -200, 255, 128, 0, 255)
        placement = parser.placement(record)
        self.assertEqual(placement["scale"], (1, 2))
        self.assertEqual(placement["skew"], (-.5, .25))
        self.assertEqual(placement["position"], (20, -10))
        self.assertEqual(placement["multiply"], (1, 128 / 255, 0, 1))
        short = parser.placement(struct.pack("<IHH4h", 0x40000 | 0x80000, 1, 2, 16384, -16384, 8192, -8192))
        self.assertEqual(short["scale"], (.5, -.5))
        self.assertEqual(short["skew"], (.25, -.25))
        for flags, stored, expected in [(2, struct.pack("<i", 30 * 1024), 30), (0, struct.pack("<f", 29.97), 29.97)]:
            header = bytearray(56)
            struct.pack_into("<I4h", header, 12, flags, 0, 32, 0, 32)
            header[24:28] = stored
            struct.pack_into("<I", header, 36, 56)
            struct.pack_into("<II", header, 48, 84, 0)
            container = struct.pack("<HHIIIII", 0, 0, 1, 0, 24, 24, 28) + bytes(4)
            self.assertAlmostEqual(AP2(bytes(header) + container, "fps-fixture").parse().fps, expected, places=4)

    def test_gc_crop_respects_tile_origin(self):
        atlas = LegacyAtlas([], Path("."))
        atlas.cache[0] = (Image.new("RGBA", (2, 2), (255, 20, 30, 255)), (10, 20))
        self.assertEqual(atlas.crop((10, 20, 2, 2)).getpixel((0, 0)), (255, 20, 30, 255))

    def test_lz_dialects_literals_overlap_and_zero_history(self):
        self.assertEqual(inflate(struct.pack("<I", 5) + b"\x1fhello"), b"hello")
        # Legacy ring position FEE repeats the immediately preceding literal.
        self.assertEqual(inflate(struct.pack("<I", 6) + b"\x01A\xee\xf2"), b"AAAAAA")
        self.assertEqual(inflate(struct.pack("<I", 3) + b"\x00\x00\x00"), bytes(3))
        # AVS references are backward distances, with a different byte order.
        self.assertEqual(inflate(b"\x01A\x00\x12", 6, avs=True), b"AAAAAA")
        self.assertEqual(inflate(b"\x00\x01\x20", 3, avs=True), bytes(3))

    def test_malformed_streams_and_indices_are_bounded(self):
        for data in [b"", b"\xff\xff\xff\x7f", struct.pack("<I", 20) + b"\xffA"]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                inflate(data)
        with self.assertRaises(ValueError):
            inflate(b"\x00\x00\x00", 8, avs=True)
        for data in [b"", struct.pack("<I", 0x7fffffff), bytes(512)]:
            with self.subTest(index=data[:8]), self.assertRaises(ValueError):
                parse_index(data)
        with tempfile.TemporaryDirectory(prefix="iidx-bad-txp2-") as folder:
            path = Path(folder) / "bad.bin"
            path.write_bytes(b"TXP2" + bytes(8) + struct.pack(">5I", 32, 32, 1, 0xffffffff, 32))
            with self.assertRaises(ValueError):
                open_package(path)

    def test_aes256_sidecar_cbc_uses_directory_name(self):
        # Independent CBC encoder checks the 17-style aligned branch.
        iv = bytes(range(16))
        plain = bytes(range(64))
        cipher = AES.new(named_key("in_01", 32), AES.MODE_CBC, iv).encrypt(plain)
        self.assertEqual(decrypt_cbc_cts(iv + cipher, "in_01", 32), plain)

    def test_missing_companions_are_reported_without_writes(self):
        with tempfile.TemporaryDirectory(prefix="iidx-missing-bin-") as folder:
            path = Path(folder) / "owl.bin"
            path.write_bytes(b"IIDX" + struct.pack("<III", 14, 1, 2) + b"system.idx".ljust(32, b"\0") + bytes(4))
            before = digest(path)
            package = open_package(path)
            try:
                self.assertTrue(any("缺少关联素材" in warning for warning in package.warnings))
                self.assertFalse(package.animations)
                self.assertEqual(json.loads(package.assets[0].read())["关联文件"], ["system.idx"])
            finally:
                package.close()
            self.assertEqual(digest(path), before)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["owl.bin"])


class RealLegacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_real_bin_and_idx_images_and_animation(self):
        cases = ["12_HAPPY_SKY/data/graph/sys/in_01/system.idx",
                 "14_GOLD/data/graph/sys/0200/owl.bin",
                 "16_EMPRESS/data/graph/sys/0200/osprey.bin",
                 "18_Resort_Anthem/data/graph_data/sys/0200.bin",
                 "19_Lincle_cn/contents/modules/data/graphic/0200.bin"]
        for relative in cases:
            path = ROOT / relative
            if not path.exists():
                continue
            with self.subTest(path=relative), tempfile.TemporaryDirectory(prefix="iidx-legacy-test-") as folder:
                originals = [p for p in path.parent.iterdir() if p.is_file() and p.suffix.lower() in {".bin", ".idx", ".gcz"}]
                # Avoid hashing unrelated, multi-GB directories of TXP2 packages.
                if path.suffix == ".bin" and not path.name in {"owl.bin", "osprey.bin"}:
                    originals = [path]
                before = {p: digest(p) for p in originals}
                service = ResourceService(folder)
                try:
                    info = service.open(path)
                    images = [(i, a) for i, a in enumerate(service.package.assets) if a.kind == "图片"]
                    self.assertTrue(images)
                    self.assertTrue(all(a.name != "tex/.png" for _, a in images))
                    preview = service.asset(images[0][0])
                    self.assertEqual(preview["kind"], "image")
                    self.assertTrue(Image.open(preview["path"]).getbbox())
                    if not relative.startswith("12_"):
                        self.assertGreater(len(info["animations"]), 0)
                        animation = service.package.animations[0]
                        self.assertEqual(animation.movie.fps, 30 if relative.startswith(("18_", "19_")) else 60)
                        a = service.render(0, 3)
                        b = service.render(0, min(8, animation.length - 1))
                        first, last = Image.open(a["path"]), Image.open(b["path"])
                        self.assertTrue(first.getbbox())
                        # The authored 304x416 overlay must occupy substantial
                        # pixels; a wrong matrix divisor leaves a tiny speck.
                        self.assertGreater(sum(first.getchannel("A").histogram()[128:]), 10000)
                        self.assertNotEqual(first.tobytes(), last.tobytes())
                        target = Path(folder) / "export.png"
                        service.dispatch({"method": "export", "kind": "frame", "animation": 0, "frame": 3, "target": str(target)})
                        self.assertTrue(Image.open(target).getbbox())
                        with self.assertRaises(ValueError):
                            service.dispatch({"method": "export", "kind": "frame", "animation": 0, "target": str(path.parent / "blocked.png")})
                finally:
                    service.close()
                self.assertEqual({p: digest(p) for p in originals}, before)

    def test_19_decryption_matches_18_bytes_and_image_pixels(self):
        found = False
        for name in ["i_resona", "i_inwall"]:
            plain = ROOT / f"18_Resort_Anthem/data/graph_data/sys/{name}.bin"
            encrypted = ROOT / f"19_Lincle_cn/contents/modules/data/graphic/{name}.bin"
            if not plain.exists() or not encrypted.exists():
                continue
            found = True
            with self.subTest(name=name):
                self.assertEqual(txp2_bytes(encrypted, read_file(encrypted)), read_file(plain))
                a, b = open_package(plain), open_package(encrypted)
                try:
                    first = next(asset for asset in a.assets if asset.kind == "图片").image()
                    second = next(asset for asset in b.assets if asset.kind == "图片").image()
                    self.assertEqual(first.size, second.size)
                    self.assertEqual(first.tobytes(), second.tobytes())
                finally:
                    a.close(); b.close()
        if not found:
            self.skipTest("缺少 18/19 代同源素材")

    def test_offscreen_tracks_do_not_prevent_loading_other_assets(self):
        for relative in ["12_HAPPY_SKY/data/graph/sys/result/system.idx", "16_EMPRESS/data/graph/sys/ending_a/system.idx",
                         "16_EMPRESS/data/graph/sys/1501/system.idx", "16_EMPRESS/data/graph/sys/1672/system.idx"]:
            path = ROOT / relative
            if not path.exists():
                continue
            with self.subTest(path=relative):
                package = open_package(path)
                try:
                    self.assertTrue(package.animations)
                    for animation in package.animations:
                        x0, x1, y0, y1 = animation.movie.rect
                        self.assertLessEqual(max(1, x1 - x0) * max(1, y1 - y0), 32 * 1024 * 1024)
                finally:
                    package.close()

    def test_encrypted_idr_and_gcr_sidecars(self):
        folder = ROOT / "12_HAPPY_SKY/data/graph/sys/in_01"
        if not (folder / "system.idx").exists():
            self.skipTest("缺少旧版 IDX/GCZ 验证数据")
        raw = (folder / "0.gcz").read_bytes()
        bf_iv = b"tukupypy"
        seed = b"0".ljust(24, b"\0")
        derived = Blowfish.new(b"omE872M2akmRkERh4177S", Blowfish.MODE_CBC, bf_iv).encrypt(seed)
        bf_key = (derived[:-1] + b"\0").split(b"\0")[0]
        wrapped = Blowfish.new(bf_key, Blowfish.MODE_CBC, bf_iv).encrypt(raw + bytes((-len(raw)) % 8))
        self.assertEqual(gc_image(raw, "0")[0].tobytes(), gc_image(wrapped, "0")[0].tobytes())
        with tempfile.TemporaryDirectory(prefix="iidx-idr-") as temporary:
            target = Path(temporary) / "scene17"
            target.mkdir()
            iv = bytes(range(16))
            def seal(plain):
                tail = len(plain) % 16
                cipher = AES.new(named_key(target.name, 32), AES.MODE_CBC, iv).encrypt(plain + bytes((-len(plain)) % 16))
                if tail:
                    cipher = cipher[:-32] + cipher[-16:] + cipher[-32:-16][:tail]
                return iv + cipher
            (target / "system.idr").write_bytes(seal((folder / "system.idx").read_bytes()))
            (target / "0.gcr").write_bytes(seal((folder / "0.gcz").read_bytes()))
            plain = open_package(folder / "system.idx")
            encrypted = open_package(target / "system.idr")
            try:
                a = next(a for a in plain.assets if a.kind == "图片").image()
                b = next(a for a in encrypted.assets if a.kind == "图片").image()
                self.assertEqual(a.size, b.size)
                self.assertEqual(a.tobytes(), b.tobytes())
            finally:
                plain.close(); encrypted.close()


if __name__ == "__main__":
    unittest.main()
