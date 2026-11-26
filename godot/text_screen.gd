## HD text renderer: draws the emulated 80x25 / 40x25 text screen with a vector
## font at the window's native resolution, instead of upscaling a 640x400 bitmap.
##
## * The grid fills the control (e.g. 1920x1080 -> 24 x 43.2 px cells), with
##   edges snapped to whole pixels so neighbouring cells never leave seams.
## * Box-drawing (single/double lines), block and shade characters are drawn
##   as rectangles spanning the full cell, so frames join up at any cell size.
##   A font's own box glyphs would leave gaps between cells.
## * Every other CP437 character is drawn from the font (JetBrains Mono, with
##   DejaVu Sans Mono as the fallback for PC symbols such as faces and suits).
## * It only redraws when video memory, cursor or blink phase changes.
##
## tools/preview_hd.py mirrors this file (same layout and box-drawing rules) so
## the output can be checked without running Godot.
class_name TextScreen
extends Control

## Arms of the CP437 box-drawing characters 0xB3..0xDA, as 4 digits
## up/down/left/right with 0 = none, 1 = single line, 2 = double line.
const BOX := {
	0xB3: "1100", 0xB4: "1110", 0xB5: "1120", 0xB6: "2210", 0xB7: "0210",
	0xB8: "0120", 0xB9: "2220", 0xBA: "2200", 0xBB: "0220", 0xBC: "2020",
	0xBD: "2010", 0xBE: "1020", 0xBF: "0110", 0xC0: "1001", 0xC1: "1011",
	0xC2: "0111", 0xC3: "1101", 0xC4: "0011", 0xC5: "1111", 0xC6: "1102",
	0xC7: "2201", 0xC8: "2002", 0xC9: "0202", 0xCA: "2022", 0xCB: "0222",
	0xCC: "2202", 0xCD: "0022", 0xCE: "2222", 0xCF: "1022", 0xD0: "2011",
	0xD1: "0122", 0xD2: "0211", 0xD3: "2001", 0xD4: "1002", 0xD5: "0102",
	0xD6: "0201", 0xD7: "2211", 0xD8: "1122", 0xD9: "1010", 0xDA: "0101",
}

## A softer, more legible take on the 16 CGA colours (same hues and meaning).
const MODERN_PALETTE := [
	0x0C0F14, 0x2F5FD0, 0x2FAE4F, 0x23A9B5, 0xC8343A, 0xB04BC4, 0xC8822B, 0xC5CAD3,
	0x5B6270, 0x6EA0FF, 0x6BE08A, 0x63E3EF, 0xFF6B6B, 0xE58CFF, 0xFFE066, 0xF5F7FA]

## Baseline offset below the cell centre, in em (see _draw).
const BASELINE_EM := 0.32

var machine: Machine = null
var blink_phase: int = 0
var modern_colors: bool = false
var font: Font = null
## Largest cell width/height ratio, so very wide windows don't stretch the grid.
var max_cell_aspect: float = 0.75

var _colors: Array[Color] = []
var _snap: PackedInt32Array = PackedInt32Array()
var _cols: int = 80
var _mode: int = 3


func _init() -> void:
	mouse_filter = Control.MOUSE_FILTER_IGNORE
	texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR
	var main_font := _load_font("res://fonts/JetBrainsMono-Medium.ttf")
	var fallback := _load_font("res://fonts/DejaVuSansMono.ttf")
	for f in [main_font, fallback]:
		var ff: FontFile = f
		ff.antialiasing = TextServer.FONT_ANTIALIASING_GRAY
		ff.hinting = TextServer.HINTING_LIGHT
		ff.subpixel_positioning = TextServer.SUBPIXEL_POSITIONING_DISABLED
		ff.generate_mipmaps = false
	var fallbacks: Array[Font] = [fallback]
	main_font.fallbacks = fallbacks
	font = main_font
	set_modern_colors(false)
	resized.connect(queue_redraw)


## The imported font resource (works in exports); falls back to reading the TTF.
static func _load_font(path: String) -> FontFile:
	var f := load(path) as FontFile
	if f == null:
		f = FontFile.new()
		f.load_dynamic_font(path)
	return f


func set_modern_colors(on: bool) -> void:
	modern_colors = on
	_colors.clear()
	var src: Array = MODERN_PALETTE if on else BIOS.CGA_PALETTE
	for c in src:
		var v: int = c
		_colors.append(Color8((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))
	queue_redraw()


## Takes a snapshot of the text page; queues a redraw if anything visible changed.
## Returns true when a redraw was queued.
func refresh(m: Machine, blink: int) -> bool:
	machine = m
	var md := m.rb(BIOS.BDA_MODE)
	var ncols := m.rw(BIOS.BDA_COLS)
	if ncols == 0:
		ncols = 80
	var page := m.rb(BIOS.BDA_PAGE)
	var base := (0xB0000 if md == 7 else 0xB8000) + page * m.rw(BIOS.BDA_PAGESIZE)
	var n := ncols * 25
	var mem := m.cpu.mem
	var changed := md != _mode or ncols != _cols or _snap.size() != n + 3
	if _snap.size() != n + 3:
		_snap.resize(n + 3)
	for i in range(n):
		var w: int = mem[base + i * 2] | (mem[base + i * 2 + 1] << 8)
		if _snap[i] != w:
			_snap[i] = w
			changed = true
	var cur := m.rw(BIOS.BDA_CURPOS + page * 2)
	var shape := m.rw(BIOS.BDA_CURSHAPE)
	var blink_on := 1 if (m.cga_mode_reg & 0x20) != 0 else 0
	var extra := [cur, shape, (blink << 1) | blink_on]
	for j in range(3):
		if _snap[n + j] != extra[j]:
			_snap[n + j] = extra[j]
			changed = true
	_mode = md
	_cols = ncols
	blink_phase = blink
	if changed:
		queue_redraw()
	return changed


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, size), _colors[0])
	var n := _cols * 25
	if _snap.size() != n + 3 or size.x < 8.0 or size.y < 8.0:
		return
	var cell_h := size.y / 25.0
	var cell_w: float = minf(size.x / _cols, cell_h * max_cell_aspect)
	var ox: float = floorf((size.x - cell_w * _cols) * 0.5)
	# Font size: glyph advance fills 98 % of the cell width (natural monospace
	# spacing), capped so caps + descenders fit the cell height.
	var adv: float = font.get_char_size(0x4D, 100).x / 100.0
	var fsize := int(minf(cell_w * 0.98 / adv, cell_h * 0.95))
	fsize = maxi(fsize, 6)
	# Baseline that centres capitals (cap height ~0.73 em in JetBrains Mono)
	# while leaving room for descenders.
	var baseline_off := roundf(cell_h * 0.5 + fsize * BASELINE_EM)
	var glyph_w := font.get_char_size(0x4D, fsize).x
	var blink_enabled := (_snap[n + 2] & 1) != 0
	var line_t: float = maxf(1.0, roundf(cell_w * 0.09))
	var gap: float = maxf(line_t + 1.0, roundf(cell_w * 0.17))
	for row in range(25):
		var y0 := roundf(row * cell_h)
		var y1 := roundf((row + 1) * cell_h)
		for col in range(_cols):
			var x0 := ox + roundf(col * cell_w)
			var x1 := ox + roundf((col + 1) * cell_w)
			var w: int = _snap[row * _cols + col]
			var ch := w & 0xFF
			var fg_bg := _attr_colors(w >> 8, blink_enabled)
			var cell := Rect2(x0, y0, x1 - x0, y1 - y0)
			if fg_bg[1] != 0:
				draw_rect(cell, _colors[fg_bg[1]])
			if ch == 0 or ch == 0x20 or ch == 0xFF or fg_bg[0] == fg_bg[1]:
				continue
			var fg: Color = _colors[fg_bg[0]]
			if _draw_block(ch, cell, fg, _colors[fg_bg[1]], line_t, gap):
				continue
			var pos := Vector2(x0 + floorf((cell.size.x - glyph_w) * 0.5), y0 + baseline_off)
			draw_char(font, pos, Video.cp437_char(ch), fsize, fg)
	_draw_cursor(ox, cell_w, cell_h, blink_enabled)


## [fg, bg] palette indexes for an attribute byte (mono mode 7 and blink aware).
func _attr_colors(at: int, blink_enabled: bool) -> PackedInt32Array:
	var fgc := at & 0x0F
	var bgc := (at >> 4) & 0x0F
	if _mode == 7:
		fgc = 15 if at & 0x08 else 7
		bgc = 0
		if (at & 0x77) == 0x70:
			fgc = 0
			bgc = 7
		elif (at & 0x07) == 0:
			fgc = 0
	elif blink_enabled:
		bgc &= 7
		if at & 0x80 and blink_phase:
			fgc = bgc
	return PackedInt32Array([fgc, bgc])


func _draw_cursor(ox: float, cell_w: float, cell_h: float, blink_enabled: bool) -> void:
	var n := _cols * 25
	var cur := _snap[n]
	var shape := _snap[n + 1]
	if (shape & 0x2000) != 0 or blink_phase != 0:
		return
	var row := cur >> 8
	var col := cur & 0xFF
	if row >= 25 or col >= _cols:
		return
	var x0 := ox + roundf(col * cell_w)
	var x1 := ox + roundf((col + 1) * cell_w)
	var y1 := roundf((row + 1) * cell_h)
	var th: float = maxf(2.0, roundf(cell_h * 0.10))
	var at := _snap[row * _cols + col] >> 8
	var fg_bg := _attr_colors(at, blink_enabled)
	draw_rect(Rect2(x0, y1 - th - roundf(cell_h * 0.06), x1 - x0, th), _colors[fg_bg[0]])


## Draws block, shade and box-drawing characters. Returns false for other chars.
func _draw_block(ch: int, cell: Rect2, fg: Color, bg: Color, t: float, g: float) -> bool:
	var x0 := cell.position.x
	var y0 := cell.position.y
	var w := cell.size.x
	var h := cell.size.y
	if ch >= 0xB0 and ch <= 0xB2:
		draw_rect(cell, bg.lerp(fg, [0.25, 0.5, 0.75][ch - 0xB0]))
		return true
	if ch == 0xDB:
		draw_rect(cell, fg)
		return true
	if ch == 0xDC:
		var hh := roundf(h * 0.5)
		draw_rect(Rect2(x0, y0 + hh, w, h - hh), fg)
		return true
	if ch == 0xDF:
		draw_rect(Rect2(x0, y0, w, roundf(h * 0.5)), fg)
		return true
	if ch == 0xDD:
		draw_rect(Rect2(x0, y0, roundf(w * 0.5), h), fg)
		return true
	if ch == 0xDE:
		var hw := roundf(w * 0.5)
		draw_rect(Rect2(x0 + hw, y0, w - hw, h), fg)
		return true
	if ch == 0xFE:
		var s := roundf(minf(w, h) * 0.5)
		draw_rect(Rect2(x0 + roundf((w - s) * 0.5), y0 + roundf((h - s) * 0.5), s, s), fg)
		return true
	if not BOX.has(ch):
		return false
	var spec: String = BOX[ch]
	var arms := PackedInt32Array([spec.unicode_at(0) - 48, spec.unicode_at(1) - 48,
		spec.unicode_at(2) - 48, spec.unicode_at(3) - 48])
	var cx := x0 + roundf(w * 0.5)
	var cy := y0 + roundf(h * 0.5)
	for seg in box_segments(arms, cx, cy, x0, y0, x0 + w, y0 + h, t, g):
		draw_rect(seg, fg)
	return true


## Rectangles for a box-drawing character. arms = [up, down, left, right] with
## 0 none / 1 single / 2 double; (cx, cy) is the cell centre, t the line
## thickness, g the offset of each line of a double line from the centre.
static func box_segments(arms: PackedInt32Array, cx: float, cy: float, left: float,
		top: float, right: float, bottom: float, t: float, g: float) -> Array[Rect2]:
	var out: Array[Rect2] = []
	var ht: float = floorf(t * 0.5)
	# direction: 0 up, 1 down, 2 left, 3 right. perpendicular pairs and sides.
	for d in range(4):
		var kind := arms[d]
		if kind == 0:
			continue
		var vertical := d < 2
		var opp := d ^ 1
		var p0 := 2 if vertical else 0      # perpendicular arm on the "minus" side
		var p1 := 3 if vertical else 1      # perpendicular arm on the "plus" side
		var perp_doubles := int(arms[p0] == 2) + int(arms[p1] == 2)
		var offsets: Array = [0.0] if kind == 1 else [-g, g]
		for k in range(offsets.size()):
			var off: float = offsets[k]
			# stop: how far past the centre (towards the far side) the line reaches;
			# negative stops short of the centre.
			var stop := 0.0
			if kind == 1:
				if arms[opp] != 0:
					stop = 0.0
				elif perp_doubles == 2:
					stop = -g
				elif perp_doubles == 1:
					stop = g
			else:
				var side := p0 if off < 0.0 else p1
				if arms[side] == 2:
					stop = -g
				elif arms[side] == 1:
					stop = 0.0
				elif arms[opp] != 0:
					stop = 0.0
				elif perp_doubles > 0:
					stop = g
				else:
					stop = 0.0
			stop += ht
			if vertical:
				var x := cx + off - ht
				if d == 0:
					out.append(Rect2(x, top, t, cy + stop - top + (t - ht * 2.0)))
				else:
					var ys := cy - stop
					out.append(Rect2(x, ys, t, bottom - ys))
			else:
				var y := cy + off - ht
				if d == 2:
					out.append(Rect2(left, y, cx + stop - left + (t - ht * 2.0), t))
				else:
					var xs := cx - stop
					out.append(Rect2(xs, y, right - xs, t))
	return out
