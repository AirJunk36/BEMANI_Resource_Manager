"""Stream bounded RGBA frames to FFmpeg; publish only complete new files."""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import math
import gc
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import imageio_ffmpeg

from .resources import open_package, unwrap_media, media_suffix, VIDEO_EXT, IMAGE_EXT
from .native_memory import native_scope

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
SIZES = {'480p': (640,480), '720p': (1280,720), '1080p': (1920,1080),
         '720p-portrait': (720,1280), '1080p-portrait': (1080,1920)}


def publish(source, target):
    created = False
    try:
        with Path(source).open('rb') as src, Path(target).open('xb') as dst:
            created = True
            shutil.copyfileobj(src, dst, 1024 * 1024)
    except BaseException:
        if created: Path(target).unlink(missing_ok=True)
        raise


@lru_cache(maxsize=1)
def hardware_encoder():
    if sys.platform == 'darwin':
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v','error','-f','lavfi','-i',
                                 'color=s=64x64:d=0.05','-c:v','h264_videotoolbox','-f','null','-'],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
        if result.returncode == 0:
            return True
    return False


def codec(size=(1280,720), fps=60):
    if hardware_encoder():
        bitrate = round(min(24_000_000,max(2_000_000,size[0]*size[1]*fps*.12)))
        return ['-c:v','h264_videotoolbox','-b:v',str(bitrate),'-allow_sw','1']
    return ['-c:v','libx264','-preset','veryfast','-crf','20']


def encode(args, frames=None):
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin','-hide_banner','-loglevel','error',
                                    *args], stdin=subprocess.PIPE if frames is not None else subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=log, creationflags=FLAGS)
        try:
            if frames is not None:
                try:
                    for frame in frames:
                        process.stdin.write(frame)
                finally:
                    process.stdin.close()
            code = process.wait(timeout=1800)
            if code:
                log.seek(0)
                raise ValueError('视频编码失败：' + log.read().decode('utf8','replace')[-1200:])
        except BaseException:
            if process.poll() is None: process.kill()
            process.wait()
            raise


def size_for(resolution):
    if resolution not in SIZES: raise ValueError('请选择有效分辨率')
    return SIZES[resolution]


def frame_bytes(renderer, seconds, size, loop=False, offset=0, stretch=False):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QPainter
    w, h = size
    out = QImage(w, h, QImage.Format.Format_RGBA8888); out.fill(0)
    at = math.floor((seconds - offset) * renderer.animation.movie.fps + 1e-7)
    if seconds >= offset and (loop or at < renderer.animation.length):
        at = at % renderer.animation.length if loop else at
        if stretch:
            image=renderer.render(at,size).convertToFormat(QImage.Format.Format_RGBA8888)
            return bytes(image.constBits())
        rect = getattr(renderer,'rect',renderer.animation.movie.rect)
        sw, sh = max(1,rect[1]-rect[0]), max(1,rect[3]-rect[2])
        scale = min(w/sw, h/sh)
        image = renderer.render(at, (max(1,round(sw*scale)),max(1,round(sh*scale))))
        painter = QPainter(out)
        painter.drawImage(QPoint((w-image.width())//2,(h-image.height())//2),image)
        painter.end()
    return bytes(out.constBits())


def frames_for(renderer, duration, size, fps, loop=False, offset=0, stretch=False):
    previous = object(); pixels = None
    source_fps = renderer.animation.movie.fps
    for i in range(math.ceil(duration*fps)):
        seconds = i/fps; at = math.floor((seconds-offset)*source_fps + 1e-7)
        key = (at % renderer.animation.length if loop else at) if seconds >= offset and (loop or at < renderer.animation.length) else None
        if key is not None and hasattr(renderer,'frame_key'): key = renderer.frame_key(key)
        if getattr(renderer,'static',False) and key is not None: key = 0
        if key != previous:
            with native_scope():
                pixels = frame_bytes(renderer,seconds,size,loop,offset,stretch)
            previous = key
        yield pixels


def animation_mp4(renderer, target, cache, fps=60, resolution='1080p'):
    if fps not in (60,120): raise ValueError('请选择 60 或 120 FPS')
    duration = renderer.animation.length / renderer.animation.movie.fps
    if not 0 < duration <= 1800: raise ValueError('动画时长无效')
    w,h = size_for(resolution)
    with tempfile.TemporaryDirectory(prefix='video-',dir=cache) as folder:
        output = Path(folder)/'animation.mp4'
        filters = f'color=c=black:s={w}x{h}:r={fps}[bg];[bg][0:v]overlay=shortest=1:format=auto,format=yuv420p[v]'
        encode(['-f','rawvideo','-pix_fmt','rgba','-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0',
                '-filter_complex',filters,'-map','[v]',*codec((w,h),fps),'-pix_fmt','yuv420p','-t',str(duration),
                '-movflags','+faststart',str(output)],
               frames_for(renderer,duration,(w,h),fps))
        publish(output,target)
    return dict(path=str(target),seconds=duration)


@contextmanager
def video_source(asset, folder):
    if isinstance(asset.source, Path):
        with asset.source.open('rb') as handle: head = handle.read(4)
        if head == b'S3V0':
            data = unwrap_media(asset.read()); path = Path(folder)/('source'+media_suffix(data,'.bin'))
            path.write_bytes(data); yield path
        else:
            yield asset.source
    else:
        data = unwrap_media(asset.read()); path = Path(folder)/('source'+media_suffix(data,'.bin'))
        path.write_bytes(data)
        yield path


def video_mp4(asset, target, cache):
    with tempfile.TemporaryDirectory(prefix='video-',dir=cache) as folder:
        with video_source(asset, folder) as source:
            if source.suffix.lower() == '.mp4':
                publish(source,target)
            else:
                output = Path(folder)/'video.mp4'
                encode(['-i',str(source),'-map','0:v:0','-map','0:a?',
                        '-vf','scale=ceil(iw/2)*2:ceil(ih/2)*2',*codec(),'-pix_fmt','yuv420p',
                        '-c:a','aac','-b:a','256k','-movflags','+faststart',str(output)])
                publish(output,target)
    return dict(path=str(target))


def layer_info(path, preferred=''):
    package = open_package(Path(path))
    try:
        animations = [dict(id=i,name=a.name,length=a.length,fps=a.movie.fps) for i,a in enumerate(package.animations)
                      if '/' not in a.name]
        if not animations:
            animations = [dict(id=i,name=a.name,length=a.length,fps=a.movie.fps) for i,a in enumerate(package.animations)]
        if not animations and (media := next((a for a in package.assets if a.kind in {'图片','视频'}),None)):
            animations = [dict(id=0,name=media.name,length=1,fps=60)]
        choice = next((a['id'] for a in animations if a['name'] == preferred), None)
        if choice is None: choice = next((a['id'] for a in animations if a['name'] == 'seq'), None)
        from .song_layers import AUTO
        if animations:
            animations.insert(0,dict(id=AUTO,name='跟随谱面',length=0,fps=60))
            choice = AUTO
        from .afp_hosts import profile,state_choices
        host=profile(package)
        return dict(animations=animations, selected=choice if choice is not None else animations[0]['id'] if animations else -1,
                    states=state_choices(package),gauge=bool(host and host['gauge']))
    finally:
        package.close()


@contextmanager
def layer_renderer(path, index, renderer, notes=(), measures=(),state='clear',gauge=100,end_time=None):
    package = open_package(Path(path)); device = None; drawing = None
    try:
        from .song_layers import AUTO
        if package.animations and index == AUTO:
            from .song_layers import GameLayerRenderer
            from .musicdb import metadata
            drawing = GameLayerRenderer(package,metadata(path),notes,renderer,measures,state,gauge,end_time)
            yield drawing
            return
        media = next((a for a in package.assets if a.kind in {'图片','视频'}),None)
        if not package.animations and media and media.kind == '视频':
            with tempfile.TemporaryDirectory(prefix='layer-') as folder, video_source(media,folder) as source:
                yield source
            return
        if not package.animations and media and media.kind == '图片':
            from types import SimpleNamespace
            from .render import qimage
            image = media.image()
            class Still:
                static = True
                animation = SimpleNamespace(length=108000,movie=SimpleNamespace(rect=(0,image.width,0,image.height),fps=60))
                cached = None
                def render(self, frame, size):
                    if self.cached is None: self.cached = qimage(image.resize(size))
                    return self.cached
                def close(self): self.cached = None; image.close()
            drawing = Still()
            yield drawing
            return
        if not 0 <= index < len(package.animations): raise ValueError('请选择 Layer 动画')
        if renderer == 'gpu':
            try:
                from .gpu import GpuDevice, GpuSceneRenderer
                device = GpuDevice(); drawing = GpuSceneRenderer(package,package.animations[index],device)
            except Exception:
                if device: device.close(); device = None
        if drawing is None:
            from .render import SceneRenderer
            drawing = SceneRenderer(package,package.animations[index])
        yield drawing
    finally:
        if drawing: drawing.close()
        if device: device.close()
        package.close()
        gc.collect()


@lru_cache(maxsize=32)
def video_size(path,size,stamp):
    stream=imageio_ffmpeg.read_frames(path)
    try:return next(stream)['size']
    finally:stream.close()


def song_movie(audio, target, cache, options, notes=(), measures=()):
    w,h = size_for(options.get('resolution','1080p')); fps = int(options.get('fps',60))
    if fps not in (60,120): raise ValueError('请选择 60 或 120 FPS')
    with wave.open(str(audio)) as wav: duration = wav.getnframes()/wav.getframerate()
    offset = float(options.get('bga_offset',0)); layer_offset = float(options.get('layer_offset',0))
    if not all(math.isfinite(x) and abs(x) <= 1800 for x in [offset,layer_offset]): raise ValueError('偏移时间无效')
    with tempfile.TemporaryDirectory(prefix='movie-',dir=cache) as folder:
        output = Path(folder)/'song.mp4'; args=[]; filters=[]; frames=None
        layer = options.get('layer_path'); bga = options.get('bga_path'); n=0
        if bga:
            with Path(bga).open('rb') as handle: head = handle.read(4)
            if head == b'S3V0':
                data = unwrap_media(Path(bga).read_bytes()); path = Path(folder)/('bga'+media_suffix(data,'.bin'))
                path.write_bytes(data); bga = path
        def run(drawing=None):
            nonlocal n,frames,duration
            if drawing:
                ending=getattr(drawing,'outcome_end',0)
                if ending:duration=max(duration,ending+layer_offset)
                viewport=(w,h)
                if bga:
                    stat=Path(bga).stat();bw,bh=video_size(str(bga),stat.st_size,stat.st_mtime_ns)
                    scale=min(w/bw,h/bh);viewport=(max(1,round(bw*scale)),max(1,round(bh*scale)))
                else:
                    rect=getattr(drawing,'rect',drawing.animation.movie.rect);bw,bh=rect[1]-rect[0],rect[3]-rect[2]
                    scale=min(w/max(1,bw),h/max(1,bh));viewport=(max(1,round(bw*scale)),max(1,round(bh*scale)))
                render_size=viewport
                if options.get('preview'):
                    rect=getattr(drawing,'rect',drawing.animation.movie.rect);sw,sh=max(1,rect[1]-rect[0]),max(1,rect[3]-rect[2])
                    scale=min(1,viewport[0]/sw,viewport[1]/sh);render_size=(max(1,round(sw*scale)),max(1,round(sh*scale)))
                args.extend(['-f','rawvideo','-pix_fmt','rgba','-s',f'{render_size[0]}x{render_size[1]}','-r',str(fps),'-i','pipe:0']); n+=1
                frames=frames_for(drawing,duration,render_size,fps,bool(options.get('layer_loop',False)),layer_offset,True)
                filters.append(f'[0:v]scale={viewport[0]}:{viewport[1]},pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000'+
                               (',colorkey=black:0.08:0.02' if options.get('layer_black_key') else '')+'[fg]')
            elif layer:
                if Path(layer).suffix.lower() in IMAGE_EXT: args.extend(['-loop','1'])
                elif options.get('layer_loop',False) or options.get('layer_animation') == -2: args.extend(['-stream_loop','-1'])
                args.extend(['-i',str(layer)]); n+=1
                filters.append(f'[0:v]trim=start={max(0,-layer_offset)},setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,fps={fps},format=rgba' +
                               (',colorkey=black:0.08:0.02' if options.get('layer_black_key') else '') +
                               f',tpad=start_duration={max(0,layer_offset)}:stop_mode=add:stop_duration=1800:color=0x00000000[fg]')
            if bga:
                if options.get('bga_loop',True): args.extend(['-stream_loop','-1'])
                args.extend(['-i',str(bga)])
                filters.append(f'[{n}:v]trim=start={max(0,-offset)},setpts=PTS-STARTPTS,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},tpad=start_duration={max(0,offset)}:stop_mode=clone:stop_duration=1800[bg]')
                n+=1
            else:
                filters.append(f'color=c=black:s={w}x{h}:r={fps}[bg]')
            if drawing:
                filters.append('[bg][fg]overlay=shortest=1:format=auto,format=yuv420p[v]')
            elif layer:
                filters.append('[bg][fg]overlay=shortest=1:format=auto,format=yuv420p[v]')
            else:
                filters.append('[bg]format=yuv420p[v]')
            args.extend(['-i',str(audio),'-filter_complex',';'.join(filters),'-map','[v]','-map',f'{n}:a:0',
                         *codec((w,h),fps),'-pix_fmt','yuv420p','-c:a','aac','-b:a','256k','-t',str(duration),'-movflags','+faststart',str(output)])
            encode(args,frames)
        if layer and Path(layer).suffix.lower() not in VIDEO_EXT | IMAGE_EXT:
            with layer_renderer(layer,int(options.get('layer_animation',-1)),options.get('renderer','gpu'),notes,measures,
                                options.get('layer_state','clear'),options.get('layer_gauge',100),options.get('_chart_end')) as drawing:
                if isinstance(drawing,Path):
                    layer = drawing
                    run()
                else:
                    run(drawing)
        else:
            run()
        publish(output,target)
    return dict(path=str(target),seconds=duration)
