## A frame of the Modern view as a flat list of vector drawing operations on
## the 1920 x 1080 logical canvas.
##
## The layout code (match_view.gd, screens.gd) only appends operations here.
## The same list is then
## * drawn by the Godot canvas (modern_view.gd: CanvasItem draw_* calls and
##   textures rasterised from the SVG art), or
## * written out as one SVG document by to_svg() (previews and tests:
##   tools/modern/render.py rasterises it with Chromium).
## Colours are "#rrggbb" or "#rrggbbaa" strings.  Text positions are the
## baseline; align is "l", "c" or "r".  Hotspots are clickable rectangles with
## an action string ("key:R", "ui:view", ...).
class_name DrawList
extends RefCounted

const FONT_UI := "Inter, 'DejaVu Sans', sans-serif"
const FONT_MONO := "'JetBrains Mono', 'DejaVu Sans Mono', monospace"
## Cap height of Inter in em: baseline = vertical centre + CAP * size / 2.
const CAP := 0.727

var ops: Array = []
var hotspots: Array = []
var width: float = 1920.0
var height: float = 1080.0
## Callable(text, size, bold, mono) -> float text width; Godot sets it to the
## real font metrics, the tests to the same fonts measured with FreeType.
var measurer: Callable = Callable()


func clear() -> void:
	ops = []
	hotspots = []


func rect(x: float, y: float, w: float, h: float, color: String) -> void:
	ops.append(["rect", x, y, w, h, color])


## Rounded rectangle; border 0 for none.
func rrect(x: float, y: float, w: float, h: float, r: float, color: String,
		border: float = 0.0, border_color: String = "") -> void:
	ops.append(["rrect", x, y, w, h, r, color, border, border_color])


func circle(cx: float, cy: float, r: float, color: String) -> void:
	ops.append(["circle", cx, cy, r, color])


func ring(cx: float, cy: float, r: float, w: float, color: String) -> void:
	ops.append(["ring", cx, cy, r, w, color])


func line(x1: float, y1: float, x2: float, y2: float, w: float, color: String) -> void:
	ops.append(["line", x1, y1, x2, y2, w, color])


## Filled polygon from a flat [x0, y0, x1, y1, ...] list.
func poly(points: Array, color: String) -> void:
	ops.append(["poly", points, color])


func text(x: float, y: float, s: String, size: float, color: String, align: String = "l",
		bold: bool = false, rot: float = 0.0, mono: bool = false) -> void:
	if s == "":
		return
	ops.append(["text", x, y, s, size, color, align, bold, rot, mono])


## Text vertically centred on cy.
func text_mid(x: float, cy: float, s: String, size: float, color: String, align: String = "l",
		bold: bool = false) -> void:
	text(x, cy + CAP * size * 0.5, s, size, color, align, bold)


## An SVG art file placed at (x, y, w, h) with placeholder values.
func art(art_name: String, x: float, y: float, w: float, h: float,
		params: Dictionary = {}) -> void:
	ops.append(["art", art_name, x, y, w, h, params])


## The original 80x25 text screen drawn in a rectangle (unrecognised screens).
func textgrid(x: float, y: float, w: float, h: float, rows: PackedStringArray,
		attrs: PackedByteArray) -> void:
	ops.append(["textgrid", x, y, w, h, rows, attrs])


func hot(x: float, y: float, w: float, h: float, action: String) -> void:
	hotspots.append([x, y, w, h, action])


## Action of the hotspot under a canvas point, or "".
func hit(px: float, py: float) -> String:
	for i in range(hotspots.size() - 1, -1, -1):
		var h: Array = hotspots[i]
		if px >= h[0] and py >= h[1] and px < h[0] + h[2] and py < h[1] + h[3]:
			return h[4]
	return ""


func measure(s: String, size: float, bold: bool = false, mono: bool = false) -> float:
	if measurer.is_valid():
		return measurer.call(s, size, bold, mono)
	return float(s.length()) * size * (0.6 if mono else (0.58 if bold else 0.55))


## Shortens s with an ellipsis so that it fits max_w.
func fit(s: String, size: float, max_w: float, bold: bool = false) -> String:
	if measure(s, size, bold) <= max_w:
		return s
	var t := s
	while t.length() > 1 and measure(t + "…", size, bold) > max_w:
		t = t.left(t.length() - 1)
	return t.strip_edges() + "…"


# ------------------------------------------------------------------ SVG export
static func esc(s: String) -> String:
	return s.xml_escape(true)


static func n(v: float) -> String:
	return UiArt.fmt(v)


## The whole frame as a standalone SVG document.
func to_svg(art_lib: UiArt, palette: Array = []) -> String:
	var out := PackedStringArray()
	out.append(("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"%s\" height=\"%s\" "
		+ "viewBox=\"0 0 %s %s\">") % [n(width), n(height), n(width), n(height)])
	for op in ops:
		var o: Array = op
		var kind: String = o[0]
		if kind == "rect":
			out.append("<rect x=\"%s\" y=\"%s\" width=\"%s\" height=\"%s\" fill=\"%s\"/>" % [
				n(o[1]), n(o[2]), n(o[3]), n(o[4]), o[5]])
		elif kind == "rrect":
			var stroke := ""
			if o[7] > 0.0:
				stroke = " stroke=\"%s\" stroke-width=\"%s\"" % [o[8], n(o[7])]
			out.append("<rect x=\"%s\" y=\"%s\" width=\"%s\" height=\"%s\" rx=\"%s\" fill=\"%s\"%s/>" % [
				n(o[1]), n(o[2]), n(o[3]), n(o[4]), n(o[5]), o[6], stroke])
		elif kind == "circle":
			out.append("<circle cx=\"%s\" cy=\"%s\" r=\"%s\" fill=\"%s\"/>" % [
				n(o[1]), n(o[2]), n(o[3]), o[4]])
		elif kind == "ring":
			out.append(("<circle cx=\"%s\" cy=\"%s\" r=\"%s\" fill=\"none\" stroke=\"%s\" "
				+ "stroke-width=\"%s\"/>") % [n(o[1]), n(o[2]), n(o[3]), o[5], n(o[4])])
		elif kind == "line":
			out.append(("<line x1=\"%s\" y1=\"%s\" x2=\"%s\" y2=\"%s\" stroke=\"%s\" "
				+ "stroke-width=\"%s\" stroke-linecap=\"round\"/>") % [
				n(o[1]), n(o[2]), n(o[3]), n(o[4]), o[6], n(o[5])])
		elif kind == "poly":
			var pts := PackedStringArray()
			var p: Array = o[1]
			for i in range(0, p.size(), 2):
				pts.append("%s,%s" % [n(p[i]), n(p[i + 1])])
			out.append("<polygon points=\"%s\" fill=\"%s\"/>" % [" ".join(pts), o[2]])
		elif kind == "text":
			out.append(_svg_text(o))
		elif kind == "art":
			out.append(art_lib.nested(o[1], o[6], o[2], o[3], o[4], o[5]))
		elif kind == "textgrid":
			out.append(_svg_textgrid(o, palette))
	out.append("</svg>")
	return "\n".join(out)


func _svg_text(o: Array) -> String:
	var anchor := "start"
	if o[6] == "c":
		anchor = "middle"
	elif o[6] == "r":
		anchor = "end"
	var weight := "700" if o[7] else "500"
	var family := FONT_MONO if o[9] else FONT_UI
	var tr := ""
	if o[8] != 0.0:
		tr = " transform=\"rotate(%s %s %s)\"" % [n(o[8]), n(o[1]), n(o[2])]
	return ("<text x=\"%s\" y=\"%s\" font-family=\"%s\" font-size=\"%s\" font-weight=\"%s\" "
		+ "fill=\"%s\" text-anchor=\"%s\"%s xml:space=\"preserve\">%s</text>") % [
		n(o[1]), n(o[2]), family, n(o[4]), weight, o[5], anchor, tr, esc(o[3])]


## The 80x25 text grid: background runs and text runs per row, in the same
## palette the HD text renderer uses.
func _svg_textgrid(o: Array, palette: Array) -> String:
	var x0: float = o[1]
	var y0: float = o[2]
	var gw: float = o[3]
	var gh: float = o[4]
	var rows: PackedStringArray = o[5]
	var attrs: PackedByteArray = o[6]
	var cw := gw / 80.0
	var ch := gh / 25.0
	var fs := minf(cw / 0.6, ch * 0.95)
	var out := PackedStringArray()
	out.append("<g>")
	for r in range(mini(rows.size(), 25)):
		var row: String = rows[r]
		var c := 0
		while c < 80:
			var at: int = attrs[r * 80 + c]
			var bg := (at >> 4) & 7
			var c1 := c
			while c1 < 80 and ((attrs[r * 80 + c1] >> 4) & 7) == bg:
				c1 += 1
			if bg != 0 and palette.size() > bg:
				out.append("<rect x=\"%s\" y=\"%s\" width=\"%s\" height=\"%s\" fill=\"%s\"/>" % [
					n(x0 + c * cw), n(y0 + r * ch), n((c1 - c) * cw + 0.5), n(ch + 0.5), palette[bg]])
			c = c1
		c = 0
		while c < 80:
			var fg: int = attrs[r * 80 + c] & 15
			var c1 := c
			while c1 < 80 and (attrs[r * 80 + c1] & 15) == fg:
				c1 += 1
			var seg := row.substr(c, c1 - c)
			if seg.strip_edges() != "" and palette.size() > fg:
				out.append(("<text x=\"%s\" y=\"%s\" font-family=\"%s\" font-size=\"%s\" fill=\"%s\" "
					+ "textLength=\"%s\" lengthAdjust=\"spacingAndGlyphs\" xml:space=\"preserve\">%s</text>") % [
					n(x0 + c * cw), n(y0 + r * ch + ch * 0.72), FONT_MONO, n(fs), palette[fg],
					n((c1 - c) * cw), esc(seg)])
			c = c1
	out.append("</g>")
	return "\n".join(out)
