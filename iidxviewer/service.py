"""Platform-neutral resource operations; all game access is read-only."""
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
import shutil
import gc
import subprocess
import sys

from PIL import Image

from .exporting import check_destination, export_animation, original_audio, safe_name, write_new
from .render import SceneRenderer
from .resources import audio_assets, media_suffix, open_package, scan, unwrap_media
from .editing import Editor, expand_tracks
from .frames import SharedFrames
from .engines import detect
from .native_memory import native_scope


class ResourceService:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.package = None
        self.root = None
        self.protected_roots = set()
        self.renderers = {}
        self.frames = OrderedDict()
        self.serial = 0
        self.package_id = 0
        self.editor = Editor(self.cache)
        self.original_path = None
        self.selected_asset = None
        self.edit_notice = ""
        self.gpu = None
        self.gpu_attempted = False
        self.gpu_error = ""
        self.shared = None
        self.raw_frames = OrderedDict()
        self.engine = None
        self.inspection_dll = None
        self.inspection_dll_error = None
        self.layer_controls = {}
        self.previews = OrderedDict()
        self.pending_delete = set()
        self.song_previews = OrderedDict()

    def clear_song_previews(self):
        for reply in self.song_previews.values():
            self.pending_delete.add(Path(reply['path']))
        self.song_previews.clear()
        self.prune_previews()

    def prepare_song(self, request):
        from .song import export_song
        from . import preview_cache
        context = request.get('context_path') or self.original_path
        chart_path = request.get('chart_path')
        stamp = Path(chart_path).stat().st_mtime_ns if chart_path else None
        key = ('audio',self.package_id,request.get('bank'),int(request['chart']),chart_path,stamp)
        if key not in self.song_previews:
            target = self.file('.wav')
            shared=self.song_cache_key(request,'audio')
            reply=preview_cache.get(shared,target)
            if reply is None:
                reply = export_song(self.package,context,target,self.cache,request.get('bank'),int(request['chart']),chart_path)
                preview_cache.put(shared,reply)
            reply.update(kind='audio')
            self.song_previews[key] = reply
        self.song_previews.move_to_end(key)
        while len(self.song_previews) > 3:
            _, old = self.song_previews.popitem(last=False)
            self.pending_delete.add(Path(old['path']))
        self.prune_previews()
        return self.song_previews[key]

    def prepare_song_movie(self, request):
        import json
        from .media_export import song_movie
        from . import preview_cache
        audio = self.prepare_song(request)
        options = dict(request.get('options',{}))
        stamps = [(name,Path(options[name]).stat().st_mtime_ns) for name in ['bga_path','layer_path'] if options.get(name)]
        key = ('video',audio['path'],json.dumps(options,sort_keys=True),tuple(stamps))
        if key not in self.song_previews:
            notes = measures = ()
            if options.get('layer_path'):
                from .song import source, judgement_timing
                context = request.get('context_path') or self.original_path
                _, _, data = source(self.package,context,request.get('bank'),request.get('chart_path'))
                if data is not None:
                    notes,measures,end=judgement_timing(data,int(request['chart']),context)
                    options['_chart_end']=end
            target=self.file('.mp4');shared=self.song_cache_key(request,'video',options,stamps)
            reply=preview_cache.get(shared,target)
            if reply is None:
                reply = song_movie(audio['path'],target,self.cache,options,notes,measures)
                preview_cache.put(shared,reply)
            reply.update(kind='video')
            self.song_previews[key] = reply
        self.song_previews.move_to_end(key)
        while len(self.song_previews) > 3:
            _, old = self.song_previews.popitem(last=False)
            self.pending_delete.add(Path(old['path']))
        self.prune_previews()
        return self.song_previews[key]

    def song_cache_key(self,request,kind,options=None,stamps=()):
        import hashlib
        from .song import source
        from .preview_cache import key
        context=request.get('context_path') or self.original_path
        _,chosen,data=source(self.package,context,request.get('bank'),request.get('chart_path'))
        files=[self.package.path,*self.package.source_files]
        files.extend(options[name] for name in ['bga_path','layer_path'] if options and options.get(name))
        return key(kind,files,[str(context),chosen['id'],int(request['chart']),hashlib.sha256(data or b'').hexdigest(),options,stamps])

    def clear_renderers(self):
        for renderer in self.renderers.values():
            renderer.close()
        self.renderers.clear()
        self.raw_frames.clear()

    def prune_previews(self, all=False):
        while self.previews and (all or len(self.previews) > 8):
            _, reply = self.previews.popitem(last=False)
            path = Path(reply['path'])
            if path.parent == self.cache and path.name.startswith('asset_'):
                self.pending_delete.add(path)
        for path in list(self.pending_delete):
            try:
                path.unlink(missing_ok=True); self.pending_delete.remove(path)
            except OSError:
                pass  # A native player may still be closing its previous file.

    def close_package(self):
        if self.package:
            self.package.close()
            self.package = None

    def close(self):
        self.clear_song_previews()
        self.clear_renderers()
        self.layer_controls.clear()
        self.prune_previews(all=True)
        self.close_package()
        self.raw_frames.clear()
        if self.gpu:
            self.gpu.close(); self.gpu = None
        if self.shared:
            self.shared.close(); self.shared = None

    shutdown = close

    def new_renderer(self, animation, renderer="gpu"):
        drawing = self._new_renderer(animation, renderer)
        index = next(i for i, a in enumerate(self.package.animations) if a is animation)
        drawing.scene.controls = self.controls(index)
        return drawing

    def controls(self, index):
        from .layers import LayerControls
        if not 0 <= index < len(self.package.animations):
            raise ValueError('请选择动画')
        if index not in self.layer_controls:
            for old in list(self.layer_controls):
                if len(self.layer_controls) < 8:
                    break
                if not self.layer_controls[old].hidden:
                    del self.layer_controls[old]
            if self.package.format=='live2d':
                from .live2d import LiveLayers
                self.layer_controls[index]=LiveLayers(self.package,self.package.animations[index])
            else:self.layer_controls[index] = LayerControls(self.package, self.package.animations[index])
        return self.layer_controls[index]

    def animation_layers(self, index, frame=0):
        control = self.controls(index)
        drawing = next((d for (i, _), d in self.renderers.items() if i == index), None)
        if drawing is None:
            self.clear_renderers()
            drawing = self.renderers[index, 'cpu'] = self.new_renderer(self.package.animations[index], 'cpu')
        frame = max(0, min(frame, drawing.animation.length - 1))
        control.discover(drawing.scene, frame)
        return dict(control.info(), package_id=self.package_id)

    def pick_layer(self, request):
        from .picking import pick
        if request.get('package_id', self.package_id) != self.package_id:
            raise ValueError('素材已切换，请重试')
        index, mode = int(request['animation']), request.get('renderer', 'gpu')
        if mode not in {'cpu','gpu'}:
            raise ValueError('请选择 CPU 或 GPU 渲染')
        control = self.controls(index)
        key = index, mode
        if key not in self.renderers:
            self.clear_renderers()
            self.renderers[key] = self.new_renderer(self.package.animations[index], mode)
        drawing = self.renderers[key]
        frame = max(0, min(int(request.get('frame', 0)), drawing.animation.length-1))
        selected, bounds = pick(drawing.scene, frame, float(request['u']), float(request['v']))
        control.discover(drawing.scene, frame)
        while selected and selected not in control.rows:
            selected = selected.rpartition('/')[0]
        return dict(control.info(), package_id=self.package_id, selected=selected, bounds=bounds if selected else None)

    def _new_renderer(self, animation, renderer="gpu"):
        if self.package.format=='live2d':
            from .live2d import LiveScene
            scene=LiveScene(self.package,animation)
        else:scene=None
        if renderer == "cpu":
            drawing=SceneRenderer(self.package, animation)
            if scene:drawing.scene.close();drawing.scene=scene;drawing.issues=scene.issues
            return drawing
        if not self.gpu_attempted:
            self.gpu_attempted = True
            try:
                from .gpu import GpuDevice
                self.gpu = GpuDevice()
            except Exception as error:
                self.gpu_error = str(error)
                print("GPU unavailable: " + self.gpu_error, file=sys.stderr)
        if self.gpu:
            from .gpu import GpuSceneRenderer
            drawing=GpuSceneRenderer(self.package, animation, self.gpu)
        else:drawing=SceneRenderer(self.package, animation)
        if scene:drawing.scene.close();drawing.scene=scene;drawing.issues=scene.issues
        return drawing

    def file(self, suffix):
        self.serial += 1
        return self.cache / f"asset_{self.serial}{suffix}"

    def animation_info(self, i, a):
        from .afp_hosts import canvas
        rect = canvas(self.package,a)
        return {"id": i, "name": a.name, "length": a.length, "fps": a.movie.fps, "width": rect[1] - rect[0], "height": rect[3] - rect[2], "warnings": sorted(a.movie.warnings)}

    def asset_info(self, i, a):
        return {"id": i, "name": a.name, "kind": a.kind, "size": a.size or len(a.payload or b""), "details": a.details,
                "replaceable": a.edit_target is not None}

    @property
    def change_count(self):
        session = self.editor.session(self.original_path)
        if self.package.format=='live2d':return len(self.package.archive.history)
        return len(session.steps) if session else 0

    def info(self):
        p = self.package;changes=self.change_count
        song_preview = False
        if p.format != 'live2d':
            from .song import source, charts
            try:
                _,_,data = source(p,self.original_path or p.path)
                from .musicdb import song_id
                song_preview = bool(charts(data)) if data is not None else song_id(self.original_path or p.path) is not None and not p.path.stem.lower().endswith('_pre')
            except (ValueError,OSError,KeyError):
                pass
        from .musicdb import song_labels
        return {**song_labels(self.original_path or p.path),"path": str(self.original_path or p.path), "assets": [self.asset_info(i, a) for i, a in enumerate(p.assets)], "animations": [self.animation_info(i, a) for i, a in enumerate(p.animations)], "warnings": p.warnings,
                "modified": changes > 0, "change_count": changes, "can_undo": changes > 0,
                "selected_asset": self.selected_asset, "edit_notice": self.edit_notice, "engine": self.engine,"format":p.format,"song_preview":song_preview}

    def open(self, path):
        path = Path(path).resolve()
        package = open_package(self.editor.path(path))
        if not path.is_relative_to(self.cache.resolve()):
            self.protected_roots.add(path.parent)
        self.engine = detect(path, package.format)
        if package.format=='live2d':self.engine=dict(family='Live2D',library=None)
        self.original_path = path
        self.selected_asset = None
        self.edit_notice = ""
        self.accept_package(package)
        return self.info()

    def accept_package(self, package):
        self.clear_song_previews()
        self.close_package()
        self.package = package
        self.package_id += 1
        self.inspection_dll = None
        self.inspection_dll_error = None
        self.clear_renderers()
        self.prune_previews(all=True)
        self.layer_controls.clear()
        self.raw_frames.clear()
        if self.gpu:
            self.gpu.reset_textures()
        for old in self.frames.values():
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
        self.frames.clear()
        # Archive trees and script clips contain cycles, including native pixel
        # buffers not reflected in Python's allocation-based GC thresholds.
        # Collect at package boundaries, never in the playback loop.
        gc.collect()

    def replace(self, request):
        scope = request.get("scope", "asset")
        if scope not in {"asset", "file"}:
            raise ValueError("替换范围无效")
        asset = None
        if scope == "asset":
            index = int(request["asset"])
            if not 0 <= index < len(self.package.assets):
                raise ValueError("请选择可替换的素材")
            asset = self.package.assets[index]
        candidate, selected, messages = self.editor.replace(self.original_path, self.package, asset, request["source"], scope == "file")
        self.accept_package(candidate)
        self.selected_asset = selected
        self.edit_notice = "已替换。" + " ".join(messages)
        return self.info()

    def render(self, index, frame, full=False, transport="png", resolution=None, renderer="gpu"):
        if renderer not in {"cpu", "gpu"}:
            raise ValueError("请选择 CPU 或 GPU 渲染")
        presets = {480: (640, 480), 720: (1280, 720), 1080: (1920, 1080),
                   "480p": (640, 480), "720p": (1280, 720), "1080p": (1920, 1080),
                   "720p-portrait": (720, 1280), "1080p-portrait": (1080, 1920)}
        if resolution is not None and resolution not in presets:
            raise ValueError("请选择有效的预览分辨率")
        renderer_key = index, renderer
        if renderer_key not in self.renderers:
            # Bound renderer caches when switching between hundreds of animations.
            self.clear_renderers()
            self.renderers[renderer_key] = self.new_renderer(self.package.animations[index], renderer)
        drawing = self.renderers[renderer_key]
        frame = max(0, min(frame, drawing.animation.length - 1))
        key = self.package_id, index, frame, full, resolution, renderer, self.controls(index).revision
        rect = getattr(drawing,'rect',drawing.animation.movie.rect)
        w, h = max(1, rect[1]-rect[0]), max(1, rect[3]-rect[2])
        bounds = presets.get(resolution)
        scale = min(1, bounds[0]/w, bounds[1]/h) if bounds else min(1, 1200/max(w,h))
        size = None if full else (max(1, round(w*scale)), max(1, round(h*scale)))
        hardware = hasattr(drawing, "device")
        renderer_info = self.gpu.info if hardware else {"hardware": False, "api": "CPU", "reason": self.gpu_error if renderer == "gpu" else ""}
        if transport == "shared" and not full:
            if key in self.raw_frames:
                self.raw_frames.move_to_end(key)
                image = self.raw_frames[key]
            else:
                image = drawing.render(frame, size)
                self.raw_frames[key] = image
                while len(self.raw_frames)>24 or sum(f.sizeInBytes() for f in self.raw_frames.values())>64*1024*1024:
                    self.raw_frames.popitem(last=False)
            if self.shared is None:
                self.shared = SharedFrames(self.cache)
            reply = self.shared.write(image)
            reply.update(frame=frame, warnings=sorted(drawing.issues)+self.package.warnings, renderer=renderer_info)
            return reply
        if key in self.frames:
            self.frames.move_to_end(key)
            path = self.frames[key]
        else:
            im = drawing.render(frame, size)
            path = self.file(".png")
            if not im.save(str(path), "PNG"):
                raise ValueError("无法保存临时预览")
            self.frames[key] = path
            while len(self.frames) > 180:
                _, old = self.frames.popitem(last=False)
                old.unlink(missing_ok=True)
        return {"kind": "image", "path": str(path), "frame": frame, "warnings": sorted(drawing.issues) + self.package.warnings, "renderer": renderer_info}

    def asset(self, index):
        if index in self.previews:
            self.previews.move_to_end(index)
            return self.previews[index]
        reply = self._asset(index)
        if reply.get('kind') in {'image', 'audio', 'video'} and 'path' in reply:
            self.previews[index] = reply
            self.prune_previews()
        return reply

    def _asset(self, index):
        asset = self.package.assets[index]
        if isinstance(asset.source, Path) and asset.source.suffix.lower() in {'.mp4', '.wav', '.mp3', '.m4a'} and asset.kind in {'音频', '视频'}:
            return dict(kind='video' if asset.kind == '视频' else 'audio', path=str(asset.source), warnings=[])
        if asset.kind == "文件" and isinstance(asset.source, Path) and not asset.name.lower().endswith(".ifs"):
            text_file = asset.name.lower().endswith((".xml", ".txt", ".json"))
            with asset.source.open("rb") as source:
                head = source.read(150000 if text_file else 2048)
            text = head.decode("utf8", "replace") if text_file else head.hex(" ")
            return {"kind": "text", "text": text, "warnings": self.package.warnings}
        data = asset.read()
        if asset.kind in {"图片", "图集"}:
            path = self.file(".png")
            image = Image.open(BytesIO(data)).convert("RGBA")
            image.save(path)
            return {"kind": "image", "path": str(path), "width": image.width, "height": image.height, "warnings": []}
        if asset.kind in {"音频", "视频"}:
            blob = unwrap_media(data)
            # ASF Video Media GUID; S3V can contain audio or video.
            video = asset.kind == "视频" or b"\xc0\xef\x19\xbc\x4d\x5b\xcf\x11\xa8\xfd\x00\x80\x5f\x5c\x44\x2b" in blob[:65536]
            suffix = media_suffix(blob, Path(asset.name).suffix)
            source = self.file(suffix)
            source.write_bytes(blob)
            if video and suffix == ".mp4":
                output = source
            else:
                output = self.file(".mp4" if video else ".wav")
                import imageio_ffmpeg
                executable = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
                argv = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source)]
                argv += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"] if video else ["-vn", "-c:a", "pcm_s16le"]
                try:
                    result = subprocess.run(argv + [str(output)], capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW if __import__("os").name == "nt" else 0)
                except BaseException:
                    output.unlink(missing_ok=True)
                    raise
                finally:
                    source.unlink(missing_ok=True)
                if result.returncode:
                    output.unlink(missing_ok=True)
                    raise ValueError("媒体转换失败：" + result.stderr.decode("utf8", "replace")[-1500:])
            return {"kind": "video" if video else "audio", "path": str(output), "warnings": []}
        if asset.kind == "音频包":
            tracks = expand_tracks(self.package, asset)
            return {"kind": "tracks", "assets": [self.asset_info(next(i for i, entry in enumerate(self.package.assets) if entry is a), a) for a in tracks]}
        if asset.name.lower().endswith(".ifs"):
            path = self.file(".ifs")
            path.write_bytes(data)
            return {"kind": "package", "package": self.open(path)}
        text = data[:150000].decode("utf8", "replace") if asset.name.endswith((".xml", ".txt", ".json")) else data[:2048].hex(" ")
        return {"kind": "text", "text": text, "warnings": self.package.warnings}

    def destination(self, value):
        for root in self.protected_roots:
            check_destination(value, root)
        return check_destination(value, self.root)

    def inspect_animation(self, index, probe=True):
        if not 0 <= index < len(self.package.animations):
            raise ValueError('请选择动画')
        if probe and self.inspection_dll is None:
            self.inspection_dll_error = None
            library = (self.engine or {}).get('library')
            try:
                if not library:
                    raise ValueError('未找到可用的 AFP 运行库')
                from .dll_inspection import inspect_package
                self.inspection_dll = inspect_package(self.package, library['path'])
            except Exception as error:
                self.inspection_dll_error = str(error)
        from .animation_inspection import describe, text_report
        report = describe(self.package, self.package.animations[index], self.original_path,
                          self.engine, self.inspection_dll, self.inspection_dll_error)
        return dict(kind='inspection', text=text_report(report), report=report)

    @native_scope()
    def dispatch(self, request):
        method = request.get("method")
        if method == "scan":
            self.root = Path(request["root"]).resolve()
            self.protected_roots.add(self.root)
            entries = scan(self.root)
            from .musicdb import annotate
            return {"root": str(self.root), "files": [{"path": str(e.path), "relative": e.relative, "name": e.path.name, "version": e.version, "kind": e.kind, "size": e.size, "song_title": title} for e,title in annotate(entries)]}
        if method == "open":
            self.protected_roots.update(Path(p).resolve() for p in request.get('protected_roots',[]))
            return self.open(request["path"])
        if method == "probe_engine":
            return detect(request["path"])
        if not self.package:
            raise ValueError("请先打开资源文件")
        if method=='info':return self.info()
        if method in {'live2d_info','live2d_details','live2d_edit','live2d_save'}:
            if self.package.format!='live2d':raise ValueError('请选择 Live2D 模型')
            doc=self.package.archive
            if method=='live2d_info':return doc.info()
            if method=='live2d_save':return doc.save(self.destination(request['target']))
            if method=='live2d_details':
                section=request['section'];index=int(request.get('index',0));form=int(request.get('form',0))
                if section=='json':return dict(text=__import__('json').dumps(doc.json_files[request['document']],ensure_ascii=False,indent=2))
                row=doc.moc.record('meshes' if section=='uvs' else section,index)
                owner=row.copy()
                if section=='deformers':
                    section='warp' if row['type']==0 else 'rotation';index=row['specific'];row=doc.moc.record(section,index)
                result=dict(section=section,index=index,record=row,owner=owner,form_count=row.get('count',1))
                result['keys']=doc.moc.form_keys(row,form)
                if section in {'meshes','warp','uvs'}:
                    points,_=doc.moc.points(section,index,form);result['points']=points.tolist()
                    if section in {'meshes','uvs'}:result['triangles']=doc.moc.triangles(index).tolist()
                    if section!='uvs':
                        group='mesh_forms' if section=='meshes' else 'warp_forms'
                        result.update(form_section=group,form_index=row['begin']+form,form_record=doc.moc.record(group,row['begin']+form))
                elif section=='rotation':result['record']=doc.moc.record('rotation_forms',row['begin']+form);result['section']='rotation_forms';result['index']=row['begin']+form
                return result
            doc.edit(request)
            for control in self.layer_controls.values():control.revision+=1
            if request.get('operation') in {'texture','undo','redo'}:
                self.clear_renderers();self.package._image_cache.clear()
                if self.gpu:self.gpu.reset_textures()
                for i,path in enumerate(doc.textures):
                    name=path.relative_to(doc.root).as_posix()
                    asset=next((a for a in self.package.assets if a.name==name),None)
                    if asset:asset.source=doc.texture_overrides.get(i,path);asset.size=asset.source.stat().st_size
            self.raw_frames.clear()
            if request.get('operation') in {'json','undo','redo'}:
                import math
                for animation in self.package.animations:
                    if animation.group:
                        entry=doc.refs['Motions'][animation.group][animation.motion]
                        animation.length=max(1,math.ceil(float(doc.json_files[entry['File']]['Meta']['Duration'])*60))
            return doc.info()
        if method == "render":
            return self.render(int(request["animation"]), int(request.get("frame", 0)), transport=request.get("transport", "png"),
                               resolution=request.get("resolution"), renderer=request.get("renderer", "gpu"))
        if method == 'animation_layers':
            return self.animation_layers(int(request['animation']), int(request.get('frame', 0)))
        if method == 'pick_layer':
            return self.pick_layer(request)
        if method == 'set_layer_visible':
            if request.get('package_id', self.package_id) != self.package_id:
                raise ValueError('素材已切换，请刷新列表')
            control = self.controls(int(request['animation']))
            if not isinstance(request.get('visible'), bool):
                raise ValueError('请选择显示状态')
            control.set_visible(str(request['layer']), request['visible'])
            return dict(control.info(), package_id=self.package_id)
        if method == "asset":
            return self.asset(int(request["asset"]))
        if method == 'inspect_animation':
            return self.inspect_animation(int(request['animation']), bool(request.get('probe_dll', True)))
        if method == 'song_info':
            from .song import song_info
            return song_info(self.package, request.get('context_path') or self.original_path, request.get('bank'), request.get('chart_path'))
        if method == 'song_visuals':
            from .musicdb import visual_info
            return visual_info(request.get('context_path') or self.original_path)
        if method == 'related_songs':
            from .musicdb import related_songs
            items = related_songs(request.get('context_path') or self.original_path)
            for item in items: item['working_path'] = str(self.editor.path(Path(item['path'])))
            return dict(songs=items)
        if method == 'layer_info':
            from .media_export import layer_info
            return layer_info(request['path'],request.get('preferred',''))
        if method == 'export_context':
            source=self.package.path;pose=None
            if self.package.format=='live2d':
                doc=self.package.archive
                target=self.cache/f'live2d-export-{self.package_id}-{doc.revision}'
                if not target.exists():doc.save(target)
                source=target/doc.path.name
                pose=[dict(index=i,value=doc.values[name]) for i,name in enumerate(doc.parameter_ids) if name in doc.values]
            return dict(path=str(source),context_path=str(self.original_path),live2d_pose=pose,modified=self.change_count>0,
                        protected_roots=[str(p) for p in self.protected_roots],
                        hidden_layers=sorted(self.controls(int(request['animation'])).hidden) if 'animation' in request else [])
        if method == 'audio_export_info':
            name, _ = original_audio(self.package.assets[int(request['asset'])], probe=True,replaced=self.change_count>0)
            return dict(name=name)
        if method == 'prepare_song':
            return self.prepare_song(request)
        if method == 'prepare_song_movie':
            return self.prepare_song_movie(request)
        if method == 'export_song':
            target = self.destination(request['target'])
            from .media_export import publish
            prepared = self.prepare_song(request)
            publish(prepared['path'],target)
            return dict(prepared,path=str(target))
        if method in {'export_song_movie','export_song_gif'}:
            target = self.destination(request['target'])
            from .media_export import publish, gif_from_video
            prepared = self.prepare_song_movie(request)
            if method=='export_song_gif':
                gif_from_video(prepared['path'],target,self.cache,request.get('options',{}).get('resolution','720p'))
            else:publish(prepared['path'],target)
            return dict(prepared,path=str(target))
        if method == "replace":
            return self.replace(request)
        if method == "undo_replace":
            candidate, selected = self.editor.undo(self.original_path)
            self.accept_package(candidate)
            self.selected_asset = selected
            self.edit_notice = "已撤销替换。"
            return self.info()
        if method == "save_replacement":
            target = self.destination(request["target"])
            return self.editor.save(self.original_path, self.package, target)
        if method == "export":
            target = self.destination(request["target"])
            kind = request["kind"]
            if kind == "asset":
                write_new(target, self.package.assets[int(request["asset"])].read())
            elif kind == "audio_original":
                _, data = original_audio(self.package.assets[int(request["asset"])])
                write_new(target, data)
            elif kind in {'mp4','gif'}:
                from .media_export import animation_mp4, video_mp4, animation_gif, video_gif
                if 'animation' in request:
                    index = int(request['animation'])
                    if request.get('hidden_layers'):
                        control = self.controls(index); control.load()
                        control.hidden.update(k for k in request['hidden_layers'] if k in control.rows)
                    drawing = self.new_renderer(self.package.animations[index],request.get('renderer','gpu'))
                    try:
                        (animation_gif if kind=='gif' else animation_mp4)(drawing,target,self.cache,int(request.get('fps',60)),request.get('resolution','1080p'))
                    finally:
                        drawing.close()
                else:
                    asset = self.package.assets[int(request['asset'])]
                    if asset.kind != '视频': raise ValueError('请选择动画或视频素材')
                    if kind=='gif':video_gif(asset,target,self.cache,request.get('resolution','720p'))
                    else:video_mp4(asset,target,self.cache)
            elif kind == "frame":
                info = self.render(int(request["animation"]), int(request.get("frame", 0)), True)
                write_new(target, Path(info["path"]).read_bytes())
            elif kind in {"webp", "sequence"}:
                renderer = self.new_renderer(self.package.animations[int(request["animation"])])
                try:
                    export_animation(renderer, target, kind == "webp")
                finally:
                    renderer.close()
            elif kind == "textures":
                target.mkdir(exist_ok=False)
                for i, asset in enumerate(a for a in self.package.assets if a.kind == "图片"):
                    write_new(target / f"{i + 1:05}_{safe_name(Path(asset.name).name,replaced=self.change_count>0)}", asset.read())
            elif kind == "audio":
                for parent in list(self.package.assets):
                    if parent.kind == "音频包":
                        expand_tracks(self.package, parent)
                tracks = [a for a in self.package.assets if a.kind == "音频"]
                if not tracks:
                    raise ValueError("未找到音频")
                target.mkdir(exist_ok=False)
                used = set()
                for i, asset in enumerate(tracks):
                    name, data = original_audio(asset,replaced=self.change_count>0)
                    while name.casefold() in used:
                        name = f"{i + 1:05}_{name}"
                    used.add(name.casefold())
                    write_new(target / name, data)
                if self.package.format == "ssp":
                    import json
                    write_new(target / "index.json", json.dumps(self.package.archive.manifest(), ensure_ascii=False, indent=2).encode("utf8"))
            else:
                raise ValueError("未知导出格式")
            return {"path": str(target)}
        raise ValueError("未知操作")
