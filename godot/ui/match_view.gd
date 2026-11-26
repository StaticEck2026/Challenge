## Modern match view: the whole field, top-down and horizontal, with the
## players as numbered team-coloured discs, the ball, the line of scrimmage
## (blue) and the first-down line (yellow), the scoreboard pill, the top
## toolbar and the bottom info strip.  Used for the pre-snap field overview
## (text mode), for plays and for the instant replay (CGA mode 4).
##
## Pure layout: build() appends to a DrawList from a GameState and a small
## `ui` dictionary from the Godot view (toolbar labels, interpolated
## positions).  It never touches the program.
class_name MatchView
extends RefCounted

## field.svg geometry (art units): 10 per yard, end line at x = 30.
const ART_W := 1260.0
const ART_H := 593.33
const ART_X0 := 30.0
const ART_Y0 := 30.0
const UPY := 10.0
const FIELD_W := 53.333
const BAR_H := 52.0
const STRIP_H := 108.0
const DISC_R := 0.9          # yards, zoomed camera
const DISC_R_FULL := 0.8     # yards, full field (players are dense at the line)
const ZOOM := 1.8            # camera "Zoom" during plays (at most)
## Yards beyond each sideline the zoom camera keeps in view.
const ZOOM_MARGIN := 1.5
## Height the scoreboard pill and its tab take under the toolbar.
const PILL_SPACE := 96.0
## Display-only declutter: overlapping discs are pushed apart by at most this
## fraction of a disc radius (the game's positions stay what they are).
const DECLUTTER_CAP := 0.5
const DECLUTTER_ITER := 6
## Smallest disc radius on the canvas, px.
const MIN_DISC_PX := 10.5

var art: UiArt = null
var kits: Array = []          # [home kit, away kit] (UiTheme.kit_colors)
var fx0: float = 0.0           # canvas position of the field art
var fy0: float = 0.0
var fs: float = 1.0            # canvas px per art unit
## Largest display offset the declutter applied in the last frame, in yards.
var last_declutter_yards: float = 0.0
## Field orientation: false horizontal (default), true vertical.
var vertical: bool = false
## Side columns left by the vertical field: [left x0, left x1, right x0, right x1].
var columns: Array = []
## Canvas area where the field is visible (not under the chrome): x0, y0, x1, y1.
var viewport: Array = []
## Discs drawn in the last frame: [x, y, r] on the canvas (tests).
var last_discs: Array = []
## Chrome drawn over the field in the last frame: [x0, y0, x1, y1] each (tests).
var occluders: Array = []
var _zoomed: bool = false


func _init(p_art: UiArt = null) -> void:
	art = p_art if p_art != null else UiArt.new()


# ------------------------------------------------------------------ geometry
## Fits the field into the area (left, top, width, height) of the canvas.
## Horizontal: the left goal line on the left.  Vertical: the field turned 90
## degrees counter-clockwise, the left goal line at the bottom (as the game's
## own graphics field runs).
func layout_field(left: float, top: float, aw: float, ah: float) -> void:
	var fw := ART_H if vertical else ART_W      # footprint in art units
	var fh := ART_W if vertical else ART_H
	fs = minf(aw / fw, ah / fh)
	fx0 = left + (aw - fw * fs) * 0.5
	fy0 = top + (ah - fh * fs) * 0.5


## Zoom camera: as large as ZOOM times the horizontal full-field scale while
## the field's whole width (sideline to sideline, plus 1.5 yd) still fits the
## space between the toolbar and the strip (horizontal) or the screen width
## (vertical); centred across the field, following fx along its length.
func layout_zoom(cam_fx: float) -> void:
	var top := BAR_H
	var ah := UiTheme.H - STRIP_H - BAR_H
	var base := minf((UiTheme.W - 80.0) / ART_W, (ah - 92.0) / ART_H)
	var span := FIELD_W * UPY + 2.0 * ZOOM_MARGIN * UPY
	var across := (UiTheme.W - 80.0) if vertical else ah
	fs = minf(base * ZOOM, across / span)
	var v_mid := ART_Y0 + FIELD_W * 0.5 * UPY
	var u_cam := ART_X0 + (clampf(cam_fx, -10.0, 110.0) + 10.0) * UPY
	if vertical:
		# the ball sits in the middle of the space under the scoreboard pill
		var mid_y := top + PILL_SPACE + (ah - PILL_SPACE) * 0.5
		fx0 = UiTheme.W * 0.5 - v_mid * fs
		fy0 = clampf(mid_y - (ART_W - u_cam) * fs, top + ah - ART_W * fs, top)
	else:
		fy0 = top + ah * 0.5 - v_mid * fs
		fx0 = clampf(UiTheme.W * 0.5 - u_cam * fs, UiTheme.W - ART_W * fs, 0.0)


## The one orientation transform: field yards (fx from the left goal line of
## the game's field, fy from its top sideline) to canvas [x, y].
func pt(fx: float, fy: float) -> Array:
	var u := ART_X0 + (fx + 10.0) * UPY
	var v := ART_Y0 + fy * UPY
	if vertical:
		return [fx0 + v * fs, fy0 + (ART_W - u) * fs]
	return [fx0 + u * fs, fy0 + v * fs]


## Canvas rectangle [x, y, w, h] of the whole field art.
func field_rect() -> Array:
	if vertical:
		return [fx0, fy0, ART_H * fs, ART_W * fs]
	return [fx0, fy0, ART_W * fs, ART_H * fs]


## Rotation (degrees) of things drawn along the field's length.
func base_rot() -> float:
	return -90.0 if vertical else 0.0


func yard_px() -> float:
	return UPY * fs


## Places art drawn for the horizontal field (w x h, centred on c) turned with
## the field.
func _art_at(dl: DrawList, art_name: String, c: Array, w: float, h: float,
		params: Dictionary = {}) -> void:
	dl.art(art_name, c[0] - w * 0.5, c[1] - h * 0.5, w, h, params, base_rot())


## Text centred on canvas point c, turned by rot degrees.
static func text_at(dl: DrawList, c: Array, s: String, size: float, color: String, rot: float,
		bold: bool = true) -> void:
	var off := DrawList.CAP * size * 0.5
	var a := deg_to_rad(rot)
	dl.text(c[0] - off * sin(a), c[1] + off * cos(a), s, size, color, "c", bold, rot)


## The team's own colour (end zones, scoreboard blocks, strip block); it does
## not change in a colour clash, only the discs do.
func team_color(team: int) -> String:
	var k: Dictionary = kits[0] if team == 1 else kits[1]
	return k["primary"]


## Text on the team's own colour (white or near-black by WCAG contrast).
func team_text(team: int) -> String:
	var k: Dictionary = kits[0] if team == 1 else kits[1]
	return k["primary_text"]


## The team's disc kit this match (the visitor's discs may change colour).
func team_kit(team: int) -> Dictionary:
	return kits[0] if team == 1 else kits[1]


func team_code(gs: GameState, team: int) -> String:
	var s := gs.short_name(team)
	if s == "":
		return "HOME" if team == 1 else "AWAY"
	return UiTheme.abbrev(s, gs.short_name(3 - team))


# --------------------------------------------------------------------- build
## ui keys: view_label, speed_label, paused, sound, players (22 x [fx, fy]
## interpolated, optional), ball ([fx, fy], optional), marker_fx (optional),
## overlay_top (bool: windows cover the field).
func build(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	kits = UiTheme.kit_colors(gs.home_short, gs.away_short)
	vertical = str(ui.get("field", "horizontal")) == "vertical"
	dl.rect(0.0, 0.0, UiTheme.W, UiTheme.H, UiTheme.BG)
	var zoom := 1.0
	var cam: Array = ui.get("camera", [])
	if ui.get("zoom", false) and gs.play_active and cam.size() == 2:
		zoom = ZOOM
	_zoomed = zoom > 1.0
	var top := BAR_H + (12.0 if vertical else 78.0)
	var bottom := UiTheme.H - (12.0 if vertical else STRIP_H + 14.0)
	columns = []
	if zoom > 1.0:
		# zoom: the whole width of the field stays visible, the camera
		# follows the ball along the field's length; the chrome is the
		# horizontal view's (pill at the top centre, strip at the bottom)
		layout_zoom(cam[0])
		viewport = [0.0, BAR_H, UiTheme.W, UiTheme.H - STRIP_H]
	elif vertical:
		# the field fills the height, centred; the side columns stay free
		layout_field(40.0, top, UiTheme.W - 80.0, bottom - top)
		var fr := field_rect()
		columns = [0.0, fr[0], fr[0] + fr[2], UiTheme.W]
		viewport = [0.0, BAR_H, UiTheme.W, UiTheme.H]
	else:
		layout_field(40.0, top, UiTheme.W - 80.0, bottom - top)
		viewport = [0.0, BAR_H, UiTheme.W, UiTheme.H - STRIP_H]
	_field(dl, gs)
	_lines(dl, gs)
	last_discs = []
	occluders = []
	if gs.play_active and gs.players.size() == 22:
		_players(dl, gs, ui)
	else:
		_ball_marker(dl, gs, ui)
	toolbar(dl, ui)
	scoreboard(dl, gs)
	if vertical and columns.size() == 4:
		_side_info(dl, gs)
	else:
		_strip(dl, gs)


func _left_team(gs: GameState) -> int:
	# The team attacking to the right defends the left end zone.
	return 1 if gs.dir_home > 0 else 2


func _field(dl: DrawList, gs: GameState) -> void:
	var lt := _left_team(gs)
	var rt := 3 - lt
	var lc := team_color(lt)
	var rc := team_color(rt)
	_art_at(dl, "field", pt(50.0, FIELD_W * 0.5), ART_W * fs, ART_H * fs, {"left": lc, "right": rc})
	# End-zone names: across the end zone, reading towards the field
	# (horizontal) or upright (vertical).
	var ez := 10.0 * yard_px()
	var size := minf(ez * 0.46, 64.0)
	var ln := gs.short_name(lt).to_upper() if gs.short_name(lt) != "" else gs.full_name(lt).to_upper()
	var rn := gs.short_name(rt).to_upper() if gs.short_name(rt) != "" else gs.full_name(rt).to_upper()
	var lrot := 0.0 if vertical else -90.0
	var rrot := 0.0 if vertical else 90.0
	text_at(dl, pt(-5.0, FIELD_W * 0.5), ln, size, team_text(lt) + "e6", lrot)
	text_at(dl, pt(105.0, FIELD_W * 0.5), rn, size, team_text(rt) + "e6", rrot)
	# Yard numbers, painted with their bottoms towards the nearer sideline:
	# horizontal, upright near the bottom sideline and upside down near the
	# top one; vertical, turned a quarter towards the left and right sidelines.
	var ns := yard_px() * 2.0
	var near := base_rot()            # rotation of the numbers by the fy = 53 sideline
	var far := near + 180.0
	for k in range(1, 10):
		var yd := k * 10
		var label := str(mini(yd, 100 - yd))
		text_at(dl, pt(float(yd), FIELD_W - 10.0), label, ns, "#f4f7f1dd", near)
		text_at(dl, pt(float(yd), 10.0), label, ns, "#f4f7f1dd", far)
		if yd != 50:
			# arrows point to the nearer goal, beside the number (in yards)
			var d := -1.0 if yd < 50 else 1.0
			var off := (dl.measure(label, ns, true) * 0.5) / yard_px() + 0.9
			_arrow(dl, float(yd) + d * off, FIELD_W - 10.0, d)
			_arrow(dl, float(yd) + d * off, 10.0, d)


## A small triangle at field point (fx, fy) pointing along the field (d = +-1).
func _arrow(dl: DrawList, fx: float, fy: float, d: float) -> void:
	var s := 0.42
	var pts := [[fx + d * s, fy], [fx - d * s * 0.6, fy - s * 0.8], [fx - d * s * 0.6, fy + s * 0.8]]
	var flat := []
	for q in pts:
		var c := pt(q[0], q[1])
		flat.append(c[0])
		flat.append(c[1])
	dl.poly(flat, "#f4f7f1cc")


func _lines(dl: DrawList, gs: GameState) -> void:
	var h := FIELD_W * yard_px()
	var w := yard_px() * 0.75
	var p := {"H": h / w * 16.0}
	if gs.first_down_fx > 0.0 and gs.first_down_fx < 100.0:
		_art_at(dl, "line_first_down", pt(gs.first_down_fx, FIELD_W * 0.5), w, h, p)
	if gs.los_fx >= 0.0 and gs.los_fx <= 100.0:
		_art_at(dl, "line_scrimmage", pt(gs.los_fx, FIELD_W * 0.5), w, h, p)


func _ball_marker(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	var bx: float = gs.ball_fx
	if gs.los_fx >= 0.0 and absf(gs.ball_fx - gs.los_fx) <= 2.0:
		bx = gs.los_fx
	bx = ui.get("marker_fx", bx)
	if bx < -10.0 or bx > 110.0 or not gs.field_up:
		return
	var c := pt(bx, FIELD_W * 0.5)
	_ball(dl, c[0], c[1], 1.0)


## The ball at canvas (x, y), lying along the field.
func _ball(dl: DrawList, x: float, y: float, k: float) -> void:
	var bw := yard_px() * 1.5 * k
	var bh := bw * 26.0 / 40.0
	dl.art("ball", x - bw * 0.5, y - bh * 0.5, bw, bh, {}, base_rot())


func _players(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	var pos: Array = ui.get("players", [])
	var disc := DISC_R if _zoomed else DISC_R_FULL
	# never smaller than MIN_DISC_PX on screen (the vertical full field is
	# small), so the numbers stay legible; the declutter works at that size
	var r := maxf(disc * yard_px(), MIN_DISC_PX)
	disc = r / yard_px()
	var carrier: int = gs.ball.get("carrier", -1)
	# defence first, so the offence (and the ball carrier) sit on top
	var order := []
	for i in range(11, 22):
		order.append(i)
	for i in range(11):
		if i != carrier:
			order.append(i)
	if carrier >= 0:
		order.append(carrier)
	var raw := []
	for i in range(22):
		var p0: Dictionary = gs.players[i]
		if pos.size() == 22:
			raw.append([float(pos[i][0]), float(pos[i][1])])
		else:
			raw.append([float(p0["fx"]), float(p0["fy"])])
	var shown := declutter(raw, disc)
	last_declutter_yards = 0.0
	for i2 in range(22):
		last_declutter_yards = maxf(last_declutter_yards, _dist(raw[i2], shown[i2]))
	for i in order:
		var p: Dictionary = gs.players[i]
		var c := pt(shown[i][0], shown[i][1])
		var x: float = c[0]
		var y: float = c[1]
		last_discs.append([x, y, r])
		var team: int = p["team"]
		var k := team_kit(team)
		if i == carrier:
			dl.art("token_ring", x - r * 1.45, y - r * 1.45, r * 2.9, r * 2.9, {"color": UiTheme.WARN})
		dl.art("token", x - r * 32.0 / 27.0, y - r * 32.0 / 27.0, r * 64.0 / 27.0, r * 64.0 / 27.0,
			{"fill": k["fill"], "stroke": k["rim"]})
		var label := str(p["jersey"]) if p["jersey"] > 0 else str(p["pos"])
		var fsz := r * (1.02 if label.length() <= 2 else 0.8)
		dl.text_mid(x, y, label, fsz, k["text"], "c", true)
	var bpos: Array = ui.get("ball", [])
	var bfx: float = gs.ball.get("fx", -100.0)
	var bfy: float = gs.ball.get("fy", 0.0)
	if bpos.size() == 2:
		bfx = bpos[0]
		bfy = bpos[1]
	if carrier >= 0:
		# with the carrier, wherever the declutter put his disc
		bfx = shown[carrier][0]
		bfy = shown[carrier][1]
		var cc := pt(bfx, bfy)
		_ball(dl, cc[0] + r * 0.8, cc[1] + r * 0.75, 0.62)
	elif bfx > -20.0:
		var cb := pt(bfx, bfy)
		_ball(dl, cb[0], cb[1], 0.8)


# ------------------------------------------------------------------- chrome
## The slim dark toolbar: app mark, then View / Speed / Pause / Sound /
## Screenshot / field orientation / Zoom / More / Fullscreen buttons
## (hotspots "ui:...").
func toolbar(dl: DrawList, ui: Dictionary) -> void:
	dl.rect(0.0, 0.0, UiTheme.W, BAR_H, UiTheme.BAR)
	dl.rect(0.0, BAR_H - 1.0, UiTheme.W, 1.0, UiTheme.BAR_LINE)
	dl.art("mark", 16.0, 10.0, 32.0, 32.0)
	dl.text_mid(58.0, BAR_H * 0.5, "NFL Challenge", 18.0, UiTheme.TEXT, "l", true)
	var sub_x := 58.0 + dl.measure("NFL Challenge", 18.0, true) + 12.0
	dl.text_mid(sub_x, BAR_H * 0.5, str(ui.get("subtitle", "")), 15.0, UiTheme.TEXT_3)
	var x := UiTheme.W - 16.0
	var paused: bool = ui.get("paused", false)
	var buttons := [
		["icon_fullscreen", "", "ui:fullscreen"],
		["icon_menu", "", "ui:more"],
		["icon_zoom", "Zoom" if ui.get("zoom", false) else "Full field", "ui:zoom"],
		["icon_field", "Vertical" if str(ui.get("field", "")) == "vertical" else "Horizontal",
			"ui:field"],
		["icon_camera", "", "ui:screenshot"],
		["icon_sound", "On" if ui.get("sound", true) else "Off", "ui:sound"],
		["icon_play" if paused else "icon_pause", "Resume" if paused else "Pause", "ui:pause"],
		["icon_speed", str(ui.get("speed_label", "1x")), "ui:speed"],
		["icon_view", str(ui.get("view_label", "Modern")), "ui:view"],
	]
	for b in buttons:
		var w := bar_button(dl, x, b[0], b[1], b[2], true)
		x -= w + 8.0


## A toolbar button ending at x (right aligned) or starting at x. Returns width.
func bar_button(dl: DrawList, x: float, icon: String, label: String, action: String,
		right: bool) -> float:
	var h := 34.0
	var w := 40.0 if label == "" else 30.0 + 16.0 + dl.measure(label, 15.0, true) + 8.0
	var x0 := x - w if right else x
	var y0 := (BAR_H - h) * 0.5
	var chrome := _chrome(w, h, 8.0, {"fill": UiTheme.CARD, "line": UiTheme.CARD_LINE})
	dl.art("button", x0, y0, w, h, chrome)
	dl.art(icon, x0 + 11.0, y0 + 8.0, 18.0, 18.0, {"color": UiTheme.TEXT_2})
	if label != "":
		dl.text_mid(x0 + 36.0, y0 + h * 0.5, label, 15.0, UiTheme.TEXT, "l", true)
	dl.hot(x0, y0, w, h, action)
	return w


## Size placeholders for the chrome art (card, panel, button, scoreboard).
static func _chrome(w: float, h: float, r: float, extra: Dictionary) -> Dictionary:
	var p := {"W": w, "H": h, "R": r, "W1": w - 1.5, "H1": h - 1.5}
	p.merge(extra, true)
	return p


## The scoreboard pill: [visitor code | score]  quarter  clock  [score | home code],
## with possession dots and time-outs, and down & distance underneath.
func scoreboard(dl: DrawList, gs: GameState) -> void:
	var h := 56.0
	var bw := 96.0
	var sw := 96.0
	var mw := 168.0
	var w := (bw + sw) * 2.0 + mw
	var mid_x := scoreboard_x()
	var x0 := mid_x - w * 0.5
	var y0 := BAR_H + 10.0
	var r := h * 0.5
	var away := team_color(2)
	var home := team_color(1)
	occluders.append([x0, y0, x0 + w, y0 + h + 26.0])
	dl.art("scoreboard", x0, y0, w, h, {"W": w, "H": h, "R": r, "BW": bw, "BW2": bw + r,
		"XR": w - bw - r, "MW": w - 2.0 * bw, "W1": w - 2.0, "H1": h - 2.0, "left": away,
		"right": home, "SL": bw + sw, "SR": w - bw - sw, "SY": 12.0, "SH": h - 24.0})
	var mid := y0 + h * 0.5
	var on_away := team_text(2)
	var on_home := team_text(1)
	dl.text_mid(x0 + bw * 0.5 + 6.0, mid, team_code(gs, 2), 22.0, on_away, "c", true)
	dl.text_mid(x0 + w - bw * 0.5 - 6.0, mid, team_code(gs, 1), 22.0, on_home, "c", true)
	dl.text_mid(x0 + bw + sw * 0.5 + 8.0, mid, str(gs.score_away), 28.0, UiTheme.TEXT, "c", true)
	dl.text_mid(x0 + w - bw - sw * 0.5 - 8.0, mid, str(gs.score_home), 28.0, UiTheme.TEXT, "c", true)
	var q := quarter_label(gs.quarter)
	dl.text_mid(x0 + w * 0.5, mid - 10.0, q, 13.0, UiTheme.TEXT_3, "c", true)
	dl.text_mid(x0 + w * 0.5, mid + 9.0, gs.clock_text, 22.0, UiTheme.TEXT, "c", true)
	# possession: a small ball next to the score of the team with the ball
	if gs.poss == 2 or gs.poss == 1:
		var px := x0 + bw + 17.0 if gs.poss == 2 else x0 + w - bw - 17.0
		var bw2 := 22.0
		dl.art("ball", px - bw2 * 0.5, mid - bw2 * 13.0 / 40.0, bw2, bw2 * 26.0 / 40.0)
	# time-outs: three dots under each team block
	_timeouts(dl, x0 + bw * 0.5 + 6.0, y0 + h - 9.0, gs.timeouts_away, on_away)
	_timeouts(dl, x0 + w - bw * 0.5 - 6.0, y0 + h - 9.0, gs.timeouts_home, on_home)
	# down & distance tab under the pill
	var dd := down_distance(gs)
	if dd != "":
		var tw := dl.measure(dd, 15.0, true) + 36.0
		var ty := y0 + h - 4.0
		dl.rrect(mid_x - tw * 0.5, ty, tw, 30.0, 10.0, "#121821", 1.0, "#ffffff1a")
		dl.rect(mid_x - tw * 0.5 + 1.0, ty + 1.0, tw - 2.0, 10.0, "#121821")
		dl.text_mid(mid_x, ty + 16.0, dd, 15.0, UiTheme.WARN, "c", true)


## Centre x of the scoreboard: the screen centre, or the left column when the
## vertical field takes the middle.
func scoreboard_x() -> float:
	if columns.size() == 4:
		return (float(columns[0]) + float(columns[1])) * 0.5
	return UiTheme.W * 0.5


func _timeouts(dl: DrawList, x: float, y: float, n: int, color: String) -> void:
	for i in range(3):
		var on := i < n
		dl.circle(x - 10.0 + i * 10.0, y, 2.6, color + ("ff" if on else "40"))


static func quarter_label(q: int) -> String:
	if q >= 5:
		return "OT"
	return ["1ST", "2ND", "3RD", "4TH"][clampi(q, 1, 4) - 1]


static func ordinal(n: int) -> String:
	return ["", "1st", "2nd", "3rd", "4th"][clampi(n, 0, 4)]


## "2nd & 9 · IND 34" from the displayed scoreboard.
func down_distance(gs: GameState) -> String:
	if gs.down < 1 or gs.down > 4:
		return ""
	var togo := gs.togo_text
	if togo == "":
		togo = "Goal"
	var s := "%s & %s" % [ordinal(gs.down), togo]
	var spot := ball_spot_text(gs)
	if spot != "":
		s += "  ·  " + spot
	return s


## "IND 34" (the side of the field the ball is on) or "50".
func ball_spot_text(gs: GameState) -> String:
	if gs.yard_text == "" or not gs.yard_text.is_valid_int():
		return ""
	var y := gs.yard_text.to_int()
	if y == 50:
		return "50"
	var fx := gs.los_fx if gs.los_fx >= 0.0 else gs.ball_fx
	if fx < 0.0:
		return str(y)
	# own half of the team defending the left end zone is fx < 50
	var side := _left_team(gs) if fx < 50.0 else 3 - _left_team(gs)
	return "%s %d" % [team_code(gs, side), y]


## Bottom strip: possession and time-outs, the game's play text, and the
## players of the team with the ball as chips.
func _strip(dl: DrawList, gs: GameState) -> void:
	var y0 := UiTheme.H - STRIP_H
	dl.rect(0.0, y0, UiTheme.W, STRIP_H, UiTheme.BAR)
	dl.rect(0.0, y0, UiTheme.W, 1.0, UiTheme.BAR_LINE)
	_possession(dl, gs, 28.0, y0 + 22.0)
	_play_text(dl, gs, 470.0, y0 + 36.0, 900.0, 3)
	if gs.play_active and gs.players.size() == 22:
		_chips(dl, gs, 1408.0, y0 + 14.0)


## Vertical field: the strip's contents as two cards at the foot of the side
## columns (possession left, play text and line-up right).
func _side_info(dl: DrawList, gs: GameState) -> void:
	var l0: float = columns[0]
	var l1: float = columns[1]
	var r0: float = columns[2]
	var r1: float = columns[3]
	var h := 108.0
	var y := UiTheme.H - h - 16.0
	var lw := minf(l1 - l0 - 48.0, 460.0)
	dl.art("panel", (l0 + l1 - lw) * 0.5, y, lw, h, _chrome(lw, h, 16.0, {}))
	_possession(dl, gs, (l0 + l1 - lw) * 0.5 + 22.0, y + 22.0)
	var rw := r1 - r0 - 48.0
	var chips := gs.play_active and gs.players.size() == 22
	if not chips and gs.pbp.size() == 0:
		return
	var rh := 196.0 if chips else h
	var ry := UiTheme.H - rh - 16.0
	dl.art("panel", r0 + 24.0, ry, rw, rh, _chrome(rw, rh, 16.0, {}))
	_play_text(dl, gs, r0 + 48.0, ry + 36.0, rw - 48.0, 3)
	if rh > h:
		_chips(dl, gs, r0 + 48.0 + (rw - 48.0 - 11.0 * 44.0) * 0.5, ry + 104.0)


## Team-with-the-ball block: code, time-outs, name, quarter.
func _possession(dl: DrawList, gs: GameState, x: float, y: float) -> void:
	var team := gs.poss
	if team != 1 and team != 2:
		return
	var c := team_color(team)
	dl.rrect(x, y, 64.0, 64.0, 14.0, c)
	dl.text_mid(x + 32.0, y + 32.0, team_code(gs, team), 20.0, team_text(team), "c", true)
	_timeouts(dl, x + 32.0, y + 54.0, gs.timeouts_home if team == 1 else gs.timeouts_away,
		team_text(team))
	dl.text(x + 82.0, y + 24.0, dl.fit(gs.full_name(team), 20.0, 300.0, true), 20.0, UiTheme.TEXT,
		"l", true)
	dl.text(x + 82.0, y + 52.0, "Ball  ·  " + quarter_label(gs.quarter) + " quarter", 15.0,
		UiTheme.TEXT_2)


## The game's play and result text, wrapped to width w, at most max_lines.
func _play_text(dl: DrawList, gs: GameState, tx: float, ly: float, tw: float,
		max_lines: int) -> void:
	var lines := gs.pbp
	if lines.size() == 0 and gs.play_active:
		lines = PackedStringArray(["Play in progress"])
	var n := 0
	for s in _wrap_join(dl, lines, 17.0, tw):
		var first := n == 0 and lines.size() > 1
		dl.text(tx, ly, dl.fit(s, 18.0 if first else 17.0, tw, first), 17.0 if not first else 18.0,
			UiTheme.TEXT if first else UiTheme.TEXT_2, "l", first)
		ly += 25.0
		n += 1
		if n >= max_lines:
			break


## Top of the status chips ("Offense selected") the screens draw over the
## field's foot.
func status_y() -> float:
	if vertical:
		return UiTheme.H - 108.0 - 16.0 - 46.0
	return UiTheme.H - STRIP_H - 48.0


## Joins the game's 70-column lines into sentences and wraps them to width.
func _wrap_join(dl: DrawList, lines: PackedStringArray, size: float, w: float) -> Array:
	if lines.size() == 0:
		return []
	var out := [lines[0]]
	var rest := ""
	for i in range(1, lines.size()):
		rest += (" " if rest != "" else "") + lines[i]
	if rest == "":
		return out
	var words := rest.split(" ", false)
	var cur := ""
	for wd in words:
		var t := wd if cur == "" else cur + " " + wd
		if dl.measure(t, size) > w and cur != "":
			out.append(cur)
			cur = wd
		else:
			cur = t
	if cur != "":
		out.append(cur)
	return out


## The 11 players of the offence as chips: number disc and position.
func _chips(dl: DrawList, gs: GameState, x: float, y: float) -> void:
	var team: int = gs.players[0]["team"]
	var k := team_kit(team)
	var cw := 44.0
	for i in range(11):
		var p: Dictionary = gs.players[i]
		var px := x + i * cw
		dl.circle(px + 18.0, y + 26.0, 17.0, k["fill"])
		dl.ring(px + 18.0, y + 26.0, 16.0, 2.0, k["rim"])
		dl.text_mid(px + 18.0, y + 26.0, str(p["jersey"]), 15.0, k["text"], "c", true)
		dl.text(px + 18.0, y + 66.0, str(p["pos"]), 12.0, UiTheme.TEXT_2, "c", true)


## Pushes overlapping discs (radius r yards) apart for display, a few
## relaxation passes, each disc moved at most DECLUTTER_CAP * r from its real
## position. Deterministic: pairs are visited in index order.
static func declutter(pts: Array, r: float) -> Array:
	var out := []
	for p in pts:
		out.append([float(p[0]), float(p[1])])
	var want := 2.0 * r * 0.92
	var cap := DECLUTTER_CAP * r
	for _it in range(DECLUTTER_ITER):
		var moved := false
		for i in range(out.size()):
			for j in range(i + 1, out.size()):
				var dx: float = out[j][0] - out[i][0]
				var dy: float = out[j][1] - out[i][1]
				var d := sqrt(dx * dx + dy * dy)
				if d >= want:
					continue
				if d < 0.0001:
					dx = 0.0
					dy = 1.0
					d = 1.0
				var push := (want - d) * 0.5
				out[i] = [out[i][0] - dx / d * push, out[i][1] - dy / d * push]
				out[j] = [out[j][0] + dx / d * push, out[j][1] + dy / d * push]
				moved = true
		for k in range(out.size()):
			out[k] = _clamp_offset(pts[k], out[k], cap)
		if not moved:
			break
	return out


static func _clamp_offset(orig: Array, p: Array, cap: float) -> Array:
	var dx: float = p[0] - orig[0]
	var dy: float = p[1] - orig[1]
	var d := sqrt(dx * dx + dy * dy)
	if d <= cap:
		return p
	return [orig[0] + dx / d * cap, orig[1] + dy / d * cap]


static func _dist(a: Array, b: Array) -> float:
	var dx: float = a[0] - b[0]
	var dy: float = a[1] - b[1]
	return sqrt(dx * dx + dy * dy)
