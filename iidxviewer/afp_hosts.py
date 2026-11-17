"""Host composition measured in bm2dx's military-stage layer controller."""


def military_actor(package):
    if not hasattr(package,'_military_actor'):
        names={a.name.casefold():i for i,a in enumerate(package.animations)}
        package._military_actor=names.get('01') if {'01','exp_aaa',*[f'm{i:02}' for i in range(11)]}.issubset(names) else None
    return package._military_actor


def motion_clip(name):
    name=name.casefold()
    return len(name)==3 and name[0]=='m' and name[1:].isdigit() or name.startswith('x_motion')


def canvas(package, animation, playback=False):
    from pathlib import Path
    path=Path(package.path)
    local=getattr(animation,'local',False) or (hasattr(animation.movie,'root') and animation.timeline is not animation.movie.root)
    if local and not playback:return animation.movie.rect
    number=path.stem if path.stem.isdecimal() else path.parent.name
    if getattr(package,'format','') in {'legacy','txp2'} and number.isdecimal():
        # Old gameplay scenes use screen coordinates. Their 304x416 movie
        # window starts at x=168; fitting all 640x480 shrinks and shifts layers.
        # VANESSA's portraits start at y=1; its paired movie omits that row.
        # Correct composition only, keeping isolated clips and exports intact.
        top=1 if playback and number=='1409' else 0
        return (168,472,top,416+top)
    return animation.movie.rect


def profile(package):
    names={a.name.casefold():i for i,a in enumerate(package.animations) if '/' not in a.name}
    def ids(*items):return tuple(names[n] for n in items if n in names)
    if military_actor(package) is not None:
        hp=ids('hp')
        return dict(normal=ids(*[f'm{i:02}' for i in range(9)]),intro=ids('m10'),miss=ids('00') if hp else (),hit=(),clear=ids('exp_aaa'),failed=ids('exp_no'),gauge=hp,military=True,hit_every=0)
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
