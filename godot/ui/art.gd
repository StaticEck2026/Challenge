## The Modern view's SVG art (res://ui/art/*.svg), hand-authored flat vector.
##
## Art files may contain {{name}} placeholders (team colours, sizes); render()
## substitutes them.  The Godot canvas (modern_view.gd) rasterises the result
## with Image.load_svg_from_string() at the window's scale and caches the
## texture; the SVG export (DrawList.to_svg) inlines it as a nested <svg>.
class_name UiArt
extends RefCounted

const DIR := "res://ui/art/"

var _src: Dictionary = {}


func source(art_name: String) -> String:
	if not _src.has(art_name):
		_src[art_name] = FileAccess.get_file_as_string(DIR + art_name + ".svg")
	return _src[art_name]


## The SVG text of an art file with its placeholders filled in.
func render(art_name: String, params: Dictionary) -> String:
	var s := source(art_name)
	for k in params:
		s = s.replace("{{" + str(k) + "}}", fmt(params[k]))
	return s


static func fmt(v) -> String:
	if typeof(v) == TYPE_FLOAT:
		var f: float = v
		var s := "%.2f" % f
		return s.trim_suffix(".00")
	return str(v)


## [x, y, w, h] of the root viewBox.
static func view_box(svg: String) -> Array:
	var p := svg.find("viewBox=\"")
	if p < 0:
		return [0.0, 0.0, 1.0, 1.0]
	var q := svg.find("\"", p + 9)
	var parts := svg.substr(p + 9, q - p - 9).split(" ", false)
	var out := []
	for v in parts:
		out.append(v.to_float())
	while out.size() < 4:
		out.append(1.0)
	return out


## The art as a nested <svg> element placed at (x, y, w, h), for SVG export.
func nested(art_name: String, params: Dictionary, x: float, y: float, w: float, h: float) -> String:
	var s := render(art_name, params)
	var vb := view_box(s)
	var start := s.find("<svg")
	var open_end := s.find(">", start)
	var body_end := s.rfind("</svg>")
	var inner := s.substr(open_end + 1, body_end - open_end - 1)
	var root := s.substr(start, open_end - start)
	var keep := ""
	for attr in ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity"]:
		var v := attr_value(root, attr)
		if v != "":
			keep += " %s=\"%s\"" % [attr, v]
	return ("<svg x=\"%s\" y=\"%s\" width=\"%s\" height=\"%s\" viewBox=\"%s %s %s %s\" "
		+ "preserveAspectRatio=\"none\" overflow=\"visible\"><g%s>%s</g></svg>") % [
		fmt(x), fmt(y), fmt(w), fmt(h), fmt(vb[0]), fmt(vb[1]), fmt(vb[2]), fmt(vb[3]), keep, inner]


## Value of an attribute in an opening tag (exact name match), or "".
static func attr_value(tag: String, attr: String) -> String:
	var key := " " + attr + "=\""
	var p := tag.find(key)
	if p < 0:
		return ""
	var q := tag.find("\"", p + key.length())
	return tag.substr(p + key.length(), q - p - key.length())
