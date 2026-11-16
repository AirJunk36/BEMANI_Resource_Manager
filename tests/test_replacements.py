"""Round-trip replacement tests; original game files must retain their hashes."""
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave
import zlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))
ROOT = Path(os.environ.get("IIDX_GAME_ROOT", HOME.parent))

from Crypto.Cipher import AES
from ifstools.ifs import IFS
from ifstools.handlers.generic_file import GenericFile
from kbinxml import KBinXML
from lxml import etree
from PIL import Image
from PySide6.QtGui import QGuiApplication
from iidxviewer.legacy_codecs import inflate, named_key
from iidxviewer.packing import compress_legacy, encrypt_cbc_cts
from iidxviewer.replacement_media import MediaConverter, wrap_s3v
from iidxviewer.resources import audio_assets, open_package
from iidxviewer.service import ResourceService


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tone(path, frequency=660, channels=1, rate=22050):
    with wave.open(str(path), "wb") as output:
        output.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<h", int(math.sin(i * 2 * math.pi * frequency / rate) * 10000)) * channels for i in range(rate // 2)))
    return path.read_bytes()


def two_tracks(wav):
    header = bytearray(0x50)
    header[:12] = b"BEMANI TEST\0"
    struct.pack_into("<II", header, 0x10, 0x50, 2)
    for index in range(2):
        struct.pack_into("<I", header, 0x48 + index * 4, len(header))
        record = b"2DX9" + struct.pack("<IIHHHHI", 24, len(wav), 0x3231, index, 64 + index, 1, 0)
        header.extend(record + wav)
    return bytes(header)


def make_ifs(folder, destination):
    archive = IFS(str(folder))
    try:
        archive.repack(progress=False, path=str(destination))
    finally:
        archive.close()


class ReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="iidx-edit-test-")
        self.folder = Path(self.temporary.name)
        self.sources = self.folder / "source"
        self.sources.mkdir()
        cache = self.folder / "cache"
        cache.mkdir()
        self.service = ResourceService(cache)
        self.new_audio = self.folder / "tone.wav"
        self.wav = tone(self.new_audio)
        self.new_image = self.folder / "new.png"
        Image.new("RGBA", (40, 50), (220, 45, 70, 255)).save(self.new_image)

    def tearDown(self):
        self.service.close()
        self.temporary.cleanup()

    def game(self, relative):
        path = ROOT / relative
        if not path.exists():
            self.skipTest("缺少验证素材：" + relative)
        return path

    def replace(self, source, index=0, whole=False):
        return self.service.dispatch({"method": "replace", "scope": "file" if whole else "asset", "asset": index, "source": str(source)})

    def save(self, name="saved"):
        return self.service.dispatch({"method": "save_replacement", "target": str(self.folder / name)})

    def test_native_compression_and_aes_cts_round_trips(self):
        for data in [b"hello", bytes(5000), b"ABC" * 5000, bytes(range(256)) * 100]:
            self.assertEqual(inflate(compress_legacy(data)), data)
        for length in [16, 17, 31, 32, 47, 4096]:
            from iidxviewer.legacy_codecs import decrypt_cbc_cts
            plain = bytes(i % 256 for i in range(length))
            self.assertEqual(decrypt_cbc_cts(encrypt_cbc_cts(plain, "0200.bin"), "0200.bin"), plain)

    def test_ifs_image_save_reopen_undo_and_draft_switch(self):
        source = self.game("20_tricoro/data/graphic/notes00.ifs")
        before = digest(source)
        self.service.open(source)
        images = [(i, a) for i, a in enumerate(self.service.package.assets) if a.kind == "图片"]
        index, asset = images[0]
        name, size, original = asset.name, asset.image().size, asset.read()
        untouched = {a.name: a.read() for _, a in images[1:]}
        result = self.replace(self.new_image, index)
        self.assertTrue(result["modified"])
        edited = next(a for a in self.service.package.assets if a.name == name)
        self.assertEqual(edited.image().size, size)
        self.assertEqual(edited.image().getpixel((10, 5)), (220, 45, 70, 255))
        for name2, payload in untouched.items():
            self.assertEqual(next(a for a in self.service.package.assets if a.name == name2).read(), payload)
        output = self.save()
        package = open_package(Path(output["primary"]))
        try:
            self.assertEqual(next(a for a in package.assets if a.name == name).image().getpixel((10, 5)), (220, 45, 70, 255))
        finally:
            package.close()
        # Independent header/data checksum checks, not just our package loader.
        data = Path(output["primary"]).read_bytes()
        end = struct.unpack_from(">I", data, 16)[0]
        self.assertEqual(data[20:36], hashlib.md5(data[36:end]).digest())
        manifest = KBinXML(data[36:end])
        self.assertEqual(manifest.xml_doc.find("_info_/md5").text, hashlib.md5(data[end:]).hexdigest())
        other = self.sources / "other.bin"
        other.write_bytes(b"unrelated resource")
        self.service.open(other)
        result = self.service.open(source)
        self.assertTrue(result["modified"])
        result = self.service.dispatch({"method": "undo_replace"})
        self.assertFalse(result["modified"])
        self.assertEqual(next(a for a in self.service.package.assets if a.name == name).read(), original)
        self.assertEqual(digest(source), before)

    def test_ifs_other_binary_xml_and_embedded_audio_track(self):
        payload = self.sources / "payload"
        payload.mkdir()
        (payload / "other.bin").write_bytes(b"ORIGINAL\x00BINARY")
        (payload / "config.xml").write_text('<root><value __type="u32">7</value></root>')
        (payload / "sound.2dx").write_bytes(two_tracks(self.wav))
        source = self.sources / "mixed.ifs"
        make_ifs(payload, source)
        before = digest(source)
        self.service.open(source)
        replacement = self.folder / "raw.dat"
        replacement.write_bytes(b"NEW\0BYTES\xff")
        index = next(i for i, a in enumerate(self.service.package.assets) if a.name == "other.bin")
        self.replace(replacement, index)
        self.assertEqual(self.service.package.files["other.bin"].read(), replacement.read_bytes())
        xml = self.folder / "config.xml"
        xml.write_text('<root><value __type="u32">99</value></root>')
        index = next(i for i, a in enumerate(self.service.package.assets) if a.name == "config.xml")
        self.replace(xml, index)
        self.assertIn(b"99", self.service.package.files["config.xml"].read())
        parent = next(i for i, a in enumerate(self.service.package.assets) if a.name == "sound.2dx")
        old_tracks = audio_assets(self.service.package.assets[parent].read(), "sound")
        response = self.service.asset(parent)
        self.assertEqual(self.service.asset(parent)["assets"], response["assets"])
        new_tone = self.folder / "different.wav"
        tone(new_tone, 1100)
        self.replace(new_tone, response["assets"][0]["id"])
        parent_asset = next(a for a in self.service.package.assets if a.name == "sound.2dx")
        tracks = audio_assets(parent_asset.read(), "sound")
        self.assertNotEqual(tracks[0].read(), old_tracks[0].read())
        self.assertEqual(tracks[1].read(), old_tracks[1].read())
        self.assertEqual(self.service.info()["selected_asset"], response["assets"][0]["name"])
        self.assertEqual(self.service.info()["change_count"], 3)
        self.assertEqual(digest(source), before)

    def test_real_sd9_2dx_s3p_preserve_other_tracks_and_metadata(self):
        cases = ["20_tricoro/data/sound/system/syssd_category_voice_lv1.sd9",
                 "20_tricoro/data/sound/20000/20000_pre.2dx",
                 "33_Sparkle_Shower/data/sound/voice/vo034_miku/vo034_06_tutorial.s3p"]
        for relative in cases:
            source = self.game(relative)
            with self.subTest(path=relative):
                before, raw = digest(source), source.read_bytes()
                originals = audio_assets(raw, source.name)
                self.service.open(source)
                self.replace(self.new_audio)
                updated = self.service.package.path.read_bytes()
                tracks = audio_assets(updated, source.name)
                self.assertEqual(len(tracks), len(originals))
                self.assertNotEqual(tracks[0].read(), originals[0].read())
                self.assertEqual([a.read() for a in tracks[1:]], [a.read() for a in originals[1:]])
                if source.suffix == ".sd9":
                    self.assertEqual(raw[12:32], updated[12:32])
                    self.assertEqual(struct.unpack_from("<I", updated, 8)[0], len(tracks[0].read()))
                elif source.suffix == ".2dx":
                    old = struct.unpack_from("<I", raw, 0x48)[0]
                    new = struct.unpack_from("<I", updated, 0x48)[0]
                    self.assertEqual(raw[old + 12:old + 24], updated[new + 12:new + 24])
                preview = self.service.asset(0)
                with wave.open(preview["path"], "rb") as decoded:
                    self.assertEqual(decoded.getframerate(), 44100)
                    self.assertEqual(decoded.getnchannels(), 2)
                    self.assertLess(decoded.getnframes() / 44100, 1)
                self.assertEqual(digest(source), before)

    def test_txp2_plain_and_encrypted_image_replacement(self):
        for relative in ["18_Resort_Anthem/data/graph_data/sys/i_resona.bin", "19_Lincle_cn/contents/modules/data/graphic/i_resona.bin"]:
            source = self.game(relative)
            with self.subTest(path=relative):
                before = digest(source)
                self.service.open(source)
                index = next(i for i, a in enumerate(self.service.package.assets) if a.kind == "图片")
                result = self.replace(self.new_image, index)
                edited = next(a for a in self.service.package.assets if a.name == result["selected_asset"])
                self.assertEqual(edited.image().getpixel((20, 20)), (220, 45, 70, 255))
                output = self.save(source.parts[-5] + "-saved")
                reopened = open_package(Path(output["primary"]))
                try:
                    self.assertEqual(next(a for a in reopened.assets if a.kind == "图片").image().getpixel((20, 20)), (220, 45, 70, 255))
                finally:
                    reopened.close()
                if relative.startswith("19_"):
                    self.assertNotEqual(Path(output["primary"]).read_bytes()[:4], b"TXP2")
                self.assertEqual(digest(source), before)

    def test_legacy_multifile_images_crc_and_output_layout(self):
        for version, name in [("14_GOLD", "owl.bin"), ("16_EMPRESS", "osprey.bin")]:
            source = self.game(f"{version}/data/graph/sys/0200/{name}")
            with self.subTest(version=version):
                before = {p: digest(p) for p in source.parent.iterdir() if p.is_file()}
                self.service.open(source)
                images = [(i, a) for i, a in enumerate(self.service.package.assets) if a.kind == "图片"]
                untouched = {a.name: a.image().tobytes() for _, a in images[1:]}
                result = self.replace(self.new_image, images[0][0])
                image = next(a for a in self.service.package.assets if a.name == result["selected_asset"]).image()
                self.assertTrue(all(abs(a - b) <= 8 for a, b in zip(image.getpixel((10, 10)), (220,45,70,255))))
                for name2, pixels in untouched.items():
                    self.assertEqual(next(a for a in self.service.package.assets if a.name == name2).image().tobytes(), pixels)
                output = self.save(version + "-saved")
                self.assertEqual(Path(output["primary"]).parent.name, "0200")
                self.assertTrue((Path(output["primary"]).parent / "system.idx").exists())
                if version.startswith("16"):
                    data = self.service.package.path.read_bytes()
                    gc = self.service.package.path.parent / "0.gcz"
                    self.assertEqual(struct.unpack_from("<I", data, 16 + 36)[0], zlib.crc32(gc.read_bytes()))
                    self.assertEqual(struct.unpack_from("<I", data, 16 + 48)[0], gc.stat().st_size)
                self.assertEqual({p: digest(p) for p in before}, before)

    def test_failed_replacement_and_save_destinations_leave_sources_intact(self):
        source = self.game("20_tricoro/data/graphic/notes00.ifs")
        before = digest(source)
        self.service.open(source)
        index = next(i for i, a in enumerate(self.service.package.assets) if a.kind == "图片")
        broken = self.folder / "bad.png"
        broken.write_bytes(b"not an image")
        with self.assertRaises(Exception):
            self.replace(broken, index)
        self.assertFalse(self.service.info()["modified"])
        self.replace(self.new_image, index)
        with self.assertRaises(ValueError):
            self.service.dispatch({"method":"save_replacement", "target":str(source.parent / "blocked-edit-output")})
        output = self.save()
        first = digest(output["primary"])
        with self.assertRaises(FileExistsError):
            self.save()
        self.assertEqual(digest(output["primary"]), first)
        self.assertEqual(digest(source), before)

    def test_standalone_bga_conversion_and_original_name(self):
        source = self.game("20_tricoro/data/movie/20red_decide.wmv")
        before = digest(source)
        converter = MediaConverter(self.folder)
        clip = self.folder / "red.mp4"
        converter.run(["-loglevel","error","-f","lavfi","-i","color=c=red:s=96x64:r=24","-t","1","-c:v","libx264","-pix_fmt","yuv420p","-an","-n",str(clip)])
        old = converter.probe(source)
        self.service.open(source)
        self.replace(clip)
        new = converter.probe(self.service.package.path)
        self.assertEqual((new.width,new.height),(old.width,old.height))
        self.assertAlmostEqual(new.fps, old.fps, places=2)
        output = self.save()
        self.assertEqual(Path(output["primary"]).name, source.name)
        self.assertEqual(self.service.asset(0)["kind"], "video")
        self.assertEqual(digest(source), before)

    def test_whole_file_replacement_and_report_name_collision(self):
        source = self.sources / "replacements.json"
        source.write_bytes(b'{"original":true}')
        new = self.folder / "new.json"
        new.write_bytes(b'{"replacement":true}')
        before = digest(source)
        self.service.open(source)
        self.replace(new, whole=True)
        output = self.save()
        self.assertEqual(Path(output["primary"]).read_bytes(), new.read_bytes())
        self.assertTrue((Path(output["path"]) / "replacements_2.json").exists())
        self.assertEqual(digest(source), before)

    def test_ifs_super_references_are_localized_and_whole_package_replacement(self):
        files = self.sources / "shared"
        files.mkdir()
        (files / "message.txt").write_bytes(b"original shared entry")
        shared = self.sources / "shared.ifs"
        make_ifs(files, shared)
        archive = IFS(str(shared))
        try:
            expected_md5 = archive.manifest_md5.hex()
        finally:
            archive.close()
        tree = etree.Element("imgfs")
        reference = etree.SubElement(tree, "_super_")
        reference.text = "shared.ifs"
        etree.SubElement(reference, "md5", __type="bin", __size="16").text = expected_md5
        entry = etree.SubElement(tree, "message_Etxt", __type="3s32")
        entry.text = "0 0 0"
        etree.SubElement(entry, "i", __type="u8").text = "1"
        manifest = KBinXML(tree)
        encoded = manifest.to_binary()
        source = self.sources / "linked.ifs"
        source.write_bytes(struct.pack(">IHHIII", 0x6CAD8F89,3,0xfffc,0,manifest.mem_size,36+len(encoded)) + hashlib.md5(encoded).digest() + encoded)
        before = {p:digest(p) for p in [source,shared]}
        self.service.open(source)
        replacement = self.folder / "message.txt"
        replacement.write_bytes(b"new localized entry")
        self.replace(replacement)
        output = self.save()
        self.assertFalse((Path(output["path"]) / "shared.ifs").exists())
        package = open_package(Path(output["primary"]))
        try:
            self.assertEqual(package.assets[0].read(), b"new localized entry")
            self.assertIsNone(package.archive.manifest.xml_doc.find("_super_"))
        finally:
            package.close()
        self.service.open(source)
        self.replace(shared, whole=True)
        self.assertEqual(self.service.package.assets[0].read(), b"original shared entry")
        self.assertEqual({p:digest(p) for p in before}, before)

    def test_encrypted_legacy_replacement_keeps_folder_key(self):
        original = self.game("12_HAPPY_SKY/data/graph/sys/in_01/system.idx")
        folder = self.sources / "scene17"
        folder.mkdir()
        (folder / "system.idr").write_bytes(encrypt_cbc_cts(original.read_bytes(), folder.name, 32))
        (folder / "0.gcr").write_bytes(encrypt_cbc_cts((original.parent / "0.gcz").read_bytes(), folder.name, 32))
        source = folder / "system.idr"
        before = {p:digest(p) for p in folder.iterdir()}
        self.service.open(source)
        index = next(i for i,a in enumerate(self.service.package.assets) if a.kind == "图片")
        result = self.replace(self.new_image, index)
        output = self.save()
        self.assertEqual(Path(output["primary"]).parent.name, folder.name)
        reopened = open_package(Path(output["primary"]))
        try:
            pixel = next(a for a in reopened.assets if a.name == result["selected_asset"]).image().getpixel((10,10))
            self.assertTrue(all(abs(a-b) <= 8 for a,b in zip(pixel,(220,45,70,255))))
        finally:
            reopened.close()
        self.assertEqual({p:digest(p) for p in before}, before)

    def test_s3v_bga_wrapper_survives_conversion_and_save(self):
        original = self.game("20_tricoro/data/movie/20red_decide.wmv")
        source = self.sources / "movie.s3v"
        header = b"S3V0" + struct.pack("<III",32,0,0) + bytes(16)
        source.write_bytes(wrap_s3v(header, original.read_bytes()))
        before = digest(source)
        converter = MediaConverter(self.folder)
        clip = self.folder / "blue.mp4"
        converter.run(["-loglevel","error","-f","lavfi","-i","color=c=blue:s=96x64:r=24","-t","1","-c:v","libx264","-pix_fmt","yuv420p","-an","-n",str(clip)])
        self.service.open(source)
        self.replace(clip)
        output = self.save()
        data = Path(output["primary"]).read_bytes()
        self.assertEqual(data[:4], b"S3V0")
        self.assertEqual(struct.unpack_from("<I", data,8)[0], len(data)-32)
        self.assertEqual(self.service.asset(0)["kind"], "video")
        self.assertEqual(digest(source), before)

    def test_replacement_invalidates_animation_caches_and_undo_restores_pixels(self):
        source = self.game("20_tricoro/data/graphic/test_effect_anime.ifs")
        self.service.open(source)
        before = Image.open(self.service.render(0,3)["path"]).tobytes()
        atlas = next(i for i,a in enumerate(self.service.package.assets) if a.kind == "图集")
        self.replace(self.new_image, atlas)
        after = Image.open(self.service.render(0,3)["path"]).tobytes()
        self.assertNotEqual(before, after)
        self.service.dispatch({"method":"undo_replace"})
        self.assertEqual(Image.open(self.service.render(0,3)["path"]).tobytes(), before)


if __name__ == "__main__":
    unittest.main()
