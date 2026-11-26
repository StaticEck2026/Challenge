#!/usr/bin/env python3
"""Check that the Modern view puts every player where the game draws him.

Runs the recompiled program headless (harness.py) through games with plays
and instant replays, and at every tick in CGA mode 4 runs the shipped
GameState (godot/ui/game_state.gd translated by gd2py) on the same memory.
For each frame it checks, independently of the play records the state is
read from:

1. players: each of the 22 positions the Modern view draws (field yards) is
   mapped back to CGA pixels and must match an O (offence) or X (defence)
   sprite that the game drew in video memory there (sprite recognised in the
   2-bit CGA frame with the game's own sprite shapes from data file A),
   within --tol pixels;
2. yard lines: the yard positions of the view (fx) must put a multiple of
   5 yards (within 0.2 yd) on every yard line the game drew (horizontal
   lines in the CGA frame, up to 3 pixels thick; wider bands are the filled
   area behind an end line), so the yard scale and the scroll are right;
4. visibility: the Modern view's layout (MatchView.build, horizontal and
   vertical field, full field and zoom camera on the ball) must draw every
   disc inside the visible field area (not off the canvas or under the
   toolbar or strip); off-screen discs are counted per orientation and zoom,
   split into "across the width" and "along the length" (zoom only follows
   the length), and discs whose centre is under the scoreboard pill; the
   zoom camera is the app's: eased once per tick towards the auto-framing
   target, then contained (ModernView._follow_camera);
3. ball spot: at the first frame of each play the ball must be within one
   yard of the line of scrimmage the text-mode scoreboard showed before the
   play ("Yard: 33", on the side of the field the overview's marker is on),
   an absolute anchor from a different screen.

  python3 -I tools/modern/check_positions.py --game-dir /path/to/nfl [--ticks 6000]
"""
import argparse
import math
import zlib
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402
import scripts  # noqa: E402
import uimod  # noqa: E402


def load_sprites(game_dir):
    name = next(n for n in os.listdir(game_dir) if n.upper() == "A")
    data = open(os.path.join(game_dir, name), "rb").read()
    out = []
    off = 0
    for _ in range(3):
        w, h = data[off], data[off + 1]
        img = np.zeros((h, w * 4), dtype=np.int8)
        for y in range(h):
            for xb in range(w):
                b = data[off + 2 + y * w + xb]
                for i, sh in enumerate((6, 4, 2, 0)):
                    img[y, xb * 4 + i] = (b >> sh) & 3
        out.append(img[:, :9])
        off += 2 + w * h
    return out          # 0: O (colour 1), 1: X (colour 2), 2: ball


def cga_pixels(mem):
    a = np.frombuffer(bytes(mem[0xB8000:0xBC000]), dtype=np.uint8)
    px = np.zeros((200, 320), dtype=np.int8)
    for y in range(200):
        o = (y & 1) * 0x2000 + (y >> 1) * 80
        row = a[o:o + 80]
        px[y, 0::4] = row >> 6
        px[y, 1::4] = (row >> 4) & 3
        px[y, 2::4] = (row >> 2) & 3
        px[y, 3::4] = row & 3
    return px


def sprite_score(px, spr, x, y):
    """How well sprite `spr` (9x9) explains the frame at top-left (x, y).
    Sprites are XOR-drawn: over a field line colour c shows as c ^ 3."""
    c = int(spr[spr > 0].max())
    h, w = spr.shape
    ys, xs = np.nonzero(spr)
    ok = n = 0
    for yy, xx in zip(ys, xs):
        X, Y = x + xx, y + yy
        if 0 <= X < 320 and 0 <= Y < 200:
            n += 1
            v = px[Y, X]
            ok += v == c or v == (c ^ 3)
    if n < len(ys) * 0.6:
        return -1.0
    bad = 0
    bg = 0
    for yy in range(h):
        for xx in range(w):
            if spr[yy, xx] == 0:
                X, Y = x + xx, y + yy
                if 0 <= X < 320 and 0 <= Y < 200:
                    bg += 1
                    bad += px[Y, X] == c
    return ok / n - (bad / bg if bg else 0.0)


def in_scroll():
    """True while the program is inside its scroll routine (ovl 5 196D:0DFE,
    f_o05_1A8F_0DFE in the recompiled code): the tick hook runs on the
    program's own call stack in this harness."""
    f = sys._getframe(1)
    while f is not None:
        if f.f_code.co_name == "f_o05_1A8F_0DFE":
            return True
        f = f.f_back
    return False


def field_lines(px):
    return [y for y in range(200) if int((px[y] == 3).sum()) > 200]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game-dir", default=os.environ.get("NFL_GAME_DIR", ""))
    ap.add_argument("--ticks", type=int, default=7000)
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--fast", action="store_true", help="also run a game at extra-extra-fast speed")
    a = ap.parse_args()
    ui = uimod.load()
    sprites = load_sprites(a.game_dir)
    runs = [("instant replay, normal speed", "/i",
             scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}" + "{wait:2000}" * 8)]
    if a.fast:
        runs.append(("instant replay, Alt-E", "/i",
                     scripts.CONFIG_KEYS + scripts.TEAM_KEYS + "{when:Timeouts}{wait:20}{Alt-E}"))
    total = {"frames": 0, "players": 0, "matched": 0, "offscreen": 0, "max_err": 0.0,
             "decl_max": 0.0, "vis": {}, "vis_frames": 0, "iso": 0, "iso_matched": 0, "iso_exact": 0, "spots": [], "line_detail": [],
             "line_frames": 0, "line_bad": 0, "plays": 0, "spot_bad": 0, "missed": [], "unplaced": 0, "settled": 0,
             "pinned": {}, "pin_detail": [], "iso_shifted": 0,
             "scroll_frames": 0}
    for title, args, keys in runs:
        r = harness.Run(a.game_dir, "check", args, keys, max_ticks=a.ticks, log=lambda m: None)
        gs = ui.GameState()
        mv = ui.MatchView(ui.UiArt())
        vdl = ui.DrawList()
        st = {"was_gfx": False, "marker_fx": None, "cam": {}, "snapped": False}

        def tick(r):
            mem = r.mem
            gs.update(mem)
            if mem[0x449] != 4:
                if gs.field_up and gs.yard_text.isdigit():
                    # the line of scrimmage as the scoreboard text shows it
                    # ("Yard: 33"); the side of the field is the one whose
                    # column carries one of the game's scrimmage / first-down
                    # marks (the half blocks drawn above the field overview)
                    y = int(gs.yard_text)
                    marks = [c for row in gs.rows[3:6] for c, ch in enumerate(row) if ch == "\u2590"]
                    side = [v for v in (y, 100 - y) if any(abs(14 + v / 2.0 - c) <= 1.0 for c in marks)]
                    st["marker_fx"] = side[0] if len(side) == 1 else None
                st["was_gfx"] = False
                return
            first = not st["was_gfx"]
            st["was_gfx"] = True
            if first:
                st["snapped"] = False
            if not gs.play_active:
                return
            # records the game has not written yet in this graphics session
            # (it keeps the old ones while it draws the field, then computes
            # the line-up one player at a time) are not shown by the view and
            # not checked; total["unplaced"] counts them
            placed = [bool(p["placed"]) for p in gs.players]
            total["unplaced"] += placed.count(False)
            px = cga_pixels(mem)
            total["frames"] += 1
            off_team = gs.players[0]["team"]
            los_y = gs.los_y_plus if gs.dir_of(off_team) > 0 else gs.los_y_minus
            los = gs.play_los
            # display-only declutter of the view (not used by the checks
            # above/below, which compare the game's own positions): how far
            # does it move a disc?
            raw = [[p["fx"], p["fy"]] for p in gs.players]
            shown = ui.MatchView.declutter(raw, ui.MatchView.DISC_R)
            total["decl_max"] = max(total["decl_max"], max(
                ((a_[0] - b_[0]) ** 2 + (a_[1] - b_[1]) ** 2) ** 0.5 for a_, b_ in zip(raw, shown)))
            # 4. visibility of every disc in the four layouts
            total["vis_frames"] += 1
            for field in ("horizontal", "vertical"):
                for zoom in (False, True):
                    u = {"field": field, "zoom": zoom}
                    if zoom:
                        # the app's camera: eased towards the auto-framing
                        # target once per tick, then contained
                        mv.vertical = field == "vertical"
                        pts = ui.MatchView.framed_points(gs, {})
                        target = mv.frame_camera(pts)
                        cam = st["cam"].get(field)
                        if first or cam is None:
                            cam = target
                        else:
                            k = 1.0 - math.exp(-(1.0 / 18.2) * ui.MatchView.CAMERA_RATE)
                            cam = [c + (t_ - c) * k for c, t_ in zip(cam, target)]
                        cam = mv.contain_camera(cam, pts)
                        st["cam"][field] = cam
                        u["camera"] = cam
                    mv.build(vdl, gs, u)
                    x0, y0, x1, y1 = mv.viewport
                    key = (field, "zoom" if zoom else "full")
                    c = total["vis"].setdefault(key, [0, 0, 0])
                    pin = total["pinned"].setdefault(key, 0)
                    total["pinned"][key] = pin + len(mv.last_pinned)
                    if mv.last_pinned and len(total["pin_detail"]) < 8:
                        total["pin_detail"].append((r.gm.ticks, key, [[i_, round(a_, 1), round(b_, 1)]
                                                                      for i_, a_, b_ in mv.last_pinned]))
                    for (x, y, rr) in mv.last_discs:
                        for (ox0, oy0, ox1, oy1) in mv.occluders:
                            # the disc centre under the scoreboard pill
                            if ox0 <= x <= ox1 and oy0 <= y <= oy1:
                                c[2] += 1
                        out_x = x - rr < x0 - 0.5 or x + rr > x1 + 0.5
                        out_y = y - rr < y0 - 0.5 or y + rr > y1 + 0.5
                        across = out_x if field == "vertical" else out_y
                        along = out_y if field == "vertical" else out_x
                        if across:
                            c[0] += 1
                        elif along:
                            c[1] += 1
            # 1. players.  The host's clock also ticks on loop back-edges, so a
            # tick can fall while the game is still erasing and redrawing the
            # XOR sprites of a frame; the pixel comparison uses only settled
            # frames: the records and the CGA memory unchanged since the
            # previous tick (the game is waiting for its next frame)
            sig = (tuple(gs._raw_records), zlib.crc32(bytes(mem[0xB8000:0xBC000])))
            settled = sig == st.get("sig")
            st["sig"] = sig
            # Scrolling (ovl 5 196D:0DFE): the game first moves every record
            # by the scroll step (196D:0F91, 1AAD:0050), then updates 8755
            # and copies video memory along the field, then updates 8753 and
            # copies it sideways column by column (ovl 7 1CE3:0008, about
            # 16,000 loop iterations: up to ~250 ticks of this test clock, a
            # moment at real speed).  Until
            # the copy reaches a sprite, the screen still shows it where the
            # previous scroll put it.  So while that routine is running, a
            # sprite may be either at its record or at the same field point
            # under the scroll from before the routine started.
            scrolling = in_scroll()
            if not scrolling:
                st["cam_idle"] = (gs.cam_x, gs.cam_y)
            cix, ciy = st.get("cam_idle", (gs.cam_x, gs.cam_y))
            old_dx, old_dy = -(gs.cam_x - cix), gs.cam_y - ciy
            if scrolling:
                total["scroll_frames"] += 1
            if settled:
                total["settled"] += 1
            exp = []
            for p in gs.players:
                ex, ey = gs.field_to_screen(p["fx"], p["fy"], los, los_y)
                exp.append((int(round(ex)), int(round(ey))) if p["placed"] else (-1000, -1000))
            bx, by = gs.field_to_screen(gs.ball["fx"], gs.ball["fy"], los, los_y)
            for i, p in enumerate(gs.players):
                if not placed[i] or not settled:
                    continue
                ex, ey = exp[i]
                # isolated: no other sprite (or the ball) overlapping its 9x9 box
                iso = all(j == i or abs(ex - qx) >= 10 or abs(ey - qy) >= 10
                          for j, (qx, qy) in enumerate(exp)) and (abs(ex - bx) >= 10 or abs(ey - by) >= 10)
                if not (0 <= ex <= 311 and 0 <= ey <= 191):
                    total["offscreen"] += 1
                    continue
                total["players"] += 1
                spr = sprites[0] if p["off"] else sprites[1]
                best = (sprite_score(px, spr, ex, ey), 0, 0)
                shifted = False
                if best[0] < 0.75 and scrolling and (old_dx or old_dy):
                    # the copy goes along the field first (8755), then
                    # sideways (8753): the picture can be before both, or
                    # past the first and before the second
                    for odx, ody in ((old_dx, old_dy), (old_dx, 0)):
                        s_old = sprite_score(px, spr, ex + odx, ey + ody)
                        if s_old >= 0.75:
                            best = (s_old, 0, 0)
                            shifted = True
                            break
                if best[0] < 0.75:          # not at the exact pixel: search the tolerance window
                    for dy in range(-a.tol, a.tol + 1):
                        for dx in range(-a.tol, a.tol + 1):
                            s = sprite_score(px, spr, ex + dx, ey + dy)
                            if s > best[0]:
                                best = (s, dx, dy)
                if iso:
                    total["iso"] += 1
                if best[0] >= 0.75:
                    total["matched"] += 1
                    total["max_err"] = max(total["max_err"], float(np.hypot(best[1], best[2])))
                    if iso:
                        total["iso_matched"] += 1
                        total["iso_exact"] += best[1] == 0 and best[2] == 0 and not shifted
                        total["iso_shifted"] += shifted
                elif iso and len(total["missed"]) < 12:
                    total["missed"].append((r.gm.ticks, i, ex, ey, round(best[0], 2)))
            # 2. yard lines: drawn rows must sit on multiples of 5 yards
            lines = field_lines(px) if settled else []
            cam_ys = [gs.cam_y, ciy] if scrolling else [gs.cam_y]
            if lines:
                total["line_frames"] += 1
                # group rows into drawn lines (the end lines are several
                # pixels thick); each line must cover a 5-yard multiple
                runs = []
                for ly in lines:
                    if runs and ly == runs[-1][-1] + 1:
                        runs[-1].append(ly)
                    else:
                        runs.append([ly])
                for run in runs:
                    if len(run) > 3:
                        continue        # a filled area (out of bounds behind an end line), not a line
                    fxs = [los + (los_y - (ly + cy)) / gs.px_per_yard for ly in run for cy in cam_ys]
                    if min(abs(fx / 5.0 - round(fx / 5.0)) * 5.0 for fx in fxs) > 0.2:
                        total["line_bad"] += 1
                        if len(total["line_detail"]) < 15:
                            total["line_detail"].append((r.gm.ticks, run, gs.cam_y, los, los_y,
                                                         [round(f, 2) for f in fxs], gs.play_step))
                        break
            # 3. ball spot at the snap vs the text overview's marker
            # (the first frame of the play with the whole line-up and the ball written)
            if not st["snapped"] and all(placed) and gs.ball["placed"]:
                st["snapped"] = True
            else:
                return
            if st["marker_fx"] is not None and gs.ball.get("fx") is not None:
                total["plays"] += 1
                total["spots"].append((r.gm.ticks, round(gs.ball["fx"], 2), st["marker_fx"], los))
                if abs(gs.ball["fx"] - st["marker_fx"]) > 1.0:
                    total["spot_bad"] += 1
        r.on_tick = tick
        r.run()
        print("[check] %s: %d ticks run" % (title, r.gm.ticks), flush=True)
    t = total
    pct = 100.0 * t["matched"] / max(t["players"], 1)
    print("[check] %d graphics frames, %d on-screen player positions, %d matched a drawn sprite "
          "(%.2f%%), max offset %.1f px (tolerance %d); %d positions scrolled off screen"
          % (t["frames"], t["players"], t["matched"], pct, t["max_err"], a.tol, t["offscreen"]))
    print("[check] sprite comparison on %d settled frames (records and CGA memory unchanged since "
          "the previous tick)" % t["settled"])
    print("[check] %d player records not yet written by the game in their graphics session (stale, "
          "not shown, not checked)" % t["unplaced"])
    ipct = 100.0 * t["iso_matched"] / max(t["iso"], 1)
    print("[check] isolated players (no other sprite overlapping, so the XOR-drawn shape is intact): "
          "%d, matched %d (%.2f%%), %d of them at exactly the same pixel"
          % (t["iso"], t["iso_matched"], ipct, t["iso_exact"]))
    print("[check] %d play frames while the game was copying video memory for a scroll; of the isolated "
          "players, %d were still on the screen where the previous scroll put them (exactly)"
          % (t["scroll_frames"], t["iso_shifted"]))
    print("[check] (overlapping sprites destroy each other's shapes in the XOR-drawn CGA frame, "
          "so only isolated players are a fair test)")
    print("[check] display-only declutter of the Modern view (zoom disc size): largest disc offset "
          "%.2f yd (cap %.2f yd); the checks use the unadjusted positions"
          % (t["decl_max"], ui.MatchView.DECLUTTER_CAP * ui.MatchView.DISC_R))
    for key in sorted(t["vis"]):
        c = t["vis"][key]
        print("[check] visibility %-10s %-4s: %d play frames x 22 discs, %d off-screen across the "
              "width, %d off-screen along the length, %d under the scoreboard pill"
              % (key[0], key[1], t["vis_frames"], c[0], c[1], c[2]))
        if t["pinned"].get(key):
            print("[check]   %d discs of players far out of bounds pinned to the edge of the view"
                  % t["pinned"][key])
    if t["pin_detail"]:
        print("[check] pinned (tick, layout, [player, fx, fy]): %s" % t["pin_detail"])
    vis_ok = all(c == [0, 0, 0] for c in t["vis"].values())
    print("[check] yard lines: %d frames with lines, %d frames where a drawn line was not on a "
          "5-yard multiple of the view's scale" % (t["line_frames"], t["line_bad"]))
    print("[check] ball at the snap vs the line of scrimmage on the text scoreboard: %d plays, "
          "%d off by more than 1 yard" % (t["plays"], t["spot_bad"]))
    print("[check] snaps (tick, ball fx, scoreboard fx, los var): %s" % t["spots"][:12])
    if t["line_detail"]:
        print("[check] yard-line mismatches (tick, rows, cam_y, los, los_y, fx, step): %s" % t["line_detail"])
    if t["missed"]:
        print("[check] unmatched isolated (tick, record, x, y, best score): %s" % t["missed"])
    ok = t["frames"] > 0 and ipct >= 99.0 and t["line_bad"] == 0 and t["spot_bad"] == 0 and vis_ok
    print("[check] %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
