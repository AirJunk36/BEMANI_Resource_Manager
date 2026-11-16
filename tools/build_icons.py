"""Generate the project default icon, or use privately supplied artwork."""
from pathlib import Path
import sys
from PIL import Image, ImageDraw

assets=Path(__file__).resolve().parents[1]/'native/assets'
assets.mkdir(parents=True,exist_ok=True)
if len(sys.argv)>1:
    with Image.open(sys.argv[1]) as source:
        artwork=source.convert('RGBA')
    side=max(artwork.size);image=Image.new('RGBA',(side,side))
    image.paste(artwork,((side-artwork.width)//2,(side-artwork.height)//2))
else:
    image=Image.new('RGBA',(1024,1024));draw=ImageDraw.Draw(image)
    draw.rounded_rectangle((32,32,992,992),radius=220,fill='#2463eb')
    for y,color in [(650,'#8db2ff'),(490,'#bed3ff'),(330,'#ffffff')]:
        draw.polygon([(220,y),(512,y-145),(804,y),(512,y+145)],fill=color)
image.save(assets/'Application.ico',sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
image.save(assets/'Application.icns')
print('Generated Application.ico and Application.icns')
