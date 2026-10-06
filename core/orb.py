"""
core/orb.py — the modern HUD centrepiece: a living, audio-reactive orb loop.

Replaces the robot head as the default. Same contract as core.avatar.HoloAvatar
(SPAN, step(), glance(), paint()), so ui.py drives it exactly the same way and
the theme hue wheel still recolours it.

What you see
    idle       a soft core that breathes, ringed by slow morphing loops
    listening  green ripples travelling outward from the core
    thinking   bright arcs orbiting the orb
    speaking   the loops deform and a radial spectrum ring pulses with the voice
    muted      dimmed and still
    sleeping   very dim, very slow

Software rendered with QPainter only — no OpenGL, no new dependency.
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QPainter, QPen, QPolygonF,
                         QRadialGradient)

_N_PTS = 120          # points per morphing loop (integer harmonics => closed)
_N_BARS = 72          # bars in the spectrum ring
_HARM = (2, 3, 5)     # harmonics that shape the loops


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlpha(int(max(0.0, min(1.0, a)) * 255))
    return out


class ModernOrb:
    # The orb is a circle: its height is exactly 2 radii. ui.py sizes the radius
    # as band_height / (SPAN + 0.08) for the head, so SPAN = 2 keeps it in the band.
    SPAN = 2.0

    def __init__(self) -> None:
        self._t = 0.0            # free-running time, seconds
        self._phase = 0.0        # loop morph phase (speeds up with energy)
        self._spin = 0.0         # orbiting-arc angle, radians
        self._energy = 0.0       # smoothed 0..1 loudness
        self._listen = 0.0       # blend 0..1 per state
        self._think = 0.0
        self._sleep = 0.0
        self._mute = 0.0
        self._speak = 0.0
        self._bars = [0.0] * _N_BARS
        self._gaze = [0.0, 0.0]
        self._gaze_tgt = [0.0, 0.0]
        self._gaze_hold = 0.0

        # Precomputed sin/cos tables so a loop costs a few multiplies per point.
        self._cos = [math.cos(2 * math.pi * i / _N_PTS) for i in range(_N_PTS)]
        self._sin = [math.sin(2 * math.pi * i / _N_PTS) for i in range(_N_PTS)]
        self._hs = {m: [math.sin(m * 2 * math.pi * i / _N_PTS) for i in range(_N_PTS)] for m in _HARM}
        self._hc = {m: [math.cos(m * 2 * math.pi * i / _N_PTS) for i in range(_N_PTS)] for m in _HARM}
        self._bar_cos = [math.cos(2 * math.pi * i / _N_BARS - math.pi / 2) for i in range(_N_BARS)]
        self._bar_sin = [math.sin(2 * math.pi * i / _N_BARS - math.pi / 2) for i in range(_N_BARS)]

    # ── animation ────────────────────────────────────────────────────────────

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Nudge the orb toward something that just appeared on screen."""
        self._gaze_tgt = [max(-1.0, min(1.0, float(dx))), max(-1.0, min(1.0, float(dy)))]
        self._gaze_hold = max(0.0, float(hold))

    def step(self, dt: float, amp: float, speaking: bool = False,
             muted: bool = False, state: str = "",
             v_open=None, v_wide: float = 0.0, v_level=None,
             v_seq=None, v_hop: float = 0.02) -> None:
        dt = max(0.0, min(0.1, float(dt or 0.0)))
        self._t += dt

        # Loudness: the speech schedule's true level while speaking, the mic
        # level otherwise.
        level = float(v_level) if (speaking and v_level is not None) else float(amp or 0.0)
        if speaking and v_open is not None:
            level = 0.65 * level + 0.35 * float(v_open)
        level = max(0.0, min(1.0, level))
        if muted:
            level = 0.0
        tau = 0.045 if level > self._energy else 0.20      # fast attack, slow release
        self._energy += (level - self._energy) * (1.0 - math.exp(-dt / tau))

        # State blends — eased so a state change is a glide, never a cut.
        st = (state or "").upper()
        want = {
            "listen": 1.0 if (st == "LISTENING" and not muted and not speaking) else 0.0,
            "think":  1.0 if (st in ("THINKING", "PROCESSING", "INITIALISING") and not muted) else 0.0,
            "sleep":  1.0 if st == "SLEEPING" else 0.0,
            "mute":   1.0 if muted else 0.0,
            "speak":  1.0 if (speaking and not muted) else 0.0,
        }
        k = 1.0 - math.exp(-dt / 0.22)
        self._listen += (want["listen"] - self._listen) * k
        self._think += (want["think"] - self._think) * k
        self._sleep += (want["sleep"] - self._sleep) * k
        self._mute += (want["mute"] - self._mute) * k
        self._speak += (want["speak"] - self._speak) * k

        # Motion speeds: calm when idle, quicker with energy and thinking.
        calm = 1.0 - 0.75 * max(self._sleep, self._mute)
        self._phase += dt * calm * (0.55 + 1.6 * self._energy + 0.6 * self._think)
        self._spin += dt * (0.5 + 3.2 * self._think) * calm

        # Spectrum bars: each bar follows the level with its own moving shape.
        kb = 1.0 - math.exp(-dt / 0.06)
        ph = self._t * 2.4
        for i in range(_N_BARS):
            shape = 0.5 + 0.5 * math.sin(ph + i * 0.55) * math.sin(ph * 0.63 - i * 0.21 + 1.3)
            target = self._energy * (0.30 + 0.70 * shape)
            self._bars[i] += (target - self._bars[i]) * kb

        # Gaze: ease toward the target, hold, then drift back to centre.
        if self._gaze_hold > 0.0:
            self._gaze_hold = max(0.0, self._gaze_hold - dt)
        else:
            self._gaze_tgt = [0.0, 0.0]
        kg = 1.0 - math.exp(-dt / 0.25)
        self._gaze[0] += (self._gaze_tgt[0] - self._gaze[0]) * kg
        self._gaze[1] += (self._gaze_tgt[1] - self._gaze[1]) * kg

    # ── drawing ──────────────────────────────────────────────────────────────

    def _loop_points(self, cx: float, cy: float, base: float, amp: float,
                     phi: float, w: tuple) -> QPolygonF:
        c1, s1 = math.cos(phi), math.sin(phi)
        c2, s2 = math.cos(-phi * 1.37 + 1.1), math.sin(-phi * 1.37 + 1.1)
        c3, s3 = math.cos(phi * 0.71 + 2.3), math.sin(phi * 0.71 + 2.3)
        h2s, h2c = self._hs[2], self._hc[2]
        h3s, h3c = self._hs[3], self._hc[3]
        h5s, h5c = self._hs[5], self._hc[5]
        pts = []
        for i in range(_N_PTS):
            wob = (w[0] * (h3s[i] * c1 + h3c[i] * s1)
                   + w[1] * (h5s[i] * c2 + h5c[i] * s2)
                   + w[2] * (h2s[i] * c3 + h2c[i] * s3))
            rad = base + amp * wob
            pts.append(QPointF(cx + rad * self._cos[i], cy + rad * self._sin[i]))
        return QPolygonF(pts)

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        if r < 8:
            return
        dim = 1.0 - 0.62 * self._sleep - 0.45 * self._mute       # overall brightness
        e = self._energy
        breath = 0.5 + 0.5 * math.sin(self._t * (0.9 - 0.5 * self._sleep))

        # Gaze parallax: the core leans toward what it is looking at, the outer
        # layers barely move, which reads as depth.
        gx, gy = self._gaze
        drift_x = math.sin(self._t * 0.33) * 0.02
        drift_y = math.cos(self._t * 0.27) * 0.02
        ox, oy = (gx * 0.07 + drift_x) * r, (gy * 0.07 + drift_y) * r
        core_x, core_y = cx + ox, cy + oy

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. Halo — a wide, soft wash of the accent colour behind everything.
        halo_r = r * (1.02 + 0.06 * e)
        g = QRadialGradient(QPointF(core_x, core_y), halo_r)
        g.setColorAt(0.0, _alpha(accent, (0.20 + 0.30 * e) * dim))
        g.setColorAt(0.55, _alpha(primary, (0.07 + 0.10 * e) * dim))
        g.setColorAt(1.0, _alpha(primary, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(core_x, core_y), halo_r, halo_r)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Everything below glows additively so overlaps brighten instead of muddy.
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        # 2. Listening ripples — two rings travelling outward, always out of step.
        if self._listen > 0.02:
            for j in range(2):
                t = (self._t * 0.55 + j * 0.5) % 1.0
                rr = r * (0.50 + 0.46 * t)
                pen = QPen(_alpha(accent, (1.0 - t) * 0.55 * self._listen * dim), 1.6)
                p.setPen(pen)
                p.drawEllipse(QPointF(core_x, core_y), rr, rr)

        # 3. Thinking arcs — bright segments orbiting the orb.
        if self._think > 0.02:
            rect = QRectF(cx - r * 0.93, cy - r * 0.93, r * 1.86, r * 1.86)
            for j in range(3):
                start = math.degrees(self._spin * (1.0 + 0.35 * j)) + j * 120.0
                pen = QPen(_alpha(_mix(primary, accent, j / 2.0), 0.85 * self._think * dim),
                           2.4 - 0.4 * j)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawArc(rect, int(-start * 16), int(52 * 16))

        # 4. Morphing loops — the "loop": three closed curves that breathe with
        # the voice. Idle they ripple gently; speaking they deform with the level.
        deform = (0.014 + 0.070 * e + 0.020 * self._think) * r
        for k in range(3):
            base = r * (0.63 + 0.075 * k) * (1.0 + 0.012 * breath)
            w = (1.0, 0.7 + 0.1 * k, 0.5)
            poly = self._loop_points(cx + ox * (1.0 - 0.3 * k), cy + oy * (1.0 - 0.3 * k),
                                     base, deform * (1.0 + 0.35 * k),
                                     self._phase * (1.0 + 0.25 * k) + k * 1.7, w)
            col = _mix(primary, accent, 0.25 + 0.35 * k)
            p.setPen(QPen(_alpha(col, (0.70 - 0.16 * k) * dim), 2.0 - 0.35 * k))
            p.drawPolygon(poly)

        # 5. Spectrum ring — bars radiating from just outside the loops.
        if e > 0.01 or self._speak > 0.02:
            r_in = r * 0.87
            lines = []
            for i in range(_N_BARS):
                ln = r * (0.012 + 0.135 * self._bars[i])
                c, s = self._bar_cos[i], self._bar_sin[i]
                lines.append(QLineF(cx + c * r_in, cy + s * r_in,
                                    cx + c * (r_in + ln), cy + s * (r_in + ln)))
            pen = QPen(_alpha(accent, (0.35 + 0.55 * min(1.0, e * 1.6)) * dim), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLines(lines)

        # 6. Core orb — the bright centre.
        core_r = r * (0.40 + 0.035 * breath + 0.11 * e)
        g = QRadialGradient(QPointF(core_x, core_y), core_r * 1.35)
        g.setColorAt(0.00, _alpha(_mix(accent, QColor(255, 255, 255), 0.65), 0.95 * dim))
        g.setColorAt(0.30, _alpha(accent, 0.85 * dim))
        g.setColorAt(0.70, _alpha(primary, 0.40 * dim))
        g.setColorAt(1.00, _alpha(primary, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(core_x, core_y), core_r * 1.35, core_r * 1.35)

        # Specular highlight, up and to the left, so the core reads as a sphere.
        hx, hy = core_x - core_r * 0.30, core_y - core_r * 0.34
        g = QRadialGradient(QPointF(hx, hy), core_r * 0.55)
        g.setColorAt(0.0, _alpha(QColor(255, 255, 255), 0.55 * dim))
        g.setColorAt(1.0, _alpha(QColor(255, 255, 255), 0.0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(hx, hy), core_r * 0.55, core_r * 0.55)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 7. Thin outer rim so the orb has an edge at rest.
        p.setPen(QPen(_alpha(primary, 0.22 * dim), 1.0))
        p.drawEllipse(QPointF(cx, cy), r * 0.99, r * 0.99)

        p.restore()
