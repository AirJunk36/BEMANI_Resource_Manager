"""Render IIDX .1 event timelines with their matching 2DX/S3P keysounds."""
from collections import defaultdict
from io import BytesIO
from pathlib import Path, PurePosixPath
import os
import re
import struct
import subprocess
import tempfile
import wave

import numpy as np


RATE = 44100
MAX_SECONDS = 1800
CHART_NAMES = {0: 'SP HYPER', 1: 'SP NORMAL', 2: 'SP ANOTHER', 3: 'SP BEGINNER',
               4: 'SP LEGGENDARIA', 5: 'DP BEGINNER', 6: 'DP HYPER', 7: 'DP NORMAL', 8: 'DP ANOTHER', 10: 'DP LEGGENDARIA'}
BANK_EXTENSIONS={'.2dx','.s3p'}


def decode_chart(data, name):
    """19 CN charts use the same filename AES envelope as their sound banks."""
    try:
        charts(data)
    except ValueError:
        if 112 <= len(data) <= 32 * 1024 * 1024:
            from .legacy_codecs import decrypt_cbc_cts
            try:
                plain = decrypt_cbc_cts(data, PurePosixPath(name).name)
                charts(plain)
                return plain
            except (ValueError, UnicodeEncodeError):
                pass
    return data


def banks(package):
    if package.path.suffix.lower() in BANK_EXTENSIONS:
        return [dict(id='package', name=package.path.name)]
    return [dict(id=str(i), name=a.name) for i, a in enumerate(package.assets)
            if a.kind == '音频包' and PurePosixPath(a.name).suffix.lower() in BANK_EXTENSIONS]


def chart_bytes(package, original, bank, external=None):
    if external:
        path = Path(external)
        if path.suffix.lower() != '.1' or path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('请选择配套的 .1 谱面')
        return decode_chart(path.read_bytes(), path.name)
    name = PurePosixPath(bank['name'])
    internal = [a for a in package.assets if a.name.lower().endswith('.1')
                and PurePosixPath(a.name).parent == name.parent]
    exact = next((a for a in internal if PurePosixPath(a.name).stem == name.stem), None)
    if exact or len(internal) == 1:
        asset = exact or internal[0]
        return decode_chart(asset.read(), asset.name)
    original = Path(original)
    exact = original.with_suffix('.1')
    if exact.is_file():
        return decode_chart(exact.read_bytes(), exact.name)
    siblings = list(original.parent.glob('*.1'))
    if len(siblings) == 1:
        return decode_chart(siblings[0].read_bytes(), siblings[0].name)
    return None


def charts(data):
    if len(data) < 96 or len(data) > 32 * 1024 * 1024:
        raise ValueError('谱面文件无效')
    result = []
    for i in range(12):
        offset, length = struct.unpack_from('<II', data, i * 8)
        if not length:
            continue
        if offset < 96 or length % 8 or offset + length > len(data):
            raise ValueError('谱面索引超出文件范围')
        result.append(dict(id=i, name=CHART_NAMES.get(i, f'谱面 {i + 1}')))
    if not result:
        raise ValueError('文件中没有谱面')
    return result


def source(package, original, bank=None, external=None):
    choices = banks(package)
    if not choices:
        raise ValueError('请打开歌曲的 2DX、S3P 或音频 IFS')
    defaults = {Path(original).stem.lower(), Path(original).stem.lower() + '2'}
    chosen = next((b for b in choices if b['id'] == bank), None) if bank is not None else next(
        (b for b in choices if PurePosixPath(b['name']).stem.lower() in defaults), choices[0])
    if chosen is None:
        raise ValueError('音频包已切换，请重新打开导出窗口')
    return choices, chosen, chart_bytes(package, original, chosen, external)


def song_info(package, original, bank=None, chart_path=None):
    choices, chosen, data = source(package, original, bank, chart_path)
    from .musicdb import metadata, VARIATIONS
    record = metadata(original)
    available = charts(data) if data is not None else []
    for chart in available:
        ident = record['idents'].get(VARIATIONS.get(chart['id'])) if record else None
        stem = record.get('id_text',f"{record['song_id']:05}") + (ident if ident != '0' else '') if ident else ''
        chart['bank'] = next((b['id'] for b in choices if PurePosixPath(b['name']).stem.lower() == stem.lower()), None)
    if bank is None:
        recommended = next((c.get('bank') for c in available if c['id'] == 1), None)
        if recommended: chosen = next(b for b in choices if b['id'] == recommended)
    return dict(banks=choices, bank=chosen['id'], charts=available,
                needs_chart=data is None)


def timebase(events, original):
    for part in Path(original).parts:
        match = re.match(r'^(\d{2})_', part)
        if match:
            version = int(match[1])
            return 1000.0 if version >= 15 else 60.05 if version == 14 else 59.95 if version == 13 else 59.94
    # Unlabelled copies: use measure spacing and BPM to distinguish frames/ms.
    bpm = next((v / d for _, t, d, v in events if t == 4 and d and v), None)
    bars = sorted({tm for tm, t, side, _ in events if t == 12 and side == 0 and tm < 0x7fffffff})
    if bpm and len(bars) > 1:
        estimated = (bars[1] - bars[0]) * bpm / 240
        if 45 < estimated < 80:
            return 59.94
        if 800 < estimated < 1200:
            return 1000.0
    raise ValueError('无法确定谱面计时，请从游戏版本目录打开歌曲')


def chart_events(data, chart, original):
    if chart not in {c['id'] for c in charts(data)}:
        raise ValueError('请选择有效谱面')
    offset, length = struct.unpack_from('<II', data, chart * 8)
    events = list(struct.iter_unpack('<iBBH', data[offset:offset + length]))
    clock = timebase(events, original)
    previous = -100000
    valid = []
    for event in events:
        timestamp = event[0]
        if timestamp == 0x7fffffff:
            break
        if timestamp < previous or timestamp / clock > MAX_SECONDS or timestamp < -100000:
            raise ValueError('谱面时间无效或超过 30 分钟')
        previous = timestamp
        valid.append(event)
    return valid, clock


def note_times(data, chart, original):
    return layer_timing(data,chart,original)[0]


def layer_timing(data, chart, original):
    notes,measures,_=judgement_timing(data,chart,original)
    return sorted(set(notes)),measures


def judgement_timing(data, chart, original):
    events,clock=chart_events(data,chart,original)
    notes=sorted(time/clock for time,kind,_,_ in events if kind in (0,1) and time>=0)
    measures=sorted({time/clock for time,kind,_,_ in events if kind==12})
    end=max((time/clock for time,kind,_,_ in events if kind==6),default=max(notes,default=0)+1)
    return notes,measures,max(end,max(notes,default=0))


def triggers(data, chart, original):
    events, clock = chart_events(data, chart, original)
    assigned, result, end = {}, [], 0.0
    for timestamp, kind, lane, value in events:
        if kind in (2, 3):
            assigned[kind - 2, lane] = value
        elif kind in (0, 1, 7):
            note_lane = lane - 100 if 100 <= lane <= 107 else lane
            sample = value if kind == 7 else assigned.get((kind, note_lane), 0)
            if sample:
                result.append((timestamp / clock, sample))
            elif kind != 7:
                raise ValueError('谱面中有未分配的键音')
        elif kind == 6:
            end = max(end, timestamp / clock)
    if not result:
        raise ValueError('谱面没有可播放的音频事件')
    return result, end, clock


def pcm_sample(blob,folder,sample):
    path = folder / f'{sample}.f32'
    try:
        with wave.open(BytesIO(blob), 'rb') as audio:
            if audio.getframerate() == RATE and audio.getsampwidth() == 2 and audio.getnchannels() in (1, 2):
                if audio.getnframes() > RATE * MAX_SECONDS:
                    raise ValueError('单条音频超过 30 分钟')
                pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32).reshape(-1, audio.getnchannels()) / 32768
                if pcm.shape[1] == 1:
                    pcm = np.repeat(pcm, 2, axis=1)
                pcm.astype('<f4', copy=False).tofile(path)
                return path, len(pcm)
    except (wave.Error, EOFError):
        pass
    return None


def decode_sample(blob, folder, sample):
    """Single-sample fallback preserves exact errors for malformed batch input."""
    path=folder/f'{sample}.f32'
    result=pcm_sample(blob,folder,sample)
    if result is not None:return result
    import imageio_ffmpeg
    source = folder / f'{sample}.audio'
    source.write_bytes(blob)
    try:
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-threads', '1',
                                 '-i', str(source), '-t', str(MAX_SECONDS), '-vn', '-ar', str(RATE), '-ac', '2',
                                 '-f', 'f32le', str(path)], capture_output=True, timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode or not path.exists() or path.stat().st_size % 8:
            raise ValueError(f'第 {sample} 个键音解码失败')
        return path, path.stat().st_size // 8
    finally:
        source.unlink(missing_ok=True)


def export_song(package, original, target, cache, bank, chart, chart_path=None):
    _, chosen, data = source(package, original, bank, chart_path)
    if data is None:
        raise ValueError('缺少配套 .1 谱面')
    if Path(target).suffix.lower() != '.wav':
        raise ValueError('整曲请保存为 WAV')
    events, end, clock = triggers(data, chart, original)
    # Reuse edited track payloads when the currently open file is a sound bank.
    if chosen['id'] == 'package':
        sounds = package.assets
    else:
        asset = package.assets[int(chosen['id'])]
        from .editing import expand_tracks
        sounds = expand_tracks(package, asset)
    positions = defaultdict(list)
    for seconds, sample in events:
        if not 1 <= sample <= len(sounds):
            raise ValueError(f'音频包缺少第 {sample} 个键音，请选择配套音频包')
        positions[sample].append(round(seconds * RATE))
    created = False
    with tempfile.TemporaryDirectory(prefix='song-', dir=cache) as directory:
        folder = Path(directory)
        from .song_decode import decode_samples
        decoded=decode_samples(sounds,positions,folder)
        frames = max(round(end * RATE), max(max(positions[s]) + n for s, (_, n) in decoded.items()))
        if not 0 < frames <= MAX_SECONDS * RATE:
            raise ValueError('合成歌曲超过 30 分钟')
        mix = np.memmap(folder / 'mix.f32', dtype='<f4', mode='w+', shape=(frames, 2))
        try:
            mix[:] = 0
            for sample, (path, count) in decoded.items():
                if not count:
                    continue
                pcm = np.memmap(path, dtype='<f4', mode='r', shape=(count, 2))
                try:
                    for start in positions[sample]:
                        begin, stop = max(0, start), min(frames, start + count)
                        if stop > begin:
                            mix[begin:stop] += pcm[begin - start:stop - start]
                finally:
                    pcm._mmap.close()
            block = RATE * 4
            peak = max(float(np.max(np.abs(mix[i:i + block]))) for i in range(0, frames, block))
            gain = min(1.0, 0.98 / peak) if peak else 1.0
            with Path(target).open('xb') as output:
                created = True
                with wave.open(output, 'wb') as wav:
                    wav.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
                    for i in range(0, frames, block):
                        wav.writeframesraw(np.clip(mix[i:i + block] * (gain * 32768), -32768, 32767).astype('<i2').tobytes())
        except BaseException:
            if created:
                Path(target).unlink(missing_ok=True)
            raise
        finally:
            mix._mmap.close()
    return dict(path=str(target), seconds=frames / RATE, events=len(events), samples=len(positions), timebase=clock)
