## The Modern view in Godot: reads the program's memory through GameState
## every frame, lays the screen out with Screens / MatchView into a DrawList
## on the 1920 x 1080 logical canvas, and draws that list scaled to the
## window: SVG art rasterised with Image.load_svg_from_string() at the
## current scale (cached, re-rasterised when the window size changes), shapes
## with CanvasItem draw calls, text with the bundled Inter font.
##
## Players move smoothly: the game updates positions a few times a second;
## between two updates this view interpolates each disc linearly over the
## measured update interval (60 fps on a 60 Hz display).
##
## Presentation only: memory is read, never written.  Clicks on buttons emit
## key_action(name) with the key the screen asks for; main.gd turns that into
## the same scancodes a key press would produce.
class_name ModernView
extends Control

signal key_action(key_name: String)
signal ui_action(action: String)

const FONT_UI := "res://fonts/Inter-Medium.otf"
const FONT_UI_BOLD := "res://fonts/Inter-Bold.otf"
const FONT_MONO := "res://fonts/JetBrainsMono-Medium.ttf"
const MAX_TEXTURES := 600
## A jump longer than this (yards) between two updates is not animated.
const SNAP_YARDS := 9.0

var state: GameState = null
var art: UiArt = null
var screens: Screens = null
var dl: DrawList = null
## Toolbar labels etc. from main.gd (view_label, speed_label, paused, sound).
var ui: Dictionary = {}
var screen_id: String = ""
## Window-space rectangle where the original text screen should be shown
## (unrecognised screens), or an empty Rect2.
var textgrid_rect: Rect2 = Rect2()

var _font: Font = null
var _font_bold: Font = null
var _font_mono: Font = null
var _textures: Dictionary = {}
var _styles: Dictionary = {}
var _colors: Dictionary = {}
var _scale: float = 1.0
var _origin: Vector2 = Vector2.ZERO
var _hover: Array = []

# interpolation
var _last_step: int = -1
var _prev: Array = []
var _cur: Array = []
var _ball_prev: Array = []
var _ball_cur: Array = []
var _since: float = 0.0
var _interval: float = 0.12
var _marker_prev: float = -100.0
var _marker_cur: float = -100.0
var _marker_since: float = 0.0
var _camera: Array = []


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_STOP
	texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	state = GameState.new()
	art = UiArt.new()
	screens = Screens.new(art)
	dl = DrawList.new()
	_font = _load_font(FONT_UI)
	_font_bold = _load_font(FONT_UI_BOLD)
	_font_mono = _load_font(FONT_MONO)
	dl.measurer = Callable(self, "_measure")
	resized.connect(_on_resized)


static func _load_font(path: String) -> Font:
	var f := load(path) as FontFile
	if f == null:
		f = FontFile.new()
		f.load_dynamic_font(path)
	f.antialiasing = TextServer.FONT_ANTIALIASING_GRAY
	f.hinting = TextServer.HINTING_LIGHT
	return f


func _on_resized() -> void:
	_textures.clear()
	queue_redraw()


## Per frame from main.gd (main thread): read memory, advance the
## interpolation, lay the frame out.
func refresh(m: Machine, delta: float) -> void:
	state.update(m.cpu.mem)
	_animate(delta)
	var u := ui.duplicate()
	if state.play_active and _cur.size() == 22:
		u["players"] = _blend(_prev, _cur)
		if _ball_cur.size() == 2:
			var b := _blend_one(_ball_prev, _ball_cur)
			u["ball"] = b
			# the zoom camera follows the ball, eased
			if _camera.size() != 2 or absf(_camera[0] - b[0]) > 25.0:
				_camera = [b[0], b[1]]
			var k := clampf(delta * 4.0, 0.0, 1.0)
			_camera = [lerpf(_camera[0], b[0], k), lerpf(_camera[1], b[1], k)]
			u["camera"] = _camera
	else:
		_camera = []
	if _marker_cur > -50.0:
		var t := clampf(_marker_since / 0.25, 0.0, 1.0)
		u["marker_fx"] = lerpf(_marker_prev, _marker_cur, t)
	screen_id = screens.build(dl, state, u)
	_update_textgrid_rect()
	queue_redraw()


func _animate(delta: float) -> void:
	_since += delta
	_marker_since += delta
	if state.play_active and state.players.size() == 22:
		if state.play_step != _last_step:
			var nxt := []
			for p in state.players:
				nxt.append([float(p["fx"]), float(p["fy"])])
			var b := [float(state.ball.get("fx", -100.0)), float(state.ball.get("fy", 0.0))]
			if _cur.size() != 22 or _last_step < 0:
				_prev = nxt
				_ball_prev = b
			else:
				_prev = _blend(_prev, _cur)
				_ball_prev = _blend_one(_ball_prev, _ball_cur)
				_interval = clampf(lerpf(_interval, _since, 0.35), 0.03, 0.6)
				for i in range(22):
					if _far(_prev[i], nxt[i]):
						_prev[i] = nxt[i]
				if _far(_ball_prev, b):
					_ball_prev = b
			_cur = nxt
			_ball_cur = b
			_since = 0.0
			_last_step = state.play_step
	else:
		_last_step = -1
		_cur = []
	if not state.graphics and state.field_up:
		if absf(state.ball_fx - _marker_cur) > 0.01:
			_marker_prev = state.ball_fx if _marker_cur < -50.0 else _marker_cur
			if absf(state.ball_fx - _marker_prev) > 30.0:
				_marker_prev = state.ball_fx
			_marker_cur = state.ball_fx
			_marker_since = 0.0


static func _far(a: Array, b: Array) -> bool:
	return absf(a[0] - b[0]) > SNAP_YARDS or absf(a[1] - b[1]) > SNAP_YARDS


func _blend(a: Array, b: Array) -> Array:
	var out := []
	for i in range(b.size()):
		out.append(_blend_one(a[i] if i < a.size() else b[i], b[i]))
	return out


func _blend_one(a: Array, b: Array) -> Array:
	if a.size() != 2:
		return b
	var t := clampf(_since / _interval, 0.0, 1.0)
	return [lerpf(a[0], b[0], t), lerpf(a[1], b[1], t)]


func _measure(text: String, size: float, bold: bool, mono: bool) -> float:
	var f := _font_mono if mono else (_font_bold if bold else _font)
	return f.get_string_size(text, HORIZONTAL_ALIGNMENT_LEFT, -1, 64).x * size / 64.0


# ------------------------------------------------------------------ drawing
func _layout() -> void:
	_scale = minf(size.x / UiTheme.W, size.y / UiTheme.H)
	_origin = (size - Vector2(UiTheme.W, UiTheme.H) * _scale) * 0.5


func _p(x: float, y: float) -> Vector2:
	return _origin + Vector2(x, y) * _scale


func _col(s: String) -> Color:
	if not _colors.has(s):
		_colors[s] = Color.html(s)
	return _colors[s]


func _draw() -> void:
	_layout()
	draw_rect(Rect2(Vector2.ZERO, size), _col(UiTheme.BG))
	for op in dl.ops:
		var o: Array = op
		match o[0]:
			"rect":
				draw_rect(Rect2(_p(o[1], o[2]), Vector2(o[3], o[4]) * _scale), _col(o[5]))
			"rrect":
				# ["rrect", x, y, w, h, radius, fill, border, border_color]
				_style(o[6], o[5], o[7], o[8]).draw(get_canvas_item(),
					Rect2(_p(o[1], o[2]), Vector2(o[3], o[4]) * _scale))
			"circle":
				draw_circle(_p(o[1], o[2]), o[3] * _scale, _col(o[4]))
				draw_arc(_p(o[1], o[2]), o[3] * _scale, 0.0, TAU, 48, _col(o[4]), 1.0, true)
			"ring":
				draw_arc(_p(o[1], o[2]), o[3] * _scale, 0.0, TAU, 64, _col(o[5]), o[4] * _scale, true)
			"line":
				draw_line(_p(o[1], o[2]), _p(o[3], o[4]), _col(o[6]), o[5] * _scale, true)
			"poly":
				var pts := PackedVector2Array()
				var src: Array = o[1]
				for i in range(0, src.size(), 2):
					pts.append(_p(src[i], src[i + 1]))
				draw_colored_polygon(pts, _col(o[2]))
			"text":
				_draw_text(o)
			"art":
				_draw_art(o)
			"textgrid":
				pass    # main.gd shows the HD text renderer in textgrid_rect
	if _hover.size() == 4:
		var hr := Rect2(_p(_hover[0], _hover[1]), Vector2(_hover[2], _hover[3]) * _scale)
		_style("#ffffff14", 10.0, 0.0, "").draw(get_canvas_item(), hr)


func _style(fill: String, r: float, border: float, border_color: String) -> StyleBoxFlat:
	var key := "%s|%.1f|%.1f|%s|%.3f" % [fill, r, border, border_color, _scale]
	if _styles.has(key):
		return _styles[key]
	var sb := StyleBoxFlat.new()
	sb.bg_color = _col(fill)
	sb.set_corner_radius_all(int(round(r * _scale)))
	sb.corner_detail = 8
	sb.anti_aliasing = true
	if border > 0.0:
		sb.set_border_width_all(maxi(1, int(round(border * _scale))))
		sb.border_color = _col(border_color)
	_styles[key] = sb
	return sb


func _draw_text(o: Array) -> void:
	var f := _font_mono if o[9] else (_font_bold if o[7] else _font)
	var fsize := maxi(1, int(round(o[4] * _scale)))
	var s: String = o[3]
	var w := f.get_string_size(s, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize).x
	var dx := 0.0
	if o[6] == "c":
		dx = -w * 0.5
	elif o[6] == "r":
		dx = -w
	var pos := _p(o[1], o[2])
	if o[8] != 0.0:
		draw_set_transform(pos, deg_to_rad(o[8]), Vector2.ONE)
		draw_string(f, Vector2(dx, 0.0), s, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize, _col(o[5]))
		draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	else:
		draw_string(f, pos + Vector2(dx, 0.0), s, HORIZONTAL_ALIGNMENT_LEFT, -1, fsize, _col(o[5]))


## SVG art rasterised at the size it is drawn, cached per name/params/size.
func _draw_art(o: Array) -> void:
	var rect := Rect2(_p(o[2], o[3]), Vector2(o[4], o[5]) * _scale)
	if rect.size.x < 1.0 or rect.size.y < 1.0:
		return
	var params: Dictionary = o[6]
	var key := "%s|%s|%d|%d" % [o[1], str(params), int(rect.size.x), int(rect.size.y)]
	var tex: Texture2D = _textures.get(key)
	if tex == null:
		var svg := art.render(o[1], params)
		var vb := UiArt.view_box(svg)
		var img := Image.new()
		var k: float = rect.size.x / maxf(float(vb[2]), 1.0)
		if img.load_svg_from_string(svg, k) != OK:
			return
		tex = ImageTexture.create_from_image(img)
		if _textures.size() > MAX_TEXTURES:
			_textures.clear()
		_textures[key] = tex
	var rot: float = o[7]
	if rot != 0.0:
		draw_set_transform(rect.get_center(), deg_to_rad(rot), Vector2.ONE)
		draw_texture_rect(tex, Rect2(-rect.size * 0.5, rect.size), false)
		draw_set_transform(Vector2.ZERO, 0.0, Vector2.ONE)
	else:
		draw_texture_rect(tex, rect, false)


func _update_textgrid_rect() -> void:
	_layout()
	textgrid_rect = Rect2()
	for op in dl.ops:
		var o: Array = op
		if o[0] == "textgrid":
			textgrid_rect = Rect2(_p(o[1], o[2]), Vector2(o[3], o[4]) * _scale)


# -------------------------------------------------------------------- input
func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseMotion:
		var lp := _logical((event as InputEventMouseMotion).position)
		var h := _hotspot_at(lp)
		if h != _hover:
			_hover = h
			queue_redraw()
		mouse_default_cursor_shape = Control.CURSOR_POINTING_HAND if h.size() == 4 \
			else Control.CURSOR_ARROW
	elif event is InputEventMouseButton:
		var mb := event as InputEventMouseButton
		if mb.pressed and mb.button_index == MOUSE_BUTTON_LEFT:
			var action := dl.hit(_logical(mb.position).x, _logical(mb.position).y)
			if action.begins_with("key:"):
				key_action.emit(action.substr(4))
				accept_event()
			elif action.begins_with("ui:"):
				ui_action.emit(action.substr(3))
				accept_event()


func _logical(p: Vector2) -> Vector2:
	_layout()
	return (p - _origin) / _scale


func _hotspot_at(lp: Vector2) -> Array:
	for i in range(dl.hotspots.size() - 1, -1, -1):
		var h: Array = dl.hotspots[i]
		if lp.x >= h[0] and lp.y >= h[1] and lp.x < h[0] + h[2] and lp.y < h[1] + h[3]:
			return [h[0], h[1], h[2], h[3]]
	return []
