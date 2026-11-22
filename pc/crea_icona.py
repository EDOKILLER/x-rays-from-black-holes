#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Icona di «Raggi X dai buchi neri» (© 2026 Edoardo Battistel):
buco nero con disco di accrescimento dorato inclinato (con l'arco della lente gravitazionale
sopra), due getti azzurri-viola, una «X» dorata, fondo blu notte con stelle."""
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

N = 512


def icona(percorso, lato=256):
    y, x = np.mgrid[0:N, 0:N].astype(float)
    cx, cy = N / 2, N * 0.50
    img = np.zeros((N, N, 4))
    # fondo: blu notte con un velo violaceo al centro
    r = np.hypot(x - cx, y - cy) / (N / 2)
    img[..., 0] = 0.03 + 0.10 * np.exp(-r ** 2 * 2.5)
    img[..., 1] = 0.05 + 0.05 * np.exp(-r ** 2 * 2.5)
    img[..., 2] = 0.14 + 0.16 * np.exp(-r ** 2 * 2.0)
    img[..., 3] = 1
    rng = np.random.default_rng(11)
    for _ in range(70):                                   # stelle
        sx, sy, sl = rng.uniform(0, N), rng.uniform(0, N), rng.uniform(0.3, 1)
        d2 = (x - sx) ** 2 + (y - sy) ** 2
        img[..., :3] += (sl * np.exp(-d2 / 2.2))[..., None] * np.array([0.9, 0.92, 1.0])
    # getti X (asse leggermente inclinato)
    ang = np.radians(-14)
    u = (x - cx) * np.cos(ang) + (y - cy) * np.sin(ang)       # perpendicolare al getto
    v = -(x - cx) * np.sin(ang) + (y - cy) * np.cos(ang)      # lungo il getto
    larghezza = 7 + 0.10 * np.abs(v)
    getto = np.exp(-(u / larghezza) ** 2) * np.clip(1 - np.abs(v) / (N * 0.47), 0, 1) ** 0.8 * (np.abs(v) > 40)
    img[..., 0] += getto * 0.45
    img[..., 1] += getto * 0.75
    img[..., 2] += getto * 1.25
    # disco di accrescimento: anello ellittico inclinato, più chiaro dal lato che viene verso di noi
    a_ang = np.radians(-14)
    xr = (x - cx) * np.cos(a_ang) + (y - cy) * np.sin(a_ang)
    yr = (-(x - cx) * np.sin(a_ang) + (y - cy) * np.cos(a_ang)) / 0.30
    rr = np.hypot(xr, yr)
    disco = np.exp(-((rr - 150) / 52) ** 2) * (rr > 72)
    doppler = 0.65 + 0.35 * np.tanh(-xr / 90)              # beaming: lato sinistro più luminoso
    colore_disco = np.array([1.0, 0.70, 0.22])
    davanti = yr > 0                                       # metà del disco davanti al buco
    # arco della lente gravitazionale sopra il buco nero (la parte posteriore del disco vista «piegata»)
    rl = np.hypot(x - cx, (y - cy) * 1.05)
    arco = np.exp(-((rl - 92) / 11) ** 2) * (y < cy + 8)
    img[..., :3] += (arco * 0.85)[..., None] * np.array([1.0, 0.78, 0.35])
    img[..., :3] += ((disco * doppler) * (~davanti))[..., None] * colore_disco
    # ombra del buco nero
    ombra = np.clip((rl - 70) / 6, 0, 1)
    img[..., :3] *= ombra[..., None]
    anello = np.exp(-((rl - 74) / 4.0) ** 2)               # anello di fotoni
    img[..., :3] += anello[..., None] * np.array([1.0, 0.88, 0.6])
    img[..., :3] += ((disco * doppler) * davanti)[..., None] * colore_disco * 1.1
    img = np.clip(img, 0, 1)
    im = Image.fromarray((img * 255).astype(np.uint8), "RGBA")
    im = im.filter(ImageFilter.GaussianBlur(0.6))
    d = ImageDraw.Draw(im)
    # «X» dorata in basso a destra
    font = None
    for nome in ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "LiberationSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(nome, 150)
            break
        except OSError:
            continue
    if font:
        d.text((N * 0.80, N * 0.80), "X", font=font, anchor="mm", fill=(242, 193, 78, 255),
               stroke_width=6, stroke_fill=(30, 20, 5, 255))
    # angoli arrotondati
    maschera = Image.new("L", (N, N), 0)
    ImageDraw.Draw(maschera).rounded_rectangle([4, 4, N - 5, N - 5], radius=90, fill=255)
    im.putalpha(maschera)
    im.resize((lato, lato), Image.LANCZOS).save(percorso)


if __name__ == "__main__":
    icona(sys.argv[1] if len(sys.argv) > 1 else "raggi_x.png", int(sys.argv[2]) if len(sys.argv) > 2 else 256)
