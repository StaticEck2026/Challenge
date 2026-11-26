## Screen rendering: text modes (CP437 + attributes, 8x16 cells) and CGA modes
## 4/5/6.  Port of tools/refemu/video.py, with incremental updates (only cells
## or bytes that changed since the previous frame are redrawn) because GDScript
## is too slow to repaint 256,000 pixels every frame.
class_name Video
extends RefCounted

const CP437_HIGH := ("ÇüéâäàåçêëèïîìÄÅÉæÆôöòûùÿÖÜ¢£¥₧ƒáíóúñÑªº¿⌐¬½¼¡«»░▒▓│┤╡╢╖╕╣║╗╝╜╛┐"
	+ "└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀αßΓπΣσµτΦΘΩδ∞φε∩≡±≥≤⌠⌡÷≈°∙·√ⁿ²■ ")
const CP437_LOW := " ☺☻♥♦♣♠•◘○◙♂♀♪♫☼►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
## CGA 320x200 palettes: [palette][intensity] -> colours for pixel values 1..3
const CGA4 := [[[2, 4, 6], [10, 12, 14]], [[3, 5, 7], [11, 13, 15]]]

var width: int = 640
var height: int = 400
var rgb: PackedByteArray = PackedByteArray()
var font16: PackedByteArray
var font8: PackedByteArray
var pal: Array = []
var shadow: PackedInt32Array = PackedInt32Array()
var last_mode: int = -1
var last_color_reg: int = -1
var last_cursor: int = -1
var last_blink: int = -1
var dirty: bool = true


func _init() -> void:
	font16 = FontCP437.font16()
	font8 = FontCP437.font8()
	for c in BIOS.CGA_PALETTE:
		var rgbv: int = c
		pal.append(PackedByteArray([(rgbv >> 16) & 0xFF, (rgbv >> 8) & 0xFF, rgbv & 0xFF]))


func _resize(w: int, h: int) -> void:
	width = w
	height = h
	rgb.resize(w * h * 3)
	rgb.fill(0)
	shadow.resize(16384)
	shadow.fill(-1)
	dirty = true


## Returns the text screen as a String (CP437 decoded), or a marker for graphics.
static func text_dump(m: Machine) -> String:
	var md := m.rb(BIOS.BDA_MODE)
	if md >= 4 and md <= 6:
		return "[graphics mode %d]" % md
	var ncols := m.rw(BIOS.BDA_COLS)
	if ncols == 0:
		ncols = 80
	var page := m.rb(BIOS.BDA_PAGE)
	var base := (0xB0000 if md == 7 else 0xB8000) + page * m.rw(BIOS.BDA_PAGESIZE)
	var lines := PackedStringArray()
	for row in range(25):
		var line := ""
		for col in range(ncols):
			line += cp437_char(m.cpu.mem[base + (row * ncols + col) * 2])
		lines.append(_rstrip_spaces(line))
	return "\n".join(lines)


## CP437 glyph as text: control codes as their PC glyphs, 0xFF as a space.
static func cp437_char(c: int) -> String:
	if c >= 128:
		return CP437_HIGH[c - 128]
	if c < 32:
		return CP437_LOW[c]
	if c == 127:
		return "⌂"
	return char(c)


static func _rstrip_spaces(s: String) -> String:
	var n := s.length()
	while n > 0 and s[n - 1] == " ":
		n -= 1
	return s.substr(0, n)


## Update `rgb` from emulated video memory.  Returns true if anything changed.
func render(m: Machine, blink_phase: int) -> bool:
	var md := m.rb(BIOS.BDA_MODE)
	var mem := m.cpu.mem
	if md != last_mode or m.cga_color_reg != last_color_reg:
		last_mode = md
		last_color_reg = m.cga_color_reg
		if md == 4 or md == 5:
			_resize(320, 200)
		elif md == 6:
			_resize(640, 200)
		else:
			var ncols := m.rw(BIOS.BDA_COLS)
			_resize((ncols if ncols else 80) * 8, 400)
	dirty = false
	if md == 4 or md == 5:
		_render_cga4(m, md)
	elif md == 6:
		_render_cga6(m)
	else:
		_render_text(m, md, blink_phase)
	return dirty


func _render_cga4(m: Machine, md: int) -> void:
	var creg := m.cga_color_reg
	var bg: PackedByteArray = pal[creg & 0x0F]
	var sel := 1 if creg & 0x20 else 0
	var inten := 1 if creg & 0x10 else 0
	var cols3: Array = CGA4[sel][inten]
	if md == 5:
		cols3 = [3, 4, 7] if not inten else [11, 12, 15]
	var lut := [bg, pal[cols3[0]], pal[cols3[1]], pal[cols3[2]]]
	var mem := m.cpu.mem
	for y in range(200):
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80
		var sidx := (y & 1) * 0x2000 + (y >> 1) * 80
		for xb in range(80):
			var b: int = mem[a + xb]
			if shadow[sidx + xb] == b:
				continue
			shadow[sidx + xb] = b
			dirty = true
			var o := (y * 320 + xb * 4) * 3
			for k in range(4):
				var c: PackedByteArray = lut[(b >> (6 - 2 * k)) & 3]
				rgb[o] = c[0]
				rgb[o + 1] = c[1]
				rgb[o + 2] = c[2]
				o += 3


func _render_cga6(m: Machine) -> void:
	var fgi := m.cga_color_reg & 0x0F
	var fg: PackedByteArray = pal[fgi if fgi else 15]
	var bg: PackedByteArray = pal[0]
	var mem := m.cpu.mem
	for y in range(200):
		var a := 0xB8000 + (y & 1) * 0x2000 + (y >> 1) * 80
		var sidx := (y & 1) * 0x2000 + (y >> 1) * 80
		for xb in range(80):
			var b: int = mem[a + xb]
			if shadow[sidx + xb] == b:
				continue
			shadow[sidx + xb] = b
			dirty = true
			var o := (y * 640 + xb * 8) * 3
			for bit in range(7, -1, -1):
				var c: PackedByteArray = fg if (b >> bit) & 1 else bg
				rgb[o] = c[0]
				rgb[o + 1] = c[1]
				rgb[o + 2] = c[2]
				o += 3


func _render_text(m: Machine, md: int, blink_phase: int) -> void:
	var ncols := m.rw(BIOS.BDA_COLS)
	if ncols == 0:
		ncols = 80
	var page := m.rb(BIOS.BDA_PAGE)
	var base := (0xB0000 if md == 7 else 0xB8000) + page * m.rw(BIOS.BDA_PAGESIZE)
	var blink_enabled := (m.cga_mode_reg & 0x20) != 0
	var cur := m.rw(BIOS.BDA_CURPOS + page * 2)
	var crow := cur >> 8
	var ccol := cur & 0xFF
	var cursor_visible := (m.rw(BIOS.BDA_CURSHAPE) & 0x2000) == 0
	var mem := m.cpu.mem
	var force_cursor := cur != last_cursor or blink_phase != last_blink
	var old_cursor := last_cursor
	last_cursor = cur
	var blink_changed := blink_phase != last_blink
	last_blink = blink_phase
	for row in range(25):
		for col in range(ncols):
			var idx := row * ncols + col
			var a := base + idx * 2
			var ch: int = mem[a]
			var at: int = mem[a + 1]
			var key := (ch << 8) | at
			var is_cursor := row == crow and col == ccol
			var was_cursor := old_cursor == ((row << 8) | col)
			if shadow[idx] == key and not (is_cursor and force_cursor) and not was_cursor:
				if not (blink_changed and (at & 0x80) and blink_enabled):
					continue
			shadow[idx] = key
			dirty = true
			var fgc := at & 0x0F
			var bgc := (at >> 4) & 0x0F
			if md == 7:
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
			var fg: PackedByteArray = pal[fgc]
			var bg: PackedByteArray = pal[bgc]
			for y in range(16):
				var bits: int = font16[ch * 16 + y]
				if is_cursor and cursor_visible and y >= 14 and blink_phase == 0:
					bits = 0xFF
				var o := ((row * 16 + y) * width + col * 8) * 3
				for x in range(8):
					var c: PackedByteArray = fg if bits & (0x80 >> x) else bg
					rgb[o] = c[0]
					rgb[o + 1] = c[1]
					rgb[o + 2] = c[2]
					o += 3
