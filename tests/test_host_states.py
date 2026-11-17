from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor
from iidxviewer.afp import Animation
from iidxviewer.afp_hosts import profile,state_choices
from iidxviewer.resources import open_package
from iidxviewer.musicdb import metadata,visual_info
from iidxviewer.service import ResourceService
from iidxviewer.song_layers import GameLayerRenderer
from iidxviewer.media_export import song_movie
from tests.test_playback import fixture,timeline,place
from tests.test_song import pcm,chart_file
from tests.test_replacements import two_tracks
from tests.test_media_export import ffmpeg

ROOT=Path(os.environ.get('IIDX_GAME_ROOT',Path(__file__).resolve().parents[2]))


class HostStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QGuiApplication.instance() or QGuiApplication([])

    def test_boss_intro_idle_outcomes_and_hp_are_independent_and_seekable(self):
        p,a=fixture(timeline([[place(2)],[],[],[]]))
        names=['boss_in','boss_neutral01','boss_neutral02','boss_neutral03','boss_neutral04','boss_attack01','boss_attack02','boss_attack03','boss_damage','boss_destroyed','boss_escape','enemy_hp']
        p.animations=[Animation(name,a.movie,a.timeline) for name in names]
        host=profile(p);self.assertTrue(host['gauge']);self.assertEqual(state_choices(p),[dict(id='clear',name='Clear'),dict(id='failed',name='Failed')])
        for state,name in [('miss','boss_attack01'),('hit','boss_damage')]:
            r=GameLayerRenderer(p,{},[0,2],'cpu',state=state,gauge=50)
            try:
                self.assertEqual(p.animations[r.cue_at(30)[1]].name,name)
                self.assertEqual(r.cue_at(0),r.cue_at(0));self.assertGreaterEqual(r.cue_at(300)[2],0)
                r.render(30,(64,64));self.assertEqual(r.scenes[host['gauge'][0]].gauge,50)
            finally:r.close()
        r=GameLayerRenderer(p,{},[0,2],'cpu')
        try:
            self.assertEqual(p.animations[r.cue_at(0)[1]].name,'boss_in')
            self.assertTrue(p.animations[r.cue_at(12)[1]].name.startswith('boss_neutral'))
            self.assertEqual(p.animations[r.cue_at(181)[1]].name,'boss_destroyed')
        finally:r.close()

    def test_boss_clear_uses_pg_and_failed_uses_poor_for_the_entire_chart(self):
        from iidxviewer.song import judgement_timing
        p,a=fixture(timeline([[place(2)],[],[],[]]))
        names=['boss_in','boss_neutral01','boss_neutral02','boss_neutral03','boss_neutral04','boss_attack01','boss_attack02','boss_attack03','boss_damage','boss_destroyed','boss_escape','enemy_hp']
        p.animations=[Animation(name,a.movie,a.timeline) for name in names]
        data=chart_file([(100,0,i%7,0) for i in range(49)]+[(200,1,0,0),(3000,6,0,0)])
        notes,measures,end=judgement_timing(data,1,'33_test/song.2dx')
        self.assertEqual(len(notes),50);self.assertEqual(end,.2)
        renderers=[GameLayerRenderer(p,{},notes,'cpu',measures,state,end_time=3) for state in ['clear','failed']]
        try:
            self.assertEqual(p.animations[renderers[0].cue_at(12)[1]].name,'boss_damage')
            for r in renderers:
                self.assertEqual(r.cue_at(12)[2],0)
                self.assertEqual(r.cue_at(12),r.cue_at(12))
            self.assertTrue(p.animations[renderers[1].cue_at(12)[1]].name.startswith('boss_attack'))
            self.assertNotEqual(renderers[0].cues,renderers[1].cues)
            self.assertEqual(renderers[0].gauge_at(12),0)
            self.assertEqual(renderers[1].gauge_at(12),100)
            self.assertEqual(renderers[1].gauge_at(180),100)
            self.assertFalse(any(p.animations[index].name=='boss_damage' for _,_,index in renderers[1].cues))
            self.assertEqual(p.animations[renderers[0].cue_at(180)[1]].name,'boss_destroyed')
            self.assertEqual(p.animations[renderers[1].cue_at(180)[1]].name,'boss_escape')
            self.assertEqual(renderers[0].cue_at(180)[2],0)
            self.assertGreater(renderers[0].outcome_end,3)
        finally:
            for r in renderers:r.close()

    def test_real_icarus_health_overlay_in_old_and_new_packages(self):
        from iidxviewer.render import SceneRenderer
        paths=['16_EMPRESS/data/graph/sys/1523/osprey.bin','33_Sparkle_Shower/data/graphic/15023.ifs']
        tested=0
        for relative in paths:
            path=ROOT/relative
            if not path.exists():continue
            before=hashlib.sha256(path.read_bytes()).digest();p=open_package(path)
            renderer=SceneRenderer(p,next(a for a in p.animations if a.name.casefold()=='m00'))
            try:
                draws=renderer.scene.draws(60)
                if relative.startswith('16_'):
                    self.assertIn('HP_GAUGE',[d.texture for d in draws]);self.assertIn('HP_WAKU',[d.texture for d in draws])
                    renderer.scene.gauge=50;half=renderer.scene.draws(60)
                    gauge=next(d for d in half if d.texture=='HP_GAUGE')
                    full=next(d for d in draws if d.texture=='HP_GAUGE')
                    self.assertAlmostEqual(gauge.matrix[0]/full.matrix[0],.5)
                    frame=next(d for d in half if d.texture=='HP_WAKU')
                    self.assertAlmostEqual(frame.matrix[0]*292,304*.98)
                    self.assertAlmostEqual(frame.matrix[4],168+304*.01)
                else:
                    self.assertFalse(any(d.layer=='root/host-hp' for d in draws))
                    self.assertFalse(profile(p)['miss'])
                from iidxviewer.layers import LayerControls
                controls=LayerControls(p,renderer.animation);controls.load();renderer.scene.controls=controls
                if relative.startswith('16_'):
                    controls.set_visible('root/host-hp',False)
                    self.assertFalse(any(d.layer=='root/host-hp' for d in renderer.scene.draws(60)))
                    controls.set_visible('root/host-hp',True)
                else:self.assertNotIn('root/host-hp',controls.rows)
                image=renderer.render(60);self.assertFalse(image.isNull());tested+=1
            finally:renderer.close();p.close()
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).digest())
        if not tested:self.skipTest('No ICARUS fixture')

    def test_named_host_pg_counter_pauses_during_intro_and_attack(self):
        p,a=fixture(timeline([[place(2)],[],[],[]]))
        p.animations=[Animation(name,a.movie,a.timeline) for name in ['in','idle','attack','miss','clear','failed']]
        notes=[0]*30+[10/60]*21+[11/60]*40+[20/60]*20+[22/60]
        r=GameLayerRenderer(p,{},notes,'cpu',end_time=2)
        try:
            self.assertEqual([start for start,stop,index in r.cues if p.animations[index].name=='attack'],[10,22])
            self.assertEqual(p.animations[r.cue_at(0)[1]].name,'in')
            self.assertEqual(p.animations[r.cue_at(120)[1]].name,'clear')
        finally:r.close()

    def test_real_rche_and_related_hosts_use_two_outcomes(self):
        tested=0
        for mid in [19016,22065,22003,19002]:
            path=ROOT/'33_Sparkle_Shower/data/graphic'/f'{mid}.ifs'
            if not path.exists():continue
            p=open_package(path);host=profile(p)
            try:
                self.assertIsNotNone(host);self.assertEqual(host['hit_every'],21)
                self.assertFalse(host['gauge'])
                self.assertEqual([x['name'] for x in state_choices(p)],['Clear','Failed'])
                results=[]
                from iidxviewer.song_layers import root_frames
                intro=p.animations[host['intro'][0]];note_time=root_frames(intro)/intro.movie.fps+1;end=note_time+2
                for state in ['clear','failed']:
                    r=GameLayerRenderer(p,{},[note_time]*21,'cpu',state=state,end_time=end)
                    try:
                        self.assertEqual(r.cue_at(round(end*60))[1],host[state][0]);results.append(r.cues)
                    finally:r.close()
                self.assertNotEqual(*results)
                self.assertTrue(any(index in host['miss'] for _,_,index in results[1]))
                self.assertFalse(any(index in host['hit'] for _,_,index in results[1]))
                tested+=1
            finally:p.close()
        if not tested:self.skipTest('No named BOSS fixture')

    def test_real_rche_endings_start_at_final_note_release(self):
        from iidxviewer.song import source,charts,judgement_timing
        tested=0
        for mid,expected in [(19016,125.838),(22065,101.838)]:
            path=ROOT/'33_Sparkle_Shower/data/sound'/f'{mid}.ifs'
            layer=ROOT/'33_Sparkle_Shower/data/graphic'/f'{mid}.ifs'
            if not path.exists() or not layer.exists():continue
            audio=open_package(path);p=open_package(layer)
            try:
                _,_,data=source(audio,path)
                for chart in charts(data):
                    notes,measures,end=judgement_timing(data,chart['id'],path)
                    self.assertAlmostEqual(end,expected,places=3)
                    for state in ['clear','failed']:
                        r=GameLayerRenderer(p,{},notes,'cpu',measures,state,end_time=end)
                        try:
                            self.assertNotEqual(r.cue_at(r.finish-1)[1],profile(p)[state][0])
                            self.assertEqual(r.cue_at(r.finish),(-1,profile(p)[state][0],0))
                        finally:r.close()
                tested+=1
            finally:audio.close();p.close()
        if not tested:self.skipTest('No Rche song fixtures')

    def test_legacy_nested_rotation_affects_the_child_position(self):
        from iidxviewer.legacy_idx import IndexData
        base=dict(type=0,id=0,flags=0,duration=0,begin=0,end=10,base=0,anchor=(0,0),position=[],scale=[],alpha=[],rotation=[])
        parent=dict(base,type=2,id=2,rotation=[(0,16384,0)])
        child=dict(base,position=[(0,10,0)])
        index=IndexData(cells=[(0,0,8,8)],records=[parent,dict(base,type=-1),child,dict(base,type=-1)])
        _,(_,matrix,_,_)=next(index.commands(0,0))
        self.assertAlmostEqual(matrix[4],0);self.assertAlmostEqual(matrix[5],10)

    def test_legacy_subtractive_matte_is_drawn_before_its_colour(self):
        from PIL import Image
        from iidxviewer.legacy_idx import IndexData,LegacyAnimation
        from iidxviewer.render import SceneRenderer
        base=dict(type=0,id=0,flags=2,duration=0,begin=0,end=10,base=0,anchor=(0,0),position=[],scale=[],alpha=[(0,100,100)],rotation=[])
        index=IndexData(cells=[(0,0,8,8),(0,0,8,8)],records=[base,dict(base,id=1,flags=18,alpha=[(0,100,0)]),dict(base,type=-1)])
        animation=LegacyAnimation('main',index,0,['colour','matte'])
        p=SimpleNamespace(path=Path('/tmp/1523/osprey.bin'),animations=[animation],assets=[],atlas_regions={},texture=lambda name:Image.new('RGBA',(8,8),(255,0,0,255) if name=='colour' else (255,255,255,255)))
        self.assertEqual([entry[1][0] for entry in animation.draw_commands(0)],['matte','colour'])
        r=SceneRenderer(p,animation)
        try:self.assertEqual(r.render(0).pixelColor(3,3).getRgb(),(255,0,0,255))
        finally:r.close()

    def test_shared_audio_preview_survives_worker_close_and_invalidates_source_changes(self):
        from iidxviewer.song import export_song
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);sound=root/'20_demo/data/sound/20032/20032.2dx';sound.parent.mkdir(parents=True)
            sound.write_bytes(two_tracks(pcm(1000,4410)));sound.with_suffix('.1').write_bytes(chart_file([(0,4,1,120),(0,7,0,1),(500,6,0,0)]))
            args=dict(chart=1,bank='package')
            with patch('iidxviewer.preview_cache.ROOT',root/'shared'),patch('iidxviewer.song.export_song',wraps=export_song) as render:
                for index in range(2):
                    cache=root/f'worker-{index}';cache.mkdir();service=ResourceService(cache)
                    try:service.open(sound);reply=service.prepare_song(args);self.assertGreater(Path(reply['path']).stat().st_size,100)
                    finally:service.close()
                self.assertEqual(render.call_count,1)
                sound.write_bytes(two_tracks(pcm(2000,4410)))
                cache=root/'worker-3';cache.mkdir();service=ResourceService(cache)
                try:service.open(sound);service.prepare_song(args)
                finally:service.close()
                self.assertEqual(render.call_count,2)

    def test_preview_draws_native_size_and_layer_uses_the_bga_viewport(self):
        class Drawing:
            animation=SimpleNamespace(length=30,movie=SimpleNamespace(fps=60,rect=(0,20,0,40)))
            sizes=[]
            def render(self,frame,size):
                self.sizes.append(size);image=QImage(*size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(0)
                painter=QPainter(image);painter.fillRect(size[0]//4,size[1]//2,max(2,size[0]//6),max(2,size[1]//6),QColor('red'));painter.end();return image
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);audio=root/'audio.wav';audio.write_bytes(pcm(1000,11025));bga=root/'bga.mp4'
            ffmpeg('-f','lavfi','-i','color=c=blue:s=64x48:r=60:d=0.3','-c:v','libx264',bga)
            for preview in [False,True]:
                drawing=Drawing();drawing.sizes=[];target=root/f'{preview}.mp4'
                with patch('iidxviewer.media_export.layer_renderer',return_value=nullcontext(drawing)),patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
                    song_movie(audio,target,root,dict(bga_path=str(bga),layer_path=str(root/'layer.ifs'),layer_animation=-2,resolution='480p',preview=preview))
                self.assertEqual(set(drawing.sizes),{(20,40) if preview else (64,48)})
                data=ffmpeg('-i',target,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1');image=np.frombuffer(data,np.uint8).reshape(48,64,3)
                self.assertGreater(int(image[26,18,0]),110);self.assertGreater(int(image[26,40,2]),180)

    def test_real_early_generic_bga_and_military_markers(self):
        for version in ['12_HAPPY_SKY','14_GOLD','16_EMPRESS','18_Resort_Anthem']:
            path=ROOT/version/'data/sd_data/0200/02001.2dx'
            if not path.exists():continue
            result=visual_info(path);self.assertTrue(result['bga_path'].endswith('02ho.4'));self.assertEqual(metadata(path)['afp'][1],'01')
        path=ROOT/'16_EMPRESS/data/graph/sys/1501/osprey.bin'
        if not path.exists():self.skipTest('No legacy Military Stage fixture')
        before=hashlib.sha256(path.read_bytes()).hexdigest();p=open_package(path);r=GameLayerRenderer(p,metadata(path),[0,4,8],'cpu')
        try:
            image=r.render(110,(320,240));self.assertTrue(any(bytes(image.constBits())))
            self.assertEqual(p.animations[r.cue_at(0)[1]].name,'M10')
        finally:r.close();p.close()
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
        path=ROOT/'33_Sparkle_Shower/data/graphic/26007.ifs'
        if not path.exists():path=ROOT/'27_BossTest/data/graphic/26007.ifs'
        if path.exists():
            from iidxviewer.scene import Scene
            p=open_package(path);s=Scene(p,next(a for a in p.animations if a.name=='enemy_hp'));s.gauge=50
            try:
                names=[d.texture for d in s.draws(120)]
                self.assertEqual(names.count('hp_num5'),1);self.assertEqual(names.count('hp_num1'),1)
            finally:s.close();p.close()
