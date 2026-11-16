"""Meaningful integration checks against local files; tests never modify them."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))
ROOT = Path(os.environ.get("IIDX_GAME_ROOT", HOME.parent))

from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.afp import restore
from iidxviewer.exporting import check_destination
from iidxviewer.resources import audio_assets, catalog_kind, open_package, scan
from iidxviewer.service import ResourceService


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CatalogCategoryTests(unittest.TestCase):
    def test_file_categories_and_bin_discovery(self):
        with tempfile.TemporaryDirectory(prefix="iidx-categories-") as folder:
            root = Path(folder)
            names = {"screen.ifs": "动画/图像资源", "screen.BIN": "动画/图像资源", "cover.png": "动画/图像资源", "sound.sd9": "音频类文件", "keys.2dx": "音频类文件", "keys.s3p": "音频类文件", "voice.wav": "音频类文件", "voice.flac": "音频类文件", "movie.mp4": "BGA", "movie.wmv": "BGA", "movie.mpg": "BGA", "movie.mov": "BGA"}
            for name in names:
                (root / name).write_bytes(b"test")
            (root / "info.xml").write_text("<info/>")
            self.assertEqual({entry.path.name: entry.kind for entry in scan(root)}, names)

    def test_s3v_classification_uses_media_stream(self):
        with tempfile.TemporaryDirectory(prefix="iidx-s3v-") as folder:
            root = Path(folder)
            video = root / "bga.s3v"
            video.write_bytes(b"S3V0" + b"\xc0\xef\x19\xbc\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b")
            self.assertEqual(catalog_kind(video), "BGA")
            audio = root / "movie" / "voice.s3v"
            audio.parent.mkdir()
            audio.write_bytes(b"S3V0" + b"\x40\x9e\x69\xf8\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b")
            self.assertEqual(catalog_kind(audio), "音频类文件")

    def test_unsupported_bin_stays_read_only_and_inspectable(self):
        with tempfile.TemporaryDirectory(prefix="iidx-bin-") as folder:
            path = Path(folder) / "screen.bin"
            path.write_bytes(b"legacy binary")
            before = digest(path)
            package = open_package(path)
            try:
                self.assertEqual(package.assets[0].kind, "文件")
                self.assertEqual(package.assets[0].read(), b"legacy binary")
                self.assertTrue(package.warnings)
            finally:
                package.close()
            self.assertEqual(digest(path), before)


class RealResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.effect = ROOT / "20_tricoro/data/graphic/test_effect_anime.ifs"
        if not cls.effect.exists():
            raise unittest.SkipTest("本地没有 20 代验证数据，请设置 IIDX_GAME_ROOT")
        cls.initial_hash = digest(cls.effect)

    @classmethod
    def tearDownClass(cls):
        assert digest(cls.effect) == cls.initial_hash, "源文件发生变化"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="iidx-test-")
        self.service = ResourceService(self.temp.name)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_texture_preview_skips_png_encoding_with_identical_pixels(self):
        from ifstools.handlers.image_file import ImageFile
        from ifstools.handlers.tex_folder import ImageCanvas
        package = open_package(self.effect)
        try:
            for source_type in (ImageFile, ImageCanvas):
                asset = next(a for a in package.assets if isinstance(a.source, source_type))
                reference = Image.open(BytesIO(asset.read())).convert("RGBA")
                with patch("ifstools.handlers.image_file.encode_png", side_effect=AssertionError("Unnecessary preview PNG encoding")):
                    actual = asset.image()
                self.assertEqual(actual.size, reference.size)
                self.assertEqual(actual.tobytes(), reference.tobytes())
        finally:
            package.close()

    def test_actual_animation_changes_pixels_and_preserves_alpha(self):
        info = self.service.dispatch({"method": "open", "path": str(self.effect)})
        self.assertGreater(len(info["animations"]), 0)
        first = self.service.render(0, 3)
        last = self.service.render(0, 7)
        a, b = Image.open(first["path"]), Image.open(last["path"])
        self.assertNotEqual(a.tobytes(), b.tobytes())
        self.assertIsNotNone(a.getbbox())
        self.assertEqual(a.getpixel((0, 0))[3], 0)

    def test_images_from_five_versions(self):
        for version in ["20_tricoro", "22_PENDUAL", "26_ROOTAGE", "31_EPOLIS", "33_Sparkle_Shower"]:
            with self.subTest(version=version):
                candidates = list((ROOT / version).rglob("notes00.ifs"))
                if not candidates:
                    self.skipTest(f"缺少 {version} 的 notes00.ifs")
                path = candidates[0]
                before = digest(path)
                package = open_package(path)
                try:
                    images = [a for a in package.assets if a.kind == "图片"]
                    self.assertTrue(images)
                    for asset in images[:3]:
                        im = asset.image()
                        self.assertGreater(im.width * im.height, 0)
                finally:
                    package.close()
                self.assertEqual(digest(path), before)

    def test_latest_timeline_renders(self):
        path = ROOT / "33_Sparkle_Shower/data/graphic/0/mselect.ifs"
        if not path.exists():
            self.skipTest("缺少 33 代 mselect 数据")
        before = digest(path)
        info = self.service.open(path)
        self.assertGreater(len(info["animations"]), 100)
        reply = self.service.render(0, 15)
        self.assertIsNotNone(Image.open(reply["path"]).getbbox())
        self.assertEqual(digest(path), before)

    def test_webp_and_png_sequence_export(self):
        self.service.open(self.effect)
        webp = Path(self.temp.name) / "effect.webp"
        self.service.dispatch({"method": "export", "kind": "webp", "animation": 0, "target": str(webp)})
        with Image.open(webp) as im:
            self.assertGreater(im.n_frames, 1)
        sequence = Path(self.temp.name) / "frames"
        self.service.dispatch({"method": "export", "kind": "sequence", "animation": 0, "target": str(sequence)})
        self.assertEqual(len(list(sequence.glob("*.png"))), self.service.package.animations[0].length)
        with self.assertRaises(FileExistsError):
            self.service.dispatch({"method": "export", "kind": "webp", "animation": 0, "target": str(webp)})

    def test_game_directory_and_existing_file_are_protected(self):
        with self.assertRaises(ValueError):
            check_destination(self.effect, ROOT)
        self.service.open(self.effect)
        with self.assertRaises(ValueError):
            self.service.dispatch({"method": "export", "kind": "frame", "animation": 0, "target": str(self.effect.parent / "should_not_exist.png")})

    def test_sd9_2dx_s3p_decode_to_native_audio(self):
        paths = [next((ROOT / "20_tricoro").rglob("*.sd9")), next((ROOT / "20_tricoro").rglob("*.2dx")), next((ROOT / "33_Sparkle_Shower").rglob("*.s3p"))]
        for path in paths:
            with self.subTest(path=path.name):
                before = digest(path)
                info = self.service.open(path)
                self.assertTrue(info["assets"])
                reply = self.service.asset(0)
                self.assertEqual(reply["kind"], "audio")
                data = Path(reply["path"]).read_bytes()
                self.assertEqual(data[:4], b"RIFF")
                self.assertEqual(data[8:12], b"WAVE")
                self.assertEqual(digest(path), before)

    def test_wmv_becomes_native_mp4(self):
        paths = list((ROOT / "20_tricoro/data/movie").glob("*.wmv"))
        path = min(paths, key=lambda p: p.stat().st_size)
        before = digest(path)
        self.service.open(path)
        reply = self.service.asset(0)
        self.assertEqual(reply["kind"], "video")
        self.assertEqual(Path(reply["path"]).read_bytes()[4:8], b"ftyp")
        self.assertEqual(digest(path), before)

    def test_truncated_inputs_fail_without_huge_allocation(self):
        with self.assertRaises(ValueError):
            restore(b"invalid")
        with self.assertRaises(ValueError):
            audio_assets(b"S3P0\xff\xff\xff\xff", "bad.s3p")

    def test_worker_protocol(self):
        commands = [{"id": 1, "method": "open", "path": str(self.effect)}, {"id": 2, "method": "render", "animation": 0, "frame": 3}, {"id": 3, "method": "missing"}]
        process = subprocess.run([sys.executable, str(HOME / "backend.py"), "--cache", self.temp.name], input="".join(json.dumps(c) + "\n" for c in commands), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr)
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertTrue(replies[0]["ok"])
        self.assertTrue(replies[1]["ok"])
        self.assertFalse(replies[2]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
