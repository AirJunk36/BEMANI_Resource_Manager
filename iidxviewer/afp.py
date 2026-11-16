"""Native AP2 timeline reader, independent of Windows game DLLs.

Binary layouts were cross-checked against the 573Renderer format documentation.
The portable bytecode interpreter and instance clocks live in actions/playback.
Three-dimensional scene rendering remains outside this reader's scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import struct
from functools import cached_property
from collections import OrderedDict


class Reader:
    def __init__(self, data, endian="<"):
        self.data, self.endian = data, endian

    def values(self, fmt, off):
        size = struct.calcsize(self.endian + fmt)
        if off < 0 or off + size > len(self.data):
            raise ValueError(f"动画数据越界：0x{off:x}")
        return struct.unpack_from(self.endian + fmt, self.data, off)

    def u16(self, off): return self.values("H", off)[0]
    def u32(self, off): return self.values("I", off)[0]


def restore(data: bytes, script: bytes = b"") -> bytes:
    data = bytearray(data)
    r = Reader(data)
    if len(data) < 56:
        raise ValueError("AFP 头被截断")
    if (r.u32(0) ^ 0xC1D0B2FF) & 0x7F7F7F00:
        if not script or len(script) % 2:
            raise ValueError("此 AFP 缺少有效的 BSI 字节序信息")
        cursor = 0
        for (word,) in struct.iter_unpack("<H", script):
            if not word:
                break
            kind, loops, skip = word >> 13, (word >> 7) & 63, word & 127
            cursor += skip * 2
            if kind == 0:
                cursor += loops * 256
                continue
            if kind > 3:
                raise ValueError("未知 BSI 操作")
            width = 1 << kind
            for _ in range(loops + 1):
                if cursor + width > len(data):
                    raise ValueError("BSI 超出动画范围")
                data[cursor:cursor + width] = reversed(data[cursor:cursor + width])
                cursor += width
    if (r.u32(0) ^ 0xC1D0B2FF) & 0x7F7F7F00:
        raise ValueError("此 AFP 版本尚不支持")
    off, size = r.values("II", 48)
    if off + size > len(data):
        raise ValueError("AFP 字符串表无效")
    if r.u16(8) != 1 and size and data[off] == 128:
        data[off:off + size] = bytes((v - 128 - i) & 255 for i, v in enumerate(data[off:off + size]))
    return bytes(data)


@dataclass
class Timeline:
    frames: list
    tags: list
    labels: list = field(default_factory=list)


@dataclass
class Movie:
    name: str
    root: Timeline
    rect: tuple
    fps: float
    definitions: dict
    exports: list
    warnings: set = field(default_factory=set)


@dataclass
class Animation:
    name: str
    movie: Movie
    timeline: Timeline

    @cached_property
    def length(self):
        # Root timelines can be a one-frame wrapper around a longer sprite.
        def duration(tl, visited):
            if id(tl) in visited:
                return 1
            visited = visited | {id(tl)}
            children = [self.movie.definitions.get(v.get("character")) for c, v in tl.tags if c == 127]
            return max([len(tl.frames), 1] + [duration(d[1], visited) for d in children if d and d[0] == "sprite"])
        return duration(self.timeline, set())


class AP2:
    def __init__(self, data, name):
        self.r = Reader(data)
        self.data = data
        self.definitions = {}
        self.warnings = set()
        self.name = name
        self.str_off, self.str_size = self.r.values("II", 48)

    def string(self, ref):
        off = (ref & 0xFFFC) | ((ref & 3) << 16)
        if off >= self.str_size:
            raise ValueError("AFP 字符串引用无效")
        start = self.str_off + off
        end = self.data.find(b"\0", start, self.str_off + self.str_size)
        if end < 0:
            raise ValueError("AFP 字符串未结束")
        return self.data[start:end].decode("utf-8", "replace")

    def placement(self, data):
        r = Reader(data)
        flags, depth, end = r.values("IHH", 0)
        out = {"flags": flags, "depth": depth, "end": end}
        p, ext = 8, 0

        def take(fmt):
            nonlocal p
            v = r.values(fmt, p)
            p += struct.calcsize("<" + fmt)
            return v[0] if len(v) == 1 else v

        if flags & 0x80000000:
            ext = take("I")
        for bit, key in [(2, "character"), (0x10, "ratio"), (0x20, "name"), (0x40, "clip")]:
            if flags & bit:
                out[key] = take("H")
        if flags & 0x20000:
            out["blend"] = take("B")
        p = (p + 3) & ~3
        for bit, key, divisor in [(0x100, "scale", 1024), (0x200, "skew", 1024), (0x400, "position", 20)]:
            if flags & bit:
                out[key] = tuple(v / divisor for v in take("ii"))
        for bit, key in [(0x800, "multiply"), (0x1000, "add")]:
            if flags & bit:
                out[key] = tuple(v / 255 for v in take("hhhh"))
        for bit, key in [(0x2000, "multiply"), (0x4000, "add")]:
            if flags & bit:
                # A little-endian uint32 containing 0xRRGGBBAA, not four
                # channels in byte order. Reading bytes makes alpha tint red.
                rgba = take("I")
                out[key] = tuple(((rgba >> shift) & 255) / 255 for shift in (24, 16, 8, 0))
        if flags & 0x80:
            event_flags, event_size = r.values("II", p)
            if event_size < 8 or p+event_size > len(data):
                raise ValueError("AFP 事件范围无效")
            events = []
            if event_flags:
                count = r.u16(p+10)
                if p+12+count*8 > p+event_size: raise ValueError("AFP 事件索引越界")
                entries=[]
                for index in range(count):
                    at=p+12+index*8
                    trigger, _, key, off = r.values("IBBH",at)
                    entries.append((trigger,at+off))
                for index,(trigger,start) in enumerate(entries):
                    end=entries[index+1][1] if index+1<len(entries) else p+event_size
                    if not p<=start<=end<=p+event_size:raise ValueError("AFP 事件脚本越界")
                    events.append((trigger,data[start:end]))
                    if trigger & ~7: self.warnings.add("含交互事件")
            out["events"] = events
            p += event_size
        if flags & 0x10000:
            self.warnings.add("滤镜尚未还原")
            p += r.u16(p + 2)
        if flags & 0x1000000:
            out["origin"] = tuple(v / 20 for v in take("ii"))
        if ext & 2:
            take("i")
        if flags & 0x2000000:
            take("I")
            self.warnings.add("含游戏几何数据")
        for bit, key in [(0x40000, "scale"), (0x80000, "skew")]:
            if flags & bit:
                out[key] = tuple(v / 32768 for v in take("hh"))
        if flags & 0x100000:
            take("H")
        p = (p + 3) & ~3
        if flags & (0x8000000 | 0x10000000):
            self.warnings.add("三维变换仅预览二维部分")
        if flags & 0x20000000:
            self.warnings.add("HSV 颜色变换尚未还原")
        if ext & ~2:
            self.warnings.add("扩展控制器或曲线尚未还原")
        return out

    def container(self, base, depth=0):
        if depth > 32:
            raise ValueError("AFP 嵌套过深")
        flags, labels, count, tagcount, labeloff, frameoff, tagoff = self.r.values("HHIIIII", base)
        if flags & 3:
            raise ValueError(f"暂不支持的时间轴标记：0x{flags:x}")
        if count > 100000 or tagcount > 100000:
            raise ValueError("AFP 时间轴过大")
        frames = []
        for i in range(count):
            v = self.r.u32(base + frameoff + i * 4)
            first, n = v & 0xFFFFF, v >> 20
            if first + n > tagcount:
                raise ValueError("AFP 帧引用超出标签表")
            frames.append((first, n))
        timeline = Timeline(frames, [])
        for i in range(labels):
            f, ref = self.r.values("HH", base + labeloff + i * 4)
            timeline.labels.append((f, self.string(ref)))
        p = base + tagoff
        for _ in range(tagcount):
            header = self.r.u32(p)
            code, size = header >> 22, header & 0x3FFFFF
            if size & 1 or p + 4 + size > len(self.data):
                raise ValueError("AFP 标签长度无效")
            payload = self.data[p + 4:p + 4 + size]
            tr = Reader(payload)
            if code == 121:
                sf, sid, off = tr.values("HHI", 0)
                if sf not in {0,1}:
                    raise ValueError("AFP 子时间轴类型尚不支持")
                self.definitions[sid] = ("sprite", self.container(p + 4 + off if sf else p+8, depth + 1))
                value = None
            elif code == 131:
                _, cid, ref = tr.values("IHH", 0)
                self.definitions[cid] = ("image", self.string(ref))
                value = None
            elif code == 132:
                cid = tr.u16(2)
                self.definitions[cid] = ("shape", f"{self.name}_shape{cid}")
                value = None
            elif code == 127:
                value = self.placement(payload)
            elif code == 128:
                value = tr.u16(2)
            else:
                value = payload
                if code not in {121, 122, 131, 132}:
                    self.warnings.add(f"未执行的 AFP 标签：{code}")
            timeline.tags.append((code, value))
            p += size + 4
        return timeline

    def parse(self):
        root = self.container(self.r.u32(36))
        exports = []
        count, imports = self.r.values("HH", 32)
        for i in range(count):
            cid, ref = self.r.values("HH", self.r.u32(40) + i * 4)
            exports.append((cid, self.string(ref)))
        if imports:
            self.warnings.add("缺少外部素材时，图层可能不全")
        rect = self.r.values("hhhh", 16)
        fps = self.r.values("i", 24)[0] / 1024 if self.r.u32(12) & 2 else self.r.values("f", 24)[0]
        movie = Movie(self.name, root, rect, fps if 1 <= fps <= 240 else 60, self.definitions, exports, self.warnings)
        movie.string = self.string
        return movie


def load_animations(package, cancelled):
    animations, warnings = [], []
    for name, asset in package.files.items():
        if not name.startswith("afp/") or name.count("/") != 1 or name.endswith(".xml"):
            continue
        if cancelled():
            raise InterruptedError()
        script = package.files.get("afp/bsi/" + name[4:])
        try:
            data = restore(asset.read(), script.read() if script else b"")
            movie = AP2(data, name[4:]).parse()
            animations.append(Animation(movie.name, movie, movie.root))
            for cid, export in movie.exports:
                definition = movie.definitions.get(cid)
                if definition and definition[0] == "sprite" and definition[1] is not movie.root:
                    animations.append(Animation(f"{movie.name} / {export}", movie, definition[1]))
        except Exception as exc:
            warnings.append(f"{name}：{exc}")
    # The engine tries the package's own entry name before other AFP files.
    # Lexicographic archive order can otherwise select a small component such
    # as frame_layer2 when opening title.ifs.
    primary = next((a for a in animations if a.timeline is a.movie.root
                    and a.movie.name.casefold() == package.path.stem.casefold()), None)
    if primary is not None:
        animations.remove(primary)
        animations.insert(0, primary)
    return animations, warnings


def apply_placement(previous, value, frame):
    """Apply AP2 control groups; carried matrix/colour fields replace a group."""
    update = dict(previous) if value['flags'] & 1 else {}
    matrix = {'scale', 'skew', 'position'}
    colour = {'multiply', 'add'}
    if value['flags'] & 4:
        # A 3D placement updates XY without replacing the held 2D basis.
        for key in ({'position'} if value['flags'] & 0x04000000 else matrix):
            update.pop(key, None)
        update.update((k, v) for k, v in value.items() if k in matrix)
    if value['flags'] & 8:
        for key in colour:
            update.pop(key, None)
        update.update((k, v) for k, v in value.items() if k in colour)
    if 'character' in value and (value['character'] != previous.get('character') or not value['flags'] & 1):
        update['start'] = frame
    update.update((k, v) for k, v in value.items() if k not in matrix | colour)
    update.setdefault('start', frame)
    return update


def display_list(timeline: Timeline, frame: int):
    """Reconstruct persistent depth state, including inherited transform fields."""
    frame = min(frame, len(timeline.frames)-1)
    cache = getattr(timeline, "_display_cache", None)
    if cache is None:
        cache = timeline._display_cache = OrderedDict()
    if frame in cache:
        cache.move_to_end(frame)
        return cache[frame]
    state = {}
    start = 0
    earlier = [i for i in cache if i < frame]
    if earlier:
        previous_frame = max(earlier)
        state = dict(cache[previous_frame])
        start = previous_frame+1
    for fi in range(start, frame+1):
        first, n = timeline.frames[fi]
        for code, value in timeline.tags[first:first + n]:
            if code == 128:
                state.pop(value, None)
            elif code == 127:
                depth = value["depth"]
                previous = state.get(depth, {})
                if value['flags'] & 1 and not previous:
                    continue
                state[depth] = apply_placement(previous, value, fi)
    cache[frame] = state
    while len(cache)>64:
        cache.popitem(last=False)
    return state


@dataclass
class Shape:
    vertices: list
    uvs: list
    names: list
    primitives: list


def read_shape(data, endian):
    r = Reader(data, endian)
    if r.u32(0) != 0x47453244:
        raise ValueError("GE2D 字节序或标识无效")
    nv, nu, nc, nn, np = r.values("HHHHH", 20)
    vo, uo, co, no, po = r.values("IIIII", 32)
    vertices = [r.values("ff", vo + i * 8) for i in range(nv)]
    uvs = [r.values("ff", uo + i * 8) for i in range(nu)]
    if any(not math.isfinite(v) or abs(v) > 1e7 for pt in vertices + uvs for v in pt):
        raise ValueError("GE2D 坐标无效")
    names = []
    for i in range(nn):
        off = r.u32(no + i * 4)
        end = data.find(b"\0", off)
        if off >= len(data) or end < 0:
            raise ValueError("GE2D 纹理名无效")
        names.append(data[off:end].decode("utf8", "replace"))
    primitives = []
    for i in range(np):
        off = po + i * 16
        kind, flags, tex, tex2, count = r.values("BBBBH", off)
        color = tuple(data[off + 8:off + 12])
        indices = r.values(f"{count}H", r.u32(off + 12))
        if any(j >= nv for j in indices):
            raise ValueError("GE2D 顶点索引无效")
        primitives.append((kind, flags, tex, color, indices))
    return Shape(vertices, uvs, names, primitives)
