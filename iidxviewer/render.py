"""QPainter-based 2D scene renderer shared by preview and export."""
from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPolygonF, QTransform


def qimage(image):
    image = image.convert("RGBA")
    # QImage borrows the Python buffer. Keep it alive until the deep copy ends;
    # an inline tobytes() temporary can be freed before .copy() allocates.
    pixels = image.tobytes()
    return QImage(pixels, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()


class SceneRenderer:
    @property
    def rect(self):return getattr(self.scene,'export_rect',getattr(self.scene,'rect',self.animation.movie.rect))
    def close(self):
        self.scene.close()
        self.images.clear(); self.image_bytes = 0
        self.package = self.animation = None

    def __init__(self, package, animation):
        self.package, self.animation = package, animation
        from .scene import Scene
        self.scene = Scene(package, animation)
        self.images = OrderedDict()
        self.image_bytes = 0
        self.issues = self.scene.issues

    def texture(self, name, mul=(1, 1, 1, 1), add=(0, 0, 0, 0)):
        # Quantize colors for stable keys during fades, and bound decoded copies.
        mul = tuple(round(v, 3) for v in mul)
        add = tuple(round(v, 3) for v in add)
        key = name, mul, add
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        image = self.package.texture(name)
        if image is None:
            self.issues.add(f"缺少纹理：{name}")
            return None
        if mul != (1, 1, 1, 1) or add != (0, 0, 0, 0):
            lut = [max(0, min(255, round(i * mul[ch] + add[ch] * 255))) for ch in range(4) for i in range(256)]
            image = image.point(lut)
        result = qimage(image)
        self.images[key] = result
        self.image_bytes += result.sizeInBytes()
        while self.image_bytes > 128 * 1024 * 1024 and len(self.images) > 1:
            _,old=self.images.popitem(last=False);self.image_bytes-=old.sizeInBytes()
        return result

    def render(self, frame, size=None):
        x0, x1, y0, y1 = self.rect
        width, height = max(1, x1 - x0), max(1, y1 - y0)
        if width * height > 32 * 1024 * 1024:
            raise ValueError("动画画布过大")
        out = QImage(*(size or (width, height)), QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        self.mask_cache={}
        self.current_size=out.size()
        self.current_rect=x0,x1,y0,y1
        painter = QPainter(out)
        try:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.scale(out.width() / width, out.height() / height)
            painter.translate(-x0, -y0)
            for draw in self.scene.draws(frame):
                self.draw_masked(painter, draw)
        finally:
            painter.end()
        return out

    def layer(self):
        image=QImage(self.current_size,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x0,x1,y0,y1=self.current_rect
        painter.scale(image.width()/max(1,x1-x0),image.height()/max(1,y1-y0));painter.translate(-x0,-y0)
        return image,painter

    def mask_image(self,chain):
        if chain in self.mask_cache:return self.mask_cache[chain]
        if len(chain)>32 or len(self.mask_cache)>64:raise ValueError('动画遮罩组合超出限制')
        image,painter=self.layer()
        try:
            for draw in self.scene.masks.get(chain[-1],[]):
                self.draw_masked(painter,replace(draw,masks=chain[:-1]+draw.masks,blend=0))
        finally:painter.end()
        self.mask_cache[chain]=image
        return image

    def draw_masked(self,painter,draw):
        if draw.blend in {5,6,8,9,10,11}:
            # Cubism's legacy blend factors retain destination alpha; Qt's
            # Multiply/Plus use a different alpha equation on transparent pixels.
            import numpy as np
            image,layer=self.layer()
            try:self.draw_masked(layer,replace(draw,blend=0))
            finally:layer.end()
            destination=painter.device()
            src=np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.width(),4).astype(np.uint32)
            dst=np.frombuffer(destination.constBits(),np.uint8).reshape(image.height(),image.width(),4).astype(np.uint32)
            if draw.blend==5:dst[:,:,:3]=(src[:,:,:3]*dst[:,:,:3]+dst[:,:,:3]*(255-src[:,:,3:4])+127)//255
            elif draw.blend==6:dst[:,:,:3]=np.minimum(255,src[:,:,:3]+dst[:,:,:3])
            else:
                coverage=src[:,:,:3].max(axis=2) if draw.blend in {8,9} else src[:,:,3]
                if draw.blend==8:dst[:,:,:3]=np.minimum(255,src[:,:,:3]+dst[:,:,:3])
                elif draw.blend==9:dst[:,:,:3]=np.maximum(0,dst[:,:,:3].astype(np.int32)-src[:,:,:3].astype(np.int32))
                elif draw.blend==10:dst[:,:,:3]=np.maximum(dst[:,:,:3],src[:,:,:3])
                else:dst[:,:,:3]=np.minimum(dst[:,:,:3],src[:,:,:3])
                dst[:,:,3]=np.minimum(255,dst[:,:,3]+coverage) if draw.blend==8 else np.maximum(dst[:,:,3],coverage)
            pixels=dst.astype(np.uint8).tobytes()
            result=QImage(pixels,image.width(),image.height(),image.width()*4,QImage.Format.Format_ARGB32_Premultiplied)
            painter.save()
            try:
                painter.resetTransform();painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source);painter.drawImage(0,0,result)
            finally:painter.restore()
            return
        if not draw.masks:
            self.draw_command(painter,draw);return
        image,layer=self.layer()
        try:
            self.draw_command(layer,replace(draw,masks=(),blend=0))
            layer.resetTransform();layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut if draw.inverted_mask else QPainter.CompositionMode.CompositionMode_DestinationIn)
            layer.drawImage(0,0,self.mask_image(draw.masks))
        finally:layer.end()
        painter.save()
        try:
            painter.resetTransform()
            mode={8:QPainter.CompositionMode.CompositionMode_Plus,3:QPainter.CompositionMode.CompositionMode_Multiply,
                  4:QPainter.CompositionMode.CompositionMode_Screen}.get(draw.blend,QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setCompositionMode(mode);painter.drawImage(0,0,image)
        finally:painter.restore()

    def draw_command(self, painter, draw):
        painter.save()
        try:
            painter.setTransform(QTransform(*draw.matrix), True)
            mode = {8:QPainter.CompositionMode.CompositionMode_Plus,
                    3:QPainter.CompositionMode.CompositionMode_Multiply,
                    4:QPainter.CompositionMode.CompositionMode_Screen}.get(draw.blend)
            if mode is not None:painter.setCompositionMode(mode)
            image = self.texture(draw.texture,draw.multiply,draw.add) if draw.texture else None
            color = QColor(*[max(0,min(255,round((a+b)*255))) for a,b in zip(draw.multiply,draw.add)])
            for index in range(0,len(draw.vertices)-2,3):
                vertices=draw.vertices[index:index+3]
                points=[QPointF(x,y) for x,y,u,v in vertices]
                if draw.texture:
                    if image is None:continue
                    uv=[QPointF(u*image.width(),v*image.height()) for x,y,u,v in vertices]
                    source=QPolygonF([uv[0],uv[1],uv[1]+uv[2]-uv[0],uv[2]])
                    dest=QPolygonF([points[0],points[1],points[1]+points[2]-points[0],points[2]])
                    transform=QTransform.quadToQuad(source,dest)
                    if transform is None:continue
                    painter.save()
                    clip=QPainterPath();clip.addPolygon(QPolygonF(points));painter.setClipPath(clip,Qt.ClipOperation.IntersectClip)
                    painter.setTransform(transform,True);painter.drawImage(0,0,image);painter.restore()
                else:
                    painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(color);painter.drawPolygon(QPolygonF(points))
        finally:painter.restore()
