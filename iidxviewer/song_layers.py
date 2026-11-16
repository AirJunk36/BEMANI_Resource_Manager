"""IIDX host-driven AFP playback, shared by song preview and movie export.

The game chooses slots 1..8 on key input, lets the root timeline finish, then
waits 60..180 game ticks before another trigger. Slot 0 is the miss animation;
`seq` is the continuous layer. A chart supplies perfect-autoplay key input.
Random choices are seeded so seeking and repeated exports agree.
"""
from bisect import bisect_right
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
import math
import random

AUTO = -2


def root_frames(animation):
    return animation.length if getattr(animation,'legacy',False) else len(animation.timeline.frames)


class Choice(NamedTuple):
    index: int
    ranges: tuple = ()


def resolve(name, names):
    if name in names:
        return Choice(names[name])
    for count in (4,2):
        parts = name.rsplit('_',count)
        if len(parts) == count+1 and parts[0] in names and all(item.isdecimal() or item == 'fine' for item in parts[1:]):
            bounds = [999 if item == 'fine' else int(item) for item in parts[1:]]
            return Choice(names[parts[0]],tuple(zip(bounds[::2],bounds[1::2])))
    return None


def mapping(package, record):
    if not package.animations:
        return None
    roots = [(i,a) for i,a in enumerate(package.animations) if '/' not in a.name] or list(enumerate(package.animations))
    names = {a.name: i for i,a in roots}
    attrs = (record or {}).get('afp', [])
    sequence = next((names[name] for name in ['seq',*attrs] if name in names and (name == 'seq' or name.startswith('seq_'))),None)
    choices = [choice for name in attrs[1:9] if name and (choice := resolve(name,names)) is not None and choice.index != sequence]
    if not choices and sequence is None:
        choices = [Choice(i) for i,a in roots if a.name.isdecimal() and 1 <= int(a.name) <= 8]
        if not choices:
            stem = Path(package.path).stem
            sequence = next((i for i,a in roots if a.name in {stem,'main','root'}),roots[0][0])
    return sequence, choices


def schedule(package, choices, notes, seed=0, measures=()):
    choices = [choice if isinstance(choice,Choice) else Choice(choice) for choice in choices]
    rng = random.Random(seed)
    result = []
    ready = 0
    for seconds in sorted(set(notes)):
        tick = math.ceil(seconds * 60 - 1e-7)
        if tick < ready or tick < 0 or not choices:
            continue
        # bm2dx event 12 increments the host measure counter. Attribute bounds
        # are inclusive; four suffix numbers describe two alternative ranges.
        measure = bisect_right(measures,seconds)
        available = [choice for choice in choices if not measures or not choice.ranges or any(lo <= measure <= hi for lo,hi in choice.ranges)]
        if not available:
            continue
        index = rng.choice(available).index
        animation = package.animations[index]
        # The host queries AFP root-frame completion, not the longest child.
        frames = animation.length if getattr(animation,'legacy',False) else len(animation.timeline.frames)
        length = max(1,frames) / animation.movie.fps
        stop = tick + max(1, math.ceil(length * 60 - 1e-7))
        result.append((tick, stop, index))
        ready = stop + rng.randrange(60, 180)
    return result


def host_schedule(package, host, notes, seed, finish):
    """Deterministic PG playback; Ninja's DLL triggers damage every 50 PGs."""
    rng=random.Random(seed);result=[]
    def append(start,index):
        animation=package.animations[index]
        stop=start+max(1,math.ceil(root_frames(animation)*60/animation.movie.fps-1e-7))
        result.append((start,stop,index))
    append(0,(host['intro'] or host['normal'])[0])
    interval=host.get('hit_every',0)
    if interval and host['hit']:
        count=0
        for tick in notes:
            if tick>=finish:break
            while result[-1][1]<=tick:append(result[-1][1],rng.choice(host['normal']))
            if host.get('hit_idle_only') and result[-1][2] not in host['normal']:continue
            count+=1
            if count%interval:continue
            if result[-1][2] in host['hit']:continue
            start,stop,index=result.pop()
            if tick>start:result.append((start,tick,index))
            append(tick,host['hit'][0])
    while result[-1][1]<max(108001,finish):append(result[-1][1],rng.choice(host['normal']))
    return result


class GameLayerRenderer:
    @property
    def rect(self):return self.animation.movie.rect
    def __init__(self, package, record, notes, mode, measures=(),state='clear',gauge=100,end_time=None):
        from .afp_hosts import military_actor,canvas,profile
        self.profile=profile(package);self.state=state
        if state not in {'auto','normal','miss','hit','clear','failed'}:raise ValueError('Layer 状态无效')
        if not math.isfinite(float(gauge)) or not 0<=float(gauge)<=100:raise ValueError('血量范围为 0–100')
        self.gauge=float(gauge);self.note_ticks=sorted(math.ceil(t*60-1e-7) for t in notes if t>=0)
        self.finish=math.ceil((max(notes,default=1800)+1 if end_time is None else end_time)*60-1e-7)
        layout = mapping(package, record)
        if layout is None:
            raise ValueError('此 Layer 请选择具体动画')
        self.sequence, choices = layout
        if choices and not notes and self.sequence is None and not self.profile:
            raise ValueError('跟随谱面需要配套谱面')
        self.package = package
        self.cues = schedule(package, choices, notes, (record or {}).get('song_id', 0),measures)
        self.host_actor=None
        actor=military_actor(package)
        if actor is not None:
            from .scene import Scene
            self.host_actor=Scene(package,package.animations[actor]);self.sequence=None
        if self.profile:
            self.sequence=None
            self.cues=host_schedule(package,self.profile,self.note_ticks,(record or {}).get('song_id',0),self.finish)
        self.starts = [cue[0] for cue in self.cues]
        first = self.cues[0][2] if self.profile else self.sequence if self.sequence is not None else choices[0].index
        self.animation = SimpleNamespace(length=108001, movie=SimpleNamespace(fps=60, rect=canvas(package,package.animations[first])))
        self.device = None
        self.drawing = None
        if mode == 'gpu':
            from .gpu import GpuDevice, GpuSceneRenderer
            try:
                self.device = GpuDevice()
                self.drawing = GpuSceneRenderer(package, package.animations[first], self.device)
            except Exception:
                if self.device: self.device.close()
                self.device = None
        if self.drawing is None:
            from .render import SceneRenderer
            self.drawing = SceneRenderer(package, package.animations[first])
        self.scenes = {first: self.drawing.scene}
        self.active = None

    def cue_at(self, frame):
        if self.profile:
            scheduled=self.state in {'auto','clear','failed'}
            state=('failed' if self.state=='failed' else 'clear') if scheduled and frame>=self.finish else 'normal' if scheduled else self.state
            if state not in {'auto','normal'} and self.profile.get(state):
                index=self.profile[state][0];a=self.package.animations[index]
                elapsed=max(0,frame-self.finish) if scheduled else frame
                local=math.floor(elapsed*a.movie.fps/60+1e-7)
                local=local%max(1,root_frames(a)) if state in {'miss','hit'} else min(local,max(0,root_frames(a)-1))
                return -1,index,local
        at = bisect_right(self.starts, frame) - 1
        if at >= 0 and frame < self.cues[at][1]:
            start, stop, index = self.cues[at]
            local = math.floor((frame-start) * self.package.animations[index].movie.fps / 60 + 1e-7)
            return at, index, local
        return None

    @property
    def outcome_end(self):
        if not self.profile or self.state not in {'auto','clear','failed'}:return 0
        choices=self.profile['failed' if self.state=='failed' else 'clear']
        return self.finish/60+max((root_frames(self.package.animations[i])/self.package.animations[i].movie.fps for i in choices),default=0)

    def gauge_at(self, frame):
        if self.state not in {'auto','clear','failed'}:return self.gauge
        if frame>=self.finish:return 0
        if not self.note_ticks:return self.gauge
        interval=self.profile.get('hit_every',0) if self.profile else 0
        done=bisect_right(self.note_ticks,frame)
        progress=(done//interval)/max(1,math.ceil(len(self.note_ticks)/interval)) if interval else done/len(self.note_ticks)
        return self.gauge*max(0,1-progress)

    def frame_key(self, frame):
        cue = self.cue_at(frame)
        sequence = None if self.sequence is None else math.floor(frame * self.package.animations[self.sequence].movie.fps / 60 + 1e-7)
        health=round(self.gauge_at(frame),6) if self.profile and (self.profile['gauge'] or self.profile.get('military')) else None
        return sequence, cue, health

    def draw_clip(self, index, frame, size, painter):
        from .scene import Scene
        if index not in self.scenes:
            self.scenes[index] = Scene(self.package, self.package.animations[index])
        self.drawing.scene = self.scenes[index]
        self.drawing.scene.rect=self.rect
        if self.profile:
            self.drawing.scene.gauge=self.gauge_at(self.song_frame)
            self.drawing.scene.show_host_gauge=bool(self.profile.get('military') and not self.profile['gauge'])
            self.drawing.scene.suppress_host_gauge=bool(self.profile['gauge'])
        self.drawing.animation = animation = self.package.animations[index]
        if self.host_actor:
            self.drawing.scene.actor=self.host_actor
            self.drawing.scene.owns_actor=False
            self.drawing.scene.host_frame=math.floor(self.song_frame*self.host_actor.animation.movie.fps/60+1e-7)
        if getattr(animation,'legacy',False): frame %= animation.length
        painter.drawImage(0,0,self.drawing.render(frame,size))

    def render(self, frame, size=None):
        from PySide6.QtGui import QImage, QPainter
        size = size or (640, 480)
        self.song_frame=frame
        image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            if self.sequence is not None:
                local = math.floor(frame*self.package.animations[self.sequence].movie.fps/60 + 1e-7)
                self.draw_clip(self.sequence, local, size, painter)
            cue = self.cue_at(frame)
            if cue is not None:
                at, index, local = cue
                if self.active != at and index in self.scenes and self.scenes[index].playback:
                    self.scenes[index].playback.reset()
                self.draw_clip(index, local, size, painter)
                self.active = at
            if self.profile:
                for index in self.profile['gauge']:
                    self.draw_clip(index,min(frame,self.package.animations[index].length-1),size,painter)
        finally:
            painter.end()
        return image

    def close(self):
        for scene in self.scenes.values():
            if scene is not self.drawing.scene: scene.close()
        self.scenes.clear()
        self.drawing.close()
        if self.host_actor:self.host_actor.close();self.host_actor=None
        if self.device: self.device.close()
        self.drawing = self.device = self.package = None
