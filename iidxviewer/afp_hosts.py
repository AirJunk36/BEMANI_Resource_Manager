"""Host composition measured in bm2dx's military-stage layer controller."""


def military_actor(package):
    if not hasattr(package,'_military_actor'):
        names={a.name.casefold():i for i,a in enumerate(package.animations)}
        package._military_actor=names.get('01') if {'01','exp_aaa',*[f'm{i:02}' for i in range(11)]}.issubset(names) else None
    return package._military_actor


def motion_clip(name):
    name=name.casefold()
    return len(name)==3 and name[0]=='m' and name[1:].isdigit() or name.startswith('x_motion')


def canvas(package, animation):
    from pathlib import Path
    path=Path(package.path)
    if getattr(animation,'legacy',False) and (path.stem.isdecimal() or path.parent.name.isdecimal()):
        return (0,640,0,480)
    return animation.movie.rect


def profile(package):
    names={a.name.casefold():i for i,a in enumerate(package.animations) if '/' not in a.name}
    def ids(*items):return tuple(names[n] for n in items if n in names)
    if military_actor(package) is not None:
        return dict(normal=ids(*[f'm{i:02}' for i in range(9)]),intro=ids('m10'),miss=ids('00'),hit=ids('00'),clear=ids('exp_aaa'),failed=ids('exp_no'),gauge=ids('hp'),military=True,hit_every=0)
    if {'boss_in','boss_damage','boss_destroyed','boss_escape'}<=names.keys():
        return dict(normal=ids(*[f'boss_neutral{i:02}' for i in range(1,5)]),intro=ids('boss_in'),
                    miss=ids(*[f'boss_attack{i:02}' for i in range(1,4)]),hit=ids('boss_damage'),clear=ids('boss_destroyed'),failed=ids('boss_escape'),gauge=ids('enemy_hp'),hit_every=50)
    if {'idle','clear','failed'}<=names.keys():
        return dict(normal=ids('idle'),intro=ids('in'),miss=ids('miss'),hit=ids('attack'),clear=ids('clear'),failed=ids('failed'),gauge=(),hit_every=21,hit_idle_only=True)
    return None


def state_choices(package):
    host=profile(package)
    if not host:return []
    return [dict(id='clear',name='Clear'),dict(id='failed',name='Failed')]


def gauge_draws(rect, percent):
    """Military host UI when modern AFP packages omit the legacy HP track.

    Positions use the legacy host's 640x480 coordinates. Geometry and digit
    strokes are generated here; no assets are borrowed from another game.
    """
    from .scene import Draw
    x0,x1,y0,y1=rect;sx=(x1-x0)/640;sy=(y1-y0)/480
    draws=[]
    def box(x,y,w,h,color):
        if w<=0 or h<=0:return
        a,b=x0+x*sx,y0+y*sy;c,d=a+w*sx,b+h*sy
        draws.append(Draw(None,((a,b,0,0),(c,b,0,0),(a,d,0,0),(a,d,0,0),(c,b,0,0),(c,d,0,0)),multiply=color,layer='root/host-hp'))
    box(174,14,292,18,(.08,.08,.1,.95));box(175,15,290,16,(.65,.65,.68,1))
    box(176,16,288,14,(.08,.08,.1,1));box(178,25,284*max(0,min(100,percent))/100,3,(.2,.85,.4,1))
    segments=((0,0,5,1),(4,0,1,4),(4,3,1,4),(0,6,5,1),(0,3,1,4),(0,0,1,4),(0,3,5,1))
    digits=('abcdef','bc','abdeg','abcdg','bcfg','acdfg','acdefg','abc','abcdefg','abcdfg')
    values=f'{max(0,min(1000,round(percent*10))):04d}1000'
    for x,n in zip((195,204,213,222,239,248,257,266),values):
        for key,(a,b,w,h) in zip('abcdefg',segments):
            if key in digits[int(n)]:box(x+a,16+b,w,h,(1,1,1,1))
    for i in range(5):box(232-i,16+i,1,1,(1,1,1,1))
    return draws
