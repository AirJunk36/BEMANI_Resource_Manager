"""Convert replacement media to the original container in a temporary folder."""
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid

from PIL import Image, ImageOps

from .resources import audio_assets, media_suffix, unwrap_media


@dataclass
class MediaInfo:
    audio_codec: str = ""
    video_codec: str = ""
    rate: int = 44100
    channels: int = 2
    width: int = 0
    height: int = 0
    fps: float = 30


class MediaConverter:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.messages = []
        import imageio_ffmpeg
        self.ffmpeg = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

    def temporary(self, suffix):
        return self.folder / ("convert-" + uuid.uuid4().hex + suffix)

    def run(self, args):
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", *args], capture_output=True,
                                timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
        if result.returncode:
            raise ValueError("替换素材转换失败：" + result.stderr.decode("utf8", "replace")[-1800:])

    def probe(self, path):
        result = subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-i", str(path)], capture_output=True,
                                timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
        text = result.stderr.decode("utf8", "replace")
        audio = re.search(r"Audio:\s*([\w]+)[^\n]*?(\d+) Hz,\s*([^,\n]+)", text)
        video = re.search(r"Video:\s*([\w]+)[^\n]*?\b(\d{1,5})x(\d{1,5})\b[^\n]*", text)
        info = MediaInfo()
        if audio:
            info.audio_codec, info.rate = audio[1], int(audio[2])
            layout = audio[3].strip()
            info.channels = {"mono": 1, "stereo": 2, "5.1": 6, "5.1(side)": 6, "7.1": 8}.get(layout, 2)
        if video:
            info.video_codec, info.width, info.height = video[1], int(video[2]), int(video[3])
            fps = re.search(r"([\d.]+) fps", video[0])
            if fps:
                info.fps = float(fps[1])
        if not info.audio_codec and not info.video_codec:
            raise ValueError("无法识别替换媒体：" + text[-700:])
        return info

    def source(self, value):
        if isinstance(value, bytes):
            blob = unwrap_media(value)
            path = self.temporary(media_suffix(blob))
            path.write_bytes(blob)
            return path
        path = Path(value)
        with path.open("rb") as handle:
            header = handle.read(32)
        if header[:4] == b"S3V0":
            if len(header) < 16:
                raise ValueError("S3V 头被截断")
            offset = struct.unpack_from("<I", header, 4)[0]
            if not 16 <= offset < path.stat().st_size:
                raise ValueError("S3V 头无效")
            output = self.temporary(".wmv")
            with path.open("rb") as source, output.open("xb") as target:
                source.seek(offset)
                shutil.copyfileobj(source, target)
            return output
        if header[:4] in {b"SD9\0", b"S3P0"} or path.suffix.lower() == ".2dx":
            tracks = audio_assets(path.read_bytes(), path.name)
            if len(tracks) != 1:
                raise ValueError("请选择 WAV、MP3 等单条音频；整包替换需同格式。")
            return self.source(tracks[0].read())
        return path

    def audio(self, original, source, suffix=None):
        original_path, source_path = self.source(original), self.source(source)
        old, new = self.probe(original_path), self.probe(source_path)
        if not new.audio_codec:
            raise ValueError("所选文件不含音频")
        extension = (suffix or original_path.suffix).lower()
        if extension in {".s3v", ".wmv", ".wma", ".bin"}:
            extension = ".wma"
        output = self.temporary(extension)
        if old.audio_codec == new.audio_codec and old.rate == new.rate and old.channels == new.channels and source_path.suffix.lower() == extension:
            shutil.copyfile(source_path, output)
            return output
        codecs = {".wav": old.audio_codec if old.audio_codec in {"adpcm_ms", "adpcm_ima_wav", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_u8"} else "pcm_s16le",
                  ".mp3": "libmp3lame", ".ogg": "libvorbis", ".flac": "flac", ".wma": "wmav2",
                  ".m4a": "aac", ".aac": "aac", ".opus": "libopus", ".aif": "pcm_s16be", ".aiff": "pcm_s16be"}
        if extension not in codecs:
            raise ValueError(f"暂不能写回音频格式 {extension}")
        rate = 48000 if extension == ".opus" else old.rate
        args = ["-loglevel", "error", "-i", str(source_path), "-map", "0:a:0", "-vn", "-c:a", codecs[extension], "-ar", str(rate), "-ac", str(old.channels)]
        if extension == ".wma":
            args += ["-b:a", "128k", "-f", "asf"]
        self.run(args + ["-n", str(output)])
        self.messages.append(f"音频：{rate} Hz · {old.channels} 声道 · {codecs[extension]}")
        return output

    def video(self, original, source, suffix):
        original_path, source_path = self.source(original), self.source(source)
        old, new = self.probe(original_path), self.probe(source_path)
        if not new.video_codec:
            raise ValueError("所选文件不含视频画面")
        extension = suffix.lower()
        asf = extension in {".wmv", ".s3v"}
        output = self.temporary(".wmv" if asf else extension)
        if (old.video_codec == new.video_codec and old.width == new.width and old.height == new.height
                and abs(old.fps - new.fps) < .01 and bool(old.audio_codec) == bool(new.audio_codec)
                and (not old.audio_codec or (old.audio_codec == new.audio_codec and old.rate == new.rate and old.channels == new.channels))
                and source_path.suffix.lower() == output.suffix.lower()):
            shutil.copyfile(source_path, output)
            return output
        width, height = (old.width + 1) // 2 * 2, (old.height + 1) // 2 * 2
        video_codec = "wmv2" if asf else "mpeg2video" if extension in {".mpg", ".mpeg"} else "mpeg4" if extension == ".avi" else "libvpx-vp9" if extension == ".webm" else "libx264"
        args = ["-loglevel", "error", "-i", str(source_path), "-map", "0:v:0", "-vf", f"scale={width}:{height}", "-r", f"{old.fps:.8g}", "-c:v", video_codec, "-pix_fmt", "yuv420p"]
        if video_codec == "libx264":
            args += ["-preset", "veryfast", "-crf", "18"]
        elif video_codec == "libvpx-vp9":
            args += ["-b:v", "0", "-crf", "24"]
        else:
            args += ["-b:v", "6000k"]
        if old.audio_codec and new.audio_codec:
            codec = "wmav2" if asf else "mp2" if extension in {".mpg", ".mpeg"} else "libmp3lame" if extension == ".avi" else "libopus" if extension == ".webm" else "aac"
            args += ["-map", "0:a:0", "-c:a", codec, "-ar", str(old.rate), "-ac", str(old.channels)]
        else:
            args += ["-an"]
        if asf:
            args += ["-f", "asf"]
        elif extension in {".mp4", ".m4v", ".mov"}:
            args += ["-movflags", "+faststart"]
        self.run(args + ["-n", str(output)])
        self.messages.append(f"BGA：{width} × {height} · {old.fps:g} FPS · {video_codec}")
        return output

    def image(self, source, size):
        with Image.open(source) as image:
            if image.width * image.height > 32 * 1024 * 1024:
                raise ValueError("替换图片超过像素上限")
            image = ImageOps.exif_transpose(image).convert("RGBA")
        if image.size != tuple(size):
            image = image.resize(tuple(size), Image.Resampling.LANCZOS)
            self.messages.append(f"图片：{size[0]} × {size[1]}")
        return image


def wrap_s3v(original, payload):
    if original[:4] != b"S3V0":
        return payload
    offset = struct.unpack_from("<I", original, 4)[0]
    if not 16 <= offset <= len(original):
        raise ValueError("S3V 封装头无效")
    head = bytearray(original[:offset])
    # The format permits zero for an uncomputed data hash (create_s3p.py).
    struct.pack_into("<II", head, 8, len(payload), 0)
    return bytes(head) + payload


def replace_audio_track(data, index, source, converter):
    from .audio_crypto import decode_2dx, encode_2dx
    if data.startswith(b'%'):
        return encode_2dx(replace_audio_track(decode_2dx(data), index, source, converter), data)
    tracks = audio_assets(data, "audio")
    if not 0 <= index < len(tracks):
        raise ValueError("音频轨道索引无效")
    if data[:4] == b"SD9\0":
        start = struct.unpack_from("<I", data, 4)[0]
        wav = converter.audio(tracks[index].read(), source, ".wav").read_bytes()
        header = bytearray(data[:start])
        struct.pack_into("<I", header, 8, len(wav))
        old_end = start + len(tracks[index].read())
        return bytes(header) + wav + data[old_end:]
    if data[:4] == b"S3P0":
        count = struct.unpack_from("<I", data, 4)[0]
        entries = []
        for i in range(count):
            off, size = struct.unpack_from("<II", data, 8 + i * 8)
            entry = data[off:off + size]
            if i == index:
                payload = converter.audio(unwrap_media(entry), source, ".wma").read_bytes()
                entry = wrap_s3v(entry, payload)
            entries.append(entry)
        header = bytearray(data[:8 + count * 8])
        payload = bytearray()
        for i, entry in enumerate(entries):
            struct.pack_into("<II", header, 8 + i * 8, len(header) + len(payload), len(entry))
            payload.extend(entry)
        return bytes(header) + bytes(payload)
    # audio_assets() has validated every 2DX record before any rebuilding.
    header_size, count = struct.unpack_from("<II", data, 16)
    if header_size > len(data):
        raise ValueError("2DX 头长度超出文件")
    output = bytearray(data[:header_size])
    for i in range(count):
        old_offset = struct.unpack_from("<I", data, 0x48 + i * 4)[0]
        head_size, size = struct.unpack_from("<II", data, old_offset + 4)
        header = bytearray(data[old_offset:old_offset + head_size])
        payload = data[old_offset + head_size:old_offset + head_size + size]
        if i == index:
            payload = converter.audio(payload, source, ".wav").read_bytes()
            struct.pack_into("<I", header, 8, len(payload))
        output.extend(bytes((-len(output)) % 4))
        struct.pack_into("<I", output, 0x48 + i * 4, len(output))
        output.extend(header); output.extend(payload)
    return bytes(output)
