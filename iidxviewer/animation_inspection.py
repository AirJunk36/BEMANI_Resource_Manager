"""Trace animation definitions and game-DLL references without guessing usage."""
from functools import lru_cache
import hashlib
from pathlib import Path
import re

from .afp import read_shape
from .engines import PE


PURPOSES = {
    'title': '标题画面', 'mselect': '选曲界面', 'result': '结算画面',
    'd_result': '双人结算画面', 'decide': '曲目决定画面', 'stage': '演奏舞台',
    'entry': '玩家登入', 'card': '卡片认证', 'mode': '模式选择',
    'mode_select': '模式选择', 'expert_result': '组曲结算', 'tutorial': '教学',
    'ranking': '排行榜', 'music_select': '选曲界面', 'credit': '投币与点数显示',
    'ending': '结束画面', 'gameover': '游戏结束', 'game_over': '游戏结束',
    'notes00': '演奏音符', 'test_effect_anime': '判定特效',
}


@lru_cache(maxsize=8)
def dll_strings(path, size, modified):
    if size > 64*1024*1024:
        raise ValueError('游戏 DLL 过大')
    data = Path(path).read_bytes()
    pe = PE(data)
    strings = []
    for section, rva, length, offset, raw_size in pe.sections:
        if section not in {'.rdata', '.data', '.text'}:
            continue
        for match in re.finditer(rb'[\x20-\x7e]{4,1024}\x00', data[offset:offset+raw_size]):
            text = match.group()[:-1].decode('ascii')
            # Keep resource references and identifiers, not every unrelated message.
            if '.ifs' in text.lower() or '.bin' in text.lower() or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_./-]{3,127}', text):
                strings.append((rva+match.start(), text))
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), strings=strings)


def game_usage(resource, engine, animation_name):
    library = (engine or {}).get('library')
    if not library:
        return dict(purpose='未识别', reason='未找到游戏运行库', references=[])
    game = Path(library['path']).with_name('bm2dx.dll')
    if not game.is_file():
        return dict(purpose='未识别', reason='未找到 bm2dx.dll', references=[])
    try:
        stat = game.stat()
        index = dll_strings(str(game), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError) as error:
        return dict(purpose='未识别', reason=str(error), references=[])
    resource = Path(resource)
    relative = resource.as_posix().lower()
    references, candidates = [], []
    for rva, literal in index['strings']:
        normalized = literal.replace('\\', '/').lower()
        if not normalized.endswith(('.ifs', '.bin')):
            continue
        # Require directory identity where the DLL supplies a directory. Two
        # title.ifs files in graphic/0 and graphic/1 are distinct resources.
        path = normalized.removeprefix('./')
        if relative.endswith('/'+path.lstrip('/')) and ('/' in path or resource.name.lower() == path):
            references.append(dict(literal=literal, rva=f'0x{rva:x}', match='path'))
        elif Path(path).name == resource.name.lower():
            candidates.append(dict(literal=literal, rva=f'0x{rva:x}', match='different_directory'))
    identifiers = []
    for rva, literal in index['strings']:
        if literal == animation_name:
            identifiers.append(dict(literal=literal, rva=f'0x{rva:x}', match='animation_identifier'))
    stem = resource.stem.lower()
    base = re.sub(r'_(?:j|a|k)$', '', stem)
    # A name alone is not evidence of a game scene. The textual scene label is
    # applied only after an exact reference in the game's own DLL is found.
    purpose = PURPOSES.get(base, '用途未识别') if references else '未识别'
    return dict(purpose=purpose, library=index['path'], sha256=index['sha256'],
                evidence='依据：DLL 路径；用途按名称推定' if references else '未找到对应路径引用',
                references=references[:30], animation_identifiers=identifiers[:20], candidates=candidates[:20])


def describe(package, animation, source, engine, dll_result=None, dll_error=None, max_layers=2000):
    if getattr(animation, 'legacy', False):
        return describe_legacy(animation, source, engine, max_layers)
    movie = animation.movie
    exports = {}
    for character, name in movie.exports:
        exports.setdefault(character, []).append(name)
    source_name = 'afp/'+movie.name
    selected = next((cid for cid, definition in movie.definitions.items()
                     if definition[0] == 'sprite' and definition[1] is animation.timeline), None)
    result = dict(resource=str(source), animation=animation.name, source_afp=source_name,
                  entry='root' if animation.timeline is movie.root else 'sprite', character=selected,
                  authored_frames=len(animation.timeline.frames), preview_frames=animation.length,
                  labels=[dict(frame=f, name=name) for f, name in animation.timeline.labels],
                  game_usage=game_usage(source, engine, movie.name), layers=[], truncated=False,
                  dll=dll_result, dll_error=dll_error)
    count = 0
    shape_textures = {}
    def walk(timeline, path, visiting):
        nonlocal count
        if id(timeline) in visiting:
            return []
        visiting = visiting | {id(timeline)}
        usages = {}
        state = {}
        for frame, (first, size) in enumerate(timeline.frames):
            for code, value in timeline.tags[first:first+size]:
                if code == 128:
                    state.pop(value, None)
                elif code == 127:
                    depth = value['depth']
                    if 'character' in value:
                        state[depth] = value['character']
                    character = state.get(depth)
                    if character is None:
                        continue
                    key = depth, character
                    if key not in usages:
                        usages[key] = dict(depth=depth, character=character, first_frame=frame, last_keyframe=frame,
                                           mask_end=value.get('clip'), instance_name=None)
                    row = usages[key]
                    row['last_keyframe'] = frame
                    if 'clip' in value:
                        row['mask_end'] = value['clip']
                    if 'name' in value:
                        name = value['name']
                        row['instance_name'] = movie.string(name) if isinstance(name, int) and hasattr(movie, 'string') else str(name)
        rows = []
        for (depth, character), values in sorted(usages.items()):
            if count >= max_layers:
                result['truncated'] = True
                break
            count += 1
            definition = movie.definitions.get(character)
            kind, data = definition if definition else ('missing', None)
            row = dict(values, path=path+[depth], kind=kind, exports=exports.get(character, []), children=[])
            if kind == 'sprite':
                row['frames'] = len(data.frames)
                row['children'] = walk(data, path+[depth], visiting)
            elif kind == 'image':
                row['source'] = 'tex/'+data.removesuffix('.png')+'.png'
            elif kind == 'shape':
                row['source'] = 'geo/'+data
                if data not in shape_textures:
                    asset = package.files.get(row['source'])
                    magic = package.files.get('magic')
                    try:
                        shape = read_shape(asset.read(), '>' if magic and magic.read()==b'NGPF' else '<') if asset else None
                        shape_textures[data] = sorted({'tex/'+shape.names[tex].removesuffix('.png')+'.png'
                                                       for _, _, tex, _, _ in shape.primitives if tex < len(shape.names)}) if shape else []
                    except (ValueError, IndexError):
                        shape_textures[data] = []
                row['textures'] = shape_textures[data]
            rows.append(row)
        return rows
    if hasattr(animation.timeline, 'tags'):
        result['layers'] = walk(animation.timeline, [], set())
    return result


def describe_legacy(animation, source, engine, max_layers=2000):
    result = dict(resource=str(source), animation=animation.name, source_afp=str(source),
                  entry='legacy', character=animation.start, authored_frames=animation.length,
                  preview_frames=animation.length, labels=[], game_usage=game_usage(source, engine, animation.name),
                  layers=[], truncated=False, dll=None,
                  dll_error='IDX 动画按索引顺序读取')
    count = 0
    def walk(start, path, visiting):
        nonlocal count
        if start in visiting or len(visiting)>16:
            return []
        rows = []
        for index, record in enumerate(animation.index.records[start:], start):
            if record['type']<0:
                break
            if count>=max_layers:
                result['truncated']=True
                break
            count+=1
            row=dict(depth=index-start, character=record['id'], kind='sprite' if record['type']==2 else 'image' if record['type']==0 else 'runtime',
                     path=path+[index], first_frame=record['begin'], last_keyframe=record['end'],
                     exports=[], children=[])
            if record['type']==2:
                row['children']=walk(record['id'], path+[index], visiting|{start})
            elif record['type']==0:
                row['source']='tex/'+animation.cell_names.get(record['id'],str(record['id']))+'.png'
            rows.append(row)
        return rows
    result['layers']=walk(animation.start, [], set())
    return result


def text_report(report):
    usage = report['game_usage']
    lines = ['资源：'+report['resource'], '动画：'+report['animation'],
             '游戏用途：'+usage['purpose'], usage.get('evidence', usage.get('reason', ''))]
    if usage.get('library'):
        lines += ['游戏 DLL：'+usage['library'], 'SHA256：'+usage['sha256']]
    for entry in usage['references']:
        lines.append('路径引用：'+entry['literal']+'  RVA '+entry['rva'])
    for entry in usage.get('animation_identifiers', []):
        lines.append('动画名引用：'+entry['literal']+'  RVA '+entry['rva'])
    for entry in usage.get('candidates', []):
        lines.append('同名引用（目录不符）：'+entry['literal']+'  RVA '+entry['rva'])
    entry_label = ' / Root' if report['entry']=='root' else (' / 索引记录 ' if report['entry']=='legacy' else ' / Sprite ')+str(report['character'])
    lines += ['', '动画入口：'+report['source_afp']+entry_label,
              f"原始：{report['authored_frames']} 帧；预览：{report['preview_frames']} 帧"]
    dll = report.get('dll')
    if dll:
        actual = next((a for a in dll.get('animations', []) if a['name']==report['source_afp']), None)
        if actual:
            lines += [f"DLL 读取根时间轴：{actual['frames']} 帧 · {actual['fps']:g} FPS · {actual['width']} × {actual['height']}",
                      '运行库 SHA256：'+dll['library_sha256']]
    elif report.get('dll_error'):
        lines += ['DLL 读取：'+report['dll_error']]
    lines += ['标签：'+', '.join(f"{v['name']}@{v['frame']}" for v in report['labels']), '',
              '图层（索引顺序）：' if report['entry']=='legacy' else '图层（同级按 depth 绘制）：']
    def walk(rows, level):
        for row in rows:
            name = ', '.join(row['exports']) or row.get('instance_name') or row.get('source', row['kind'])
            mask = ' [遮罩至 depth '+str(row['mask_end'])+']' if row.get('mask_end', 0) and row['mask_end']>row['depth'] else ''
            lines.append('  '*level+f"depth {row['depth']} · #{row['character']} · {row['kind']} · {name}"+mask)
            if row.get('source') and row['source'] != name:
                lines.append('  '*(level+1)+row['source'])
            for texture in row.get('textures', []):
                lines.append('  '*(level+1)+'图像：'+texture)
            walk(row['children'], level+1)
    walk(report['layers'], 0)
    if report['truncated']:
        lines.append('仅显示前 2,000 个节点。')
    lines += ['', '各图层按时间轴显示，脚本可能改变层级。']
    return '\n'.join(lines)
