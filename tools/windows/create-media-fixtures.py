"""Create short, generated media only inside the Windows test data copy."""
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0,str(Path.cwd()))
from tests.test_media_export import db_fixture, ffmpeg
from tests.test_song import pcm, chart_file
from tests.test_replacements import two_tracks
from tests.test_s3p_song import s3p

fixture_root=Path(sys.argv[1])
game_root=Path(__file__).resolve().parents[3]
# Copy only the real files exercised by host regressions, into the test copy.
for version in ['12_HAPPY_SKY','14_GOLD','16_EMPRESS','18_Resort_Anthem']:
    for relative in ['data/info/eout.bin','data/sd_data/0200/02001.2dx','data/movie/02ho.4']:
        source=game_root/version/relative
        if source.is_file():
            target=fixture_root/version/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
for ident in ['1501','1523']:
    source=game_root/f'16_EMPRESS/data/graph/sys/{ident}'
    if source.is_dir():shutil.copytree(source,fixture_root/f'16_EMPRESS/data/graph/sys/{ident}',dirs_exist_ok=True)
with tempfile.TemporaryDirectory(prefix='bemani-media-fixture-') as folder:
    root=Path(folder)/'data'; db_fixture(root)
    sound=root/'sound/20032/20032.2dx'; sound.parent.mkdir(parents=True)
    sound.write_bytes(two_tracks(pcm(1000,44100)))
    sound.with_suffix('.1').write_bytes(chart_file([(0,4,1,120),(0,7,0,1),(1000,6,0,0)]))
    movie=root/'movie/19po.mp4'; movie.parent.mkdir()
    ffmpeg('-f','lavfi','-i','color=c=blue:s=64x48:r=60:d=1','-c:v','libx264',movie)
    layer=root/'graphic/20032.ifs'; layer.parent.mkdir()
    shutil.copy2(fixture_root/'20_tricoro/data/graphic/test_effect_anime.ifs',layer)
    target=fixture_root/'20_MediaTest/data'
    shutil.copytree(root,target,dirs_exist_ok=True)
    full=Path(folder)/'s3p/data'; db_fixture(full,34)
    bank=full/'sound/20032.s3p'; bank.parent.mkdir()
    bank.write_bytes(s3p(pcm(1000,44100),pcm(2000,4410)))
    bank.with_suffix('.1').write_bytes(chart_file([(0,4,1,120),(0,2,0,2),(0,7,0,1),(1500,0,0,0),(2000,6,0,0)]))
    bga=full/'movie/19po.mp4';bga.parent.mkdir();shutil.copy2(movie,bga)
    (full/'graphic').mkdir();shutil.copy2(layer,full/'graphic/20032.ifs')
    shutil.copytree(full,fixture_root/'34_S3PTest/data',dirs_exist_ok=True)
    boss=game_root/'33_Sparkle_Shower/data/graphic/26007.ifs'
    if boss.is_file():
        target=fixture_root/'27_BossTest/data';(target/'graphic').mkdir(parents=True,exist_ok=True);(target/'sound').mkdir(exist_ok=True)
        shutil.copy2(boss,target/'graphic/26007.ifs')
        shutil.copy2(bank,target/'sound/26007.s3p');shutil.copy2(bank.with_suffix('.1'),target/'sound/26007.1')
print('Generated one-second media fixtures in Windows test data')
