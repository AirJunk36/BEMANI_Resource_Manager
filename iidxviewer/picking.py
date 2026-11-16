"""On-demand picking against the same ordered geometry, alpha and masks as preview."""
import math


def pick(scene, frame, u, v):
    if not math.isfinite(u) or not math.isfinite(v) or not (0 <= u < 1 and 0 <= v < 1):
        return None, None
    x0, x1, y0, y1 = getattr(scene,'rect',scene.animation.movie.rect)
    point = x0 + u * (x1 - x0), y0 + v * (y1 - y0)
    draws = scene.draws(frame)
    mask_cache = {}

    def mask_alpha(chain):
        if not chain:
            return 1.
        if chain not in mask_cache:
            alpha = 0.
            for draw in scene.masks.get(chain[-1], []):
                alpha = 1 - (1 - alpha) * (1 - draw_alpha(draw))
            mask_cache[chain] = alpha * mask_alpha(chain[:-1])
        return mask_cache[chain]

    def draw_alpha(draw):
        a, b, c, d, tx, ty = draw.matrix
        determinant = a*d - b*c
        if abs(determinant) < 1e-12:
            return 0.
        px, py = point[0] - tx, point[1] - ty
        px, py = (d*px - c*py)/determinant, (a*py - b*px)/determinant
        image = scene.texture(draw.texture) if draw.texture else None
        if draw.texture and image is None:
            return 0.
        opacity = 0.
        for i in range(0, len(draw.vertices)-2, 3):
            p, q, r = draw.vertices[i:i+3]
            ax, ay, bx, by = q[0]-p[0], q[1]-p[1], r[0]-p[0], r[1]-p[1]
            det = ax*by - ay*bx
            if abs(det) < 1e-12:
                continue
            s = ((px-p[0])*by - (py-p[1])*bx)/det
            t = (ax*(py-p[1]) - ay*(px-p[0]))/det
            if s < -1e-8 or t < -1e-8 or s+t > 1+1e-8:
                continue
            alpha = 1.
            if image is not None:
                tu, tv = (p[k] + s*(q[k]-p[k]) + t*(r[k]-p[k]) for k in (2,3))
                ix = min(image.width-1, max(0, tu*image.width-.5))
                iy = min(image.height-1, max(0, tv*image.height-.5))
                left, top = math.floor(ix), math.floor(iy)
                fx, fy = ix-left, iy-top
                alpha = sum(image.getpixel((min(image.width-1,left+dx), min(image.height-1,top+dy)))[3] * weight
                    for dx,dy,weight in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy))) / 255
            alpha = min(1., max(0., alpha*draw.multiply[3] + draw.add[3]))
            opacity = 1 - (1-opacity)*(1-alpha)
        mask=mask_alpha(draw.masks)
        return opacity * (1-mask if draw.masks and getattr(draw,'inverted_mask',False) else mask)

    chosen = next((draw.layer for draw in reversed(draws) if draw.layer and draw_alpha(draw) >= 1/255), None)
    if chosen is None:
        return None, None
    points = []
    for draw in draws:
        if draw.layer != chosen:
            continue
        a,b,c,d,tx,ty = draw.matrix
        points.extend((a*x+c*y+tx, b*x+d*y+ty) for x,y,_,_ in draw.vertices)
    left, right = max(x0, min(p[0] for p in points)), min(x1, max(p[0] for p in points))
    top, bottom = max(y0, min(p[1] for p in points)), min(y1, max(p[1] for p in points))
    return chosen, [float(value) for value in ((left-x0)/(x1-x0), (top-y0)/(y1-y0), max(0,right-left)/(x1-x0), max(0,bottom-top)/(y1-y0))]
