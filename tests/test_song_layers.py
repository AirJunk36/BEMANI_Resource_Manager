from unittest.mock import patch
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from PIL import Image
from PySide6.QtGui import QGuiApplication

from iidxviewer.afp import Animation
from iidxviewer.media_export import frames_for, layer_info, layer_renderer, song_movie
from iidxviewer.resources import Asset, Package
from iidxviewer.song import note_times, layer_timing
from iidxviewer.song_layers import AUTO, Choice, GameLayerRenderer, mapping, resolve, schedule
from tests.test_playback import fixture, timeline, place
from tests.test_song import chart_file


def layer_package():
    child = timeline([[place(2)],[(127,dict(flags=5,depth=1,position=(16,0)))],[],[]])
    package, wrapper = fixture(child)
    animation = Animation('01',wrapper.movie,child)
    package.animations = [Animation('00',wrapper.movie,child), animation]
    return package


def pixels(image):
    return bytes(image.constBits())


class SongLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_chart_drives_both_players_without_bgm_or_duplicate_chords(self):
        data = chart_file([(0,7,0,1),(100,0,0,0),(100,0,1,0),(200,1,107,0),(300,7,0,2)])
        self.assertEqual(note_times(data,1,'33_test/song.s3p'),[.1,.2])

    def test_final_note_release_precedes_chart_end_marker(self):
        from iidxviewer.song import judgement_timing
        data=chart_file([(100,0,0,500),(200,1,1,0),(3000,6,0,0)])
        notes,_,end=judgement_timing(data,1,'33_test/song.2dx')
        self.assertEqual(notes,[.1,.2,.6]);self.assertEqual(end,.6)

    def test_host_uses_root_duration_cooldown_and_reproducible_selection(self):
        package = layer_package()
        package.animations[1].movie.definitions[3] = ('sprite',timeline([[]]*300))
        package.animations[1].timeline.tags.append(place(3))
        package.animations[1].timeline.frames[0] = (0,2)
        self.assertEqual(package.animations[1].length,300)
        notes = [i/60 for i in range(600)]
        cues = schedule(package,[1],notes,3013)
        self.assertGreater(len(cues),3)
        self.assertEqual(cues,schedule(package,[1],notes,3013))
        self.assertEqual(cues[0][:2],(0,8))  # Four root frames at 30 FPS.
        for previous, current in zip(cues,cues[1:]):
            self.assertGreaterEqual(current[0]-previous[1],60)
            self.assertLess(current[0]-previous[1],180)

    def test_follow_mode_animates_restarts_seeks_and_returns_transparency(self):
        package = layer_package()
        renderer = GameLayerRenderer(package,dict(afp_flag=2,afp=['00','01'],song_id=16020),[0,4,8],'cpu')
        try:
            images = [renderer.render(i,(64,64)) for i in [0,2,60,240,0]]
            pixels = [bytes(i.constBits()) for i in images]
            self.assertNotEqual(pixels[0],pixels[1])
            self.assertFalse(any(pixels[2]))
            self.assertEqual(pixels[0],pixels[3])
            self.assertEqual(pixels[0],pixels[4])
            self.assertTrue(all(cue[2] == 1 for cue in renderer.cues))
            self.assertEqual(renderer.frame_key(60),renderer.frame_key(61))
        finally: renderer.close()

    def test_sequence_keeps_running_while_transient_clips_are_idle(self):
        package = layer_package()
        package.animations[0].name = 'seq'
        renderer = GameLayerRenderer(package,dict(afp_flag=2,afp=['','seq','01']),[0,4],'cpu')
        try:
            self.assertTrue(any(pixels(renderer.render(60,(64,64)))))
            self.assertNotEqual(renderer.frame_key(60),renderer.frame_key(62))
        finally: renderer.close()

    def test_automatic_choice_available_with_missing_partial_and_conditional_metadata(self):
        package = layer_package()
        package.close = lambda: None
        records = [None,{},dict(afp_flag=0),dict(afp_flag=2,afp=['missing','01']),dict(afp_flag=2,afp=['00','01_24_44'])]
        for record in records:
            with self.subTest(record=record),patch('iidxviewer.media_export.open_package',return_value=package):
                result = layer_info('16020.ifs','01')
                self.assertEqual(result['selected'],AUTO)
                self.assertEqual(result['animations'][0]['name'],'跟随谱面')
                self.assertEqual([a['id'] for a in result['animations']],[AUTO,0,1])
                self.assertIsNotNone(mapping(package,record))
        with self.assertRaisesRegex(ValueError,'配套谱面'):
            GameLayerRenderer(package,records[-1],[],'cpu')

    def test_attribute_ranges_use_chart_measures_and_preserve_exact_names(self):
        names = {'01':1,'deep_01':2,'01_24_44':3}
        self.assertEqual(resolve('01_24_44',names),Choice(3))
        self.assertEqual(resolve('deep_01_24_44',names),Choice(2,((24,44),)))
        self.assertEqual(resolve('01_0_58_84_fine',names),Choice(1,((0,58),(84,999))))
        self.assertIsNone(resolve('missing_2_4',names))
        package = layer_package()
        cues = schedule(package,[Choice(1,((2,2),)),Choice(0,((4,999),))],[.5,1.1,4.1],0,[0,1,2,3,4])
        self.assertEqual([(start,index) for start,stop,index in cues],[(66,1),(246,0)])
        data = chart_file([(0,12,0,0),(0,12,1,0),(100,0,0,0),(1000,12,0,0),(1000,12,1,0),(1100,1,0,0)])
        self.assertEqual(layer_timing(data,1,'33_test/song.2dx'),([.1,1.1],[0,1]))

    def test_missing_config_uses_numbered_clips_or_continuous_main(self):
        package = layer_package()
        self.assertEqual(mapping(package,None),(None,[Choice(1)]))
        package.animations[0].name = 'arm'
        package.animations[1].name = 'main'
        self.assertEqual(mapping(package,None),(1,[]))
        renderer = GameLayerRenderer(package,None,[],'cpu')
        try:
            self.assertTrue(any(pixels(renderer.render(120,(64,64)))))
        finally: renderer.close()

    def test_legacy_main_without_afp_timeline_loops_in_follow_mode(self):
        package = layer_package()
        class Legacy:
            legacy = True
            name = 'main'
            length = 4
            movie = SimpleNamespace(fps=60,rect=(0,64,0,64),warnings=set())
            def draw_layers(self,frame,hidden):
                yield 'pixel',('pixel',frame*4,0,8,8,(0,0),0,1,'normal')
        package.animations = [Legacy()]
        renderer = GameLayerRenderer(package,None,[],'cpu')
        try:
            first = pixels(renderer.render(0,(64,64)))
            self.assertNotEqual(first,pixels(renderer.render(1,(64,64))))
            self.assertEqual(first,pixels(renderer.render(4,(64,64))))
        finally: renderer.close()

    def test_still_and_packaged_video_offer_follow_and_export(self):
        from tests.test_media_export import ffmpeg, pixel
        from tests.test_song import pcm
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder);audio = root/'song.wav';audio.write_bytes(pcm(1000,44100//2))
            png = BytesIO();Image.new('RGBA',(64,48),(255,0,0,255)).save(png,'PNG')
            video = root/'clip.avi';ffmpeg('-f','lavfi','-i','color=c=red:s=64x48:r=60:d=0.1','-c:v','mpeg4',video)
            for kind,payload in [('图片',png.getvalue()),('视频',video.read_bytes())]:
                def package(_):
                    return Package(root/'layer.ifs',assets=[Asset('meta','文件',payload=b'info'),Asset('layer',kind,payload=payload)])
                with self.subTest(kind=kind),patch('iidxviewer.media_export.open_package',side_effect=package),patch.dict('iidxviewer.media_export.SIZES',{'480p':(64,48)}):
                    self.assertEqual(layer_info('layer.ifs')['selected'],AUTO)
                    target=root/(kind+'.mp4')
                    song_movie(audio,target,root,dict(layer_path='layer.ifs',layer_animation=AUTO,resolution='480p',renderer='cpu'))
                    self.assertGreater(pixel(target,.4)[0],180)

    def test_export_skips_repeated_idle_and_half_rate_frames(self):
        package = layer_package()
        renderer = GameLayerRenderer(package,dict(afp_flag=2,afp=['00','01']),[0],'cpu')
        try:
            with patch.object(renderer,'render',wraps=renderer.render) as drawing:
                frames = list(frames_for(renderer,1,(64,64),60))
                self.assertEqual(len(frames),60)
                self.assertEqual(drawing.call_count,5)  # Four source frames + transparency.
        finally: renderer.close()

    def test_chart_end_interrupts_long_sequence_and_fixed_clip_with_outro(self):
        package=layer_package();movie=package.animations[0].movie
        package.animations=[Animation('seq',movie,timeline([[place(2)]]+[[]]*299)),Animation('out',movie,timeline([[place(2)],[]]))]
        for fixed in [None,0]:
            renderer=GameLayerRenderer(package,{},[0],'cpu',end_time=.5,fixed=fixed,loop=True)
            try:
                self.assertEqual(renderer.frame_key(29)[0],29*30//60 if fixed is None else None)
                self.assertEqual(renderer.cue_at(30),(-1,1,0))
                self.assertEqual(renderer.cue_at(300),(-1,1,1))
                self.assertIsNone(renderer.frame_key(30)[0])
                self.assertAlmostEqual(renderer.outcome_end,.5+2/30)
                self.assertTrue(any(pixels(renderer.render(30,(64,64)))))
            finally:renderer.close()

    def test_chart_end_hides_layer_without_outro_and_seeking_restores_it(self):
        package=layer_package();package.animations[0].name='seq'
        renderer=GameLayerRenderer(package,{},[0,4],'cpu',end_time=.5)
        try:
            before=pixels(renderer.render(29,(64,64)))
            self.assertTrue(any(before));self.assertFalse(any(pixels(renderer.render(30,(64,64)))))
            self.assertEqual(before,pixels(renderer.render(29,(64,64))))
        finally:renderer.close()
