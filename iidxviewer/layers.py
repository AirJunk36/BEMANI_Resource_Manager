"""Per-animation visibility overrides, independent of script state and source data."""
from pathlib import PurePosixPath
from urllib.parse import quote

from .animation_inspection import describe


def layer_key(parent, depth, character):
    return f'{parent}/{depth}:{character}'


def texture_key(parent, name):
    return parent + '/tex:' + quote(name.removesuffix('.png'), safe='')


class LayerControls:
    LIMIT = 10000

    def __init__(self, package, animation):
        self.rows = {}
        self.hidden = set()
        self.revision = 0
        self.truncated = False
        self.package, self.animation = package, animation
        self.loaded = False
        self.add('root', '', animation.name, '动画', 0)

    def load(self):
        if self.loaded:
            return
        package, animation = self.package, self.animation
        report = describe(package, animation, package.path, None, max_layers=self.LIMIT)
        self.truncated = report['truncated']
        legacy = getattr(animation, 'legacy', False)
        def walk(rows, parent, level):
            for row in rows:
                key = parent + '/' + str(row['path'][-1]) if legacy else layer_key(parent, row['depth'], row['character'])
                source = row.get('source', '')
                name = ' / '.join(row['exports']) or row.get('instance_name') or PurePosixPath(source).name or f"图层 {row['depth']}"
                self.add(key, parent, name, source or {'sprite': '分组', 'image': '图片', 'shape': '几何'}.get(row['kind'], row['kind']), level)
                for texture in row.get('textures', []):
                    self.add(texture_key(key, texture.removeprefix('tex/')), key, PurePosixPath(texture).name, texture, level + 1)
                walk(row['children'], key, level + 1)
        walk(report['layers'], 'root', 1)
        from .afp_hosts import military_actor,motion_clip
        if military_actor(package) is not None and motion_clip(animation.name):
            self.add('root/host-hp','root','HP','宿主血条',1)
        self.loaded = True

    def add(self, key, parent, name, detail, level):
        if key in self.rows:
            return
        if len(self.rows) >= self.LIMIT:
            self.truncated = True
            return
        self.rows[key] = dict(id=key, parent=parent, name=name, detail=detail, level=level)

    def visible(self, key):
        if not self.hidden:
            return True
        while key:
            if key in self.hidden:
                return False
            key = key.rpartition('/')[0]
        return True

    def set_visible(self, key, shown):
        self.load()
        if key not in self.rows:
            raise ValueError('素材已失效，请刷新列表')
        children = {item for item in self.rows if item == key or item.startswith(key + '/')}
        if shown:
            self.hidden.difference_update(children)
            while key:
                self.hidden.discard(key)
                key = key.rpartition('/')[0]
        else:
            self.hidden.update(children)
        self.revision += 1

    def discover(self, scene, frame):
        self.load()
        if scene.playback is None:
            return
        def walk(clip, parent, level):
            for child in clip.children.values():
                key = child.layer_key
                kind, data = child.definition
                source = ('tex/' + data + '.png') if kind == 'image' else 'geo/' + data if kind == 'shape' else '分组'
                self.add(key, parent, child.name or (PurePosixPath(source).name if kind != 'sprite' else '动态图层'), source, level)
                if kind == 'shape':
                    for name, _, _ in scene.shape(data):
                        if name:
                            self.add(texture_key(key, name), key, name, 'tex/' + name + '.png', level + 1)
                walk(child, key, level + 1)
        walk(scene.playback.frame(frame), 'root', 1)

    def info(self):
        children = {}
        for row in self.rows.values():
            children.setdefault(row['parent'], []).append(row)
        rows = []
        def walk(parent):
            for row in children.get(parent, []):
                rows.append(dict(row, visible=self.visible(row['id'])))
                walk(row['id'])
        walk('')
        return dict(layers=rows, truncated=self.truncated)
