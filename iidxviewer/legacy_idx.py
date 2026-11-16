"""Legacy IDX/IDR cells and keyframe animations used before TXP2."""
from collections import OrderedDict
from dataclasses import dataclass, field
import math
from pathlib import Path

from PIL import Image

from .legacy_codecs import BinaryReader, decrypt_cbc_cts, dimensions, gc_image, read_file


def sample(keys, time, fallback):
    if not keys:
        return fallback
    if time <= keys[0][0]:
        return keys[0][1:]
    if time >= keys[-1][0]:
        return keys[-1][1:]
    for a, b in zip(keys, keys[1:]):
        if a[0] <= time < b[0]:
            fraction = (time - a[0]) / (b[0] - a[0])
            return tuple(x + (y - x) * fraction for x, y in zip(a[1:], b[1:]))
    return fallback


@dataclass
class IndexData:
    paths: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    records: list = field(default_factory=list)
    cell_names: dict = field(default_factory=dict)
    animation_names: dict = field(default_factory=dict)

    def commands(self,start,time,parent=(1,0,0,1,0,0),colour=(100,0,0),depth=0,hidden=frozenset(),path='root',markers=False):
        from .scene import compose
        if depth>16:raise ValueError('旧版动画嵌套超过上限')
        pa,pb,pblend=colour
        for index,rec in enumerate(self.records[start:],start):
            if rec['type']<0:break
            key=path+'/'+str(index)
            if key in hidden or not rec['begin']<=time<rec['end']:continue
            x,y=sample(rec['position'],time,(0,0));sx,sy=(v/100 for v in sample(rec['scale'],time,(100,100)))
            angle=sample(rec['rotation'],time,(0,0))[0]*2*math.pi/65536
            c,s=math.cos(angle),math.sin(angle);ox,oy=rec['anchor']
            matrix=compose(parent,(c*sx,s*sx,-s*sy,c*sy,x-c*sx*ox+s*sy*oy,y-s*sx*ox-c*sy*oy))
            a,b=sample(rec['alpha'],time,(100,0));alpha=pa*a/100
            total=alpha+pb if pb>0 and pblend&2 else (pa+pb)*(a+b)/100;beta=total-alpha
            code=(pblend|(rec['flags']&3 if alpha!=100 or beta!=0 else 0)|(rec['flags']&16))^(rec['flags']&12)
            if rec['type']==2:
                local=rec['base']+time-rec['begin']
                if rec['duration']>0:local=int(100*local/rec['duration'])
                yield from self.commands(rec['id'],local,matrix,(alpha,beta,code),depth+1,hidden,key,markers)
            elif rec['type']==3 and markers:
                yield key,(('host',rec['id']),matrix,max(0,min(1,alpha/100)),'normal')
            elif rec['type']==0 and 0<=rec['id']<len(self.cells):
                blend='subtract' if code&16 else 'add' if code&2 and alpha+beta>100 else 'normal'
                yield key,(rec['id'],matrix,max(0,min(1,alpha/100)),blend)

    def nodes(self, start, time, parent=(0, 0, 1, 1, 100, 0, 0), depth=0, hidden=frozenset(), path='root', with_keys=False):
        if depth > 16:
            raise ValueError("旧版动画嵌套超过上限")
        if not 0 <= start < len(self.records):
            return
        px, py, psx, psy, pa, pb, pblend = parent
        for record_index, rec in enumerate(self.records[start:], start):
            if rec["type"] < 0:
                break
            key = path + '/' + str(record_index)
            if key in hidden:
                continue
            if not rec["begin"] <= time < rec["end"]:
                continue
            x, y = sample(rec["position"], time, (0, 0))
            sx, sy = (v / 100 for v in sample(rec["scale"], time, (100, 100)))
            a, b = sample(rec["alpha"], time, (100, 0))
            alpha = pa * a / 100
            total = alpha + pb if pb > 0 and pblend & 2 else (pa + pb) * (a + b) / 100
            beta = total - alpha
            authored = alpha != 100 or beta != 0
            code = (pblend | (rec["flags"] & 3 if authored else 0) | (rec["flags"] & 16)) ^ (rec["flags"] & 12)
            pivot = (px + psx * x, py + psy * y)
            x = pivot[0] - psx * sx * rec["anchor"][0]
            y = pivot[1] - psy * sy * rec["anchor"][1]
            sx, sy = sx * psx, sy * psy
            if rec["type"] == 2:
                child_time = rec["base"] + time - rec["begin"]
                if rec["duration"] > 0:
                    child_time = int(100 * child_time / rec["duration"])
                yield from self.nodes(rec["id"], child_time, (x, y, sx, sy, alpha, beta, code), depth + 1, hidden, key, with_keys)
            elif rec["type"] == 0 and 0 <= rec["id"] < len(self.cells):
                _, _, width, height = self.cells[rec["id"]]
                rotation = sample(rec["rotation"], time, (0, 0))[0] * 360 / 65536
                blend = "subtract" if code & 16 else "add" if code & 2 and alpha + beta > 100 else "normal"
                node = (rec["id"], x, y, width * sx, height * sy, pivot, rotation, max(0, min(1, alpha / 100)), blend)
                yield (key, node) if with_keys else node


def parse_index(data):
    outer = BinaryReader(data)
    if len(data) >= 8 and int.from_bytes(data[:4], "big") + 4 == len(data):
        c0, c1, single = data[4:], b"", True
    else:
        size = outer.u32(0)
        c0 = outer.span(4, size)
        size1 = outer.u32(4 + size)
        c1 = outer.span(8 + size, size1)
        if 8 + size + size1 != len(data):
            raise ValueError("IDX 分段长度不匹配")
        single = False
    r = BinaryReader(c0)
    r.span(0, 440)
    result = IndexData()
    count, cells_at, records_at = r.u16(2), r.u32(4), r.u32(16)
    if not 0 < count <= 12:
        raise ValueError("IDX 纹理数量无效")
    for i in range(count):
        value = r.span(20 + i * 32, 32).split(b"\0")[0].decode("shift_jis", "replace")
        if not value:
            raise ValueError("IDX 缺少纹理路径")
        result.paths.append(value)
    at = cells_at
    while True:
        cell = r.values("4H", at)
        if cell[2] == 0:
            break
        x, y, width, height = cell
        dimensions(width, height)
        if x + width > 1024 or y + height > len(result.paths) * 1024:
            raise ValueError("IDX 裁切区域超出图集")
        result.cells.append(cell)
        if len(result.cells) > 100000:
            raise ValueError("IDX 裁切图数量超过上限")
        at += 8
    cells_end = at

    def keys(offset, stride=8, minus_one=False, alpha=False):
        if offset == 0:
            return []
        values = []
        while True:
            t = r.values("h", offset)[0]
            if (t == -1) if minus_one else (t < 0):
                break
            if len(values) > 100000 or values and t < values[-1][0]:
                raise ValueError("IDX 关键帧时间或数量无效")
            if alpha:
                a, b = r.values("bb", offset + 2)
            else:
                a = r.values("h", offset + 2)[0]
                b = r.values("h", offset + 4)[0] if stride >= 6 else 0
            values.append((t, a, b))
            offset += stride
        return values

    records_end = 0
    if records_at:
        for i in range(100000):
            offset = records_at + i * 36
            kind, ident, flags, duration, begin, end, base, _, ax, ay, pos, scale, alpha, rot = r.values("hhH7h4I", offset)
            active=kind>=0 and begin<end
            rotation = keys(r.u32(rot), 4) if rot and active else []
            result.records.append({"type": kind, "id": ident, "flags": flags, "duration": duration,
                                   "begin": begin, "end": end, "base": base, "anchor": (ax, ay),
                                   "position": keys(pos, minus_one=True) if active else [], "scale": keys(scale) if active else [],
                                   "alpha": keys(alpha, 4, alpha=True) if active else [], "rotation": rotation})
            if kind == -2:
                records_end = offset + 36
                break
        else:
            raise ValueError("IDX 动画表未结束")

    def split_names(section, cursor):
        names = []
        rr = BinaryReader(section)
        while cursor < len(section) and section[cursor]:
            name = rr.string(cursor, allow_zero=True)
            # Index strings use Shift-JIS; measure their stored byte length.
            end = section.index(0, cursor)
            names.append(name)
            cursor = end + 1
        cursor += 1
        output = {}
        for name in names:
            output[name] = rr.u16(cursor)
            cursor += 2
        if cursor < len(section) and section[cursor] == 0:
            cursor += 1
        return output, cursor

    def paired_names(section, cursor):
        rr, output = BinaryReader(section), {}
        while cursor < len(section):
            if section[cursor] == 0:
                return output, cursor + 1
            name = rr.string(cursor, allow_zero=True)
            cursor = section.index(0, cursor) + 1
            output[name] = rr.u16(cursor)
            cursor += 2
        raise ValueError("IDX 名称表被截断")

    if single:
        name_at = records_end or cells_end + 44
        if name_at + 4 <= len(c0) and name_at + 4 + r.u32(name_at) == len(c0):
            section = c0[name_at + 4:]
            result.cell_names, at = split_names(section, 0)
            result.animation_names, _ = split_names(section, at)
    else:
        result.cell_names, at = paired_names(c1, 0)
        _, at = paired_names(c1, at)
        result.animation_names, _ = paired_names(c1, at)
    if any(i >= len(result.cells) for i in result.cell_names.values()) or any(i >= len(result.records) for i in result.animation_names.values()):
        raise ValueError("IDX 名称引用无效")
    return result


class LegacyAtlas:
    def __init__(self, paths, folder):
        self.paths, self.folder, self.cache = paths, folder, OrderedDict()

    def image(self, index):
        from .legacy import sibling
        if index not in self.cache:
            path = sibling(self.folder, self.paths[index])
            if not path.is_file():
                path = sibling(self.folder, path.with_suffix(".gcr").name)
            data = read_file(path)
            if path.suffix.lower() == ".gcr":
                data = decrypt_cbc_cts(data, self.folder.name, 32)
            image, origin = gc_image(data, path.stem)
            self.cache[index] = image, origin
        self.cache.move_to_end(index)
        while len(self.cache) > 1 and sum(im.width * im.height * 4 for im, _ in self.cache.values()) > 96 * 1024 * 1024:
            self.cache.popitem(last=False)
        return self.cache[index][0]

    def crop(self, cell):
        x, y, width, height = cell
        output = Image.new("RGBA", (width, height))
        for i in range(y // 1024, (y + height - 1) // 1024 + 1):
            image = self.image(i)
            ox, oy = self.cache[i][1]
            top, bottom = max(y, i * 1024), min(y + height, (i + 1) * 1024)
            output.paste(image.crop((x - ox, top - i * 1024 - oy, x + width - ox, bottom - i * 1024 - oy)), (0, top - y))
        return output

    def close(self):
        self.cache.clear()


@dataclass
class LegacyMovie:
    rect: tuple
    fps: float = 60
    warnings: set = field(default_factory=set)


class LegacyAnimation:
    legacy = True

    def __init__(self, name, index, start, cell_names):
        self.name, self.index, self.start, self.cell_names = name, index, start, cell_names
        records = []
        end = 1
        for rec in index.records[start:]:
            if rec["type"] < 0:
                end = max(end, rec["end"])
                break
            records.append(rec)
            end = max(end, rec["end"])
        self.length = max(1, end)
        if self.length > 100000:
            raise ValueError("IDX 动画帧数超过上限")
        warnings = set()
        if any(r["type"] not in {-2, -1, 0, 2} for r in index.records):
            warnings.add("旧版动画包含游戏运行时元素；外部文字、数值和场景组合尚未还原。")
        self.movie = LegacyMovie((0, 640, 0, 480), warnings=warnings)
        self.movie.rect = self.bounds(records)

    def draw_nodes(self, frame, hidden=frozenset()):
        for node in self.index.nodes(self.start, frame, hidden=hidden):
            yield (self.cell_names[node[0]],) + node[1:]

    def draw_layers(self, frame, hidden=frozenset()):
        for key, node in self.index.nodes(self.start, frame, hidden=hidden, with_keys=True):
            yield key, (self.cell_names[node[0]],) + node[1:]

    def draw_commands(self,frame,hidden=frozenset()):
        # IDX stores front-to-back records, including nested groups. Reverse the
        # flattened list so subtractive mattes precede their additive colour.
        for key,(cell,matrix,alpha,blend) in reversed(list(self.index.commands(self.start,frame,hidden=hidden,markers=True))):
            if isinstance(cell,tuple):yield key,('@host/'+str(cell[1]),(0,0),matrix,alpha,blend)
            else:yield key,(self.cell_names[cell],self.index.cells[cell][2:4],matrix,alpha,blend)

    def bounds(self, records):
        times = {0, self.length - 1}
        for rec in records:
            times.update((max(0, rec["begin"]), max(0, rec["end"] - 1)))
            for track in ["position", "scale", "rotation"]:
                times.update(t for t, _, _ in rec[track] if 0 <= t < self.length)
        times = sorted(times)
        if len(times) > 64:
            times = [times[i * (len(times) - 1) // 63] for i in range(64)]
        xs, ys = [], []
        for time in times:
            for _, x, y, width, height, pivot, angle, alpha, _ in self.index.nodes(self.start, time):
                if not alpha:
                    continue
                cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
                for xx, yy in [(x, y), (x + width, y), (x, y + height), (x + width, y + height)]:
                    dx, dy = xx - pivot[0], yy - pivot[1]
                    xs.append(pivot[0] + dx * cos - dy * sin)
                    ys.append(pivot[1] + dx * sin + dy * cos)
        if not xs:
            return (0, 640, 0, 480)
        rect = math.floor(min(xs)), math.ceil(max(xs)), math.floor(min(ys)), math.ceil(max(ys))
        width, height = max(1, rect[1] - rect[0]), max(1, rect[3] - rect[2])
        if width > 8192 or height > 8192 or width * height > 32 * 1024 * 1024:
            self.movie.warnings.add("画布过大，使用 640 × 480 预览")
            return (0, 640, 0, 480)
        return rect


def load_index(package, path, cancelled):
    from .legacy import LegacyImage, sibling
    from .resources import Asset

    data = read_file(path)
    if path.suffix.lower() == ".idr":
        data = decrypt_cbc_cts(data, path.parent.name, 32)
    index = parse_index(data)
    package.format = "legacy"
    if path not in package.source_files:
        package.source_files.append(path)
    atlas = LegacyAtlas(index.paths, path.parent)
    package.archive = atlas
    missing = []
    for name in index.paths:
        tile = sibling(path.parent, name)
        if not tile.is_file() and not tile.with_suffix(".gcr").is_file():
            missing.append(tile.name)
        else:
            package.source_files.append(tile if tile.is_file() else sibling(path.parent, tile.with_suffix(".gcr").name))
    if missing:
        package.warnings.append("缺少关联纹理：" + "、".join(missing) + "，请保留原目录结构")
    for i, name in enumerate(index.paths):
        package.assets.append(Asset(f"atlas/{Path(name.replace(chr(92), '/')).stem}.png", "图集", LegacyImage(lambda i=i: atlas.image(i)), edit_target={"kind": "legacy_atlas", "index": i}))
    cell_names = {i: f"cell_{i:04}" for i in range(len(index.cells))}
    for name, ident in index.cell_names.items():
        cell_names[ident] = name
    for i, cell in enumerate(index.cells):
        if cancelled():
            raise InterruptedError()
        source = LegacyImage(lambda cell=cell: atlas.crop(cell))
        asset = Asset(f"tex/{cell_names[i]}.png", "图片", source, details=f"{cell[2]} × {cell[3]}", edit_target={"kind": "legacy_image", "rect": cell})
        package.assets.append(asset)
        package.files[asset.name] = asset
    for name, start in sorted(index.animation_names.items()):
        if cancelled():
            raise InterruptedError()
        package.animations.append(LegacyAnimation(name, index, start, cell_names))
    package.assets.append(Asset("original/" + path.name, "文件", path, path.stat().st_size, edit_target={"kind": "legacy_raw", "file": path.name}))
    return package
