## Colours, sizes and team colour pairs of the Modern view.
##
## Everything is laid out on a 1920 x 1080 logical canvas; the Godot canvas
## (modern_view.gd) and the SVG export (draw_list.gd) scale it to the window.
## Team colours are plain colour pairs keyed by the team's short name as the
## game prints it (no logos); unknown teams get the neutral home blue / away red.
class_name UiTheme
extends RefCounted

const W := 1920.0
const H := 1080.0

const BG := "#0b0f14"
const BAR := "#10151c"
const BAR_LINE := "#1f2833"
const PANEL := "#151b23"
const CARD := "#1a212b"
const CARD_HI := "#222b37"
const CARD_LINE := "#2a3442"
const TEXT := "#eef2f7"
const TEXT_2 := "#a3adbb"
const TEXT_3 := "#6b7685"
const ACCENT := "#3b82f6"
const ACCENT_2 := "#60a5fa"
const GOOD := "#22c55e"
const WARN := "#facc15"
const LOS := "#3b82f6"
const FIRST_DOWN := "#facc15"
const GRASS_A := "#2f7d3c"
const GRASS_B := "#2a7236"
const GRASS_EDGE := "#235f2e"
const LINE := "#f4f7f1"

## The 16 text colours (the HD text renderer's modern palette) for the
## original text shown inside the modern frame.
const TEXT_PALETTE := [
	"#0c0f14", "#2f5fd0", "#2fae4f", "#23a9b5", "#c8343a", "#b04bc4", "#c8822b", "#c5cad3",
	"#5b6270", "#6ea0ff", "#6be08a", "#63e3ef", "#ff6b6b", "#e58cff", "#ffe066", "#f5f7fa"]

const HOME_DEFAULT := ["#2563eb", "#dbeafe"]
const AWAY_DEFAULT := ["#dc2626", "#fee2e2"]

## Plain colour pairs [primary, secondary] by short team name (lower case).
const TEAM_COLORS := {
	"bills": ["#1d4ed8", "#ef4444"], "colts": ["#1e40af", "#f8fafc"],
	"dolphins": ["#0d9488", "#f97316"], "patriots": ["#1e3a8a", "#dc2626"],
	"jets": ["#15803d", "#f8fafc"], "bengals": ["#ea580c", "#111827"],
	"browns": ["#7c2d12", "#f97316"], "oilers": ["#38bdf8", "#dc2626"],
	"steelers": ["#1f2937", "#facc15"], "broncos": ["#f97316", "#1e3a8a"],
	"chiefs": ["#dc2626", "#facc15"], "raiders": ["#374151", "#d1d5db"],
	"chargers": ["#2563eb", "#facc15"], "seahawks": ["#1e3a8a", "#22c55e"],
	"cowboys": ["#1e3a8a", "#cbd5e1"], "giants": ["#1d4ed8", "#dc2626"],
	"eagles": ["#065f46", "#cbd5e1"], "cards": ["#b91c1c", "#f8fafc"],
	"redskins": ["#7f1d1d", "#facc15"], "bears": ["#1e293b", "#f97316"],
	"lions": ["#0284c7", "#cbd5e1"], "packers": ["#166534", "#facc15"],
	"vikings": ["#6d28d9", "#facc15"], "bucs": ["#ea580c", "#b91c1c"],
	"falcons": ["#b91c1c", "#111827"], "rams": ["#1d4ed8", "#facc15"],
	"saints": ["#a16207", "#111827"], "49ers": ["#b91c1c", "#d4a017"],
}


## [primary, secondary] for a team; team 1 = home, 2 = visitor.
static func team_colors(short_name: String, team: int) -> Array:
	var key := short_name.strip_edges().to_lower()
	if TEAM_COLORS.has(key):
		return TEAM_COLORS[key]
	return HOME_DEFAULT if team == 1 else AWAY_DEFAULT


## Disc styles ("kits") for both teams: [home, away], each
## {"fill", "rim", "text", "primary"}.  If the primaries are close, the
## visitor wears white with its colour as rim and number, as road teams do.
static func match_colors(home: String, away: String) -> Array:
	var h: Array = team_colors(home, 1)
	var a: Array = team_colors(away, 2)
	var hk := kit(h[0], shade(h[0], 0.62), on_color(h[0]), h[0])
	var ak := kit(a[0], shade(a[0], 0.62), on_color(a[0]), a[0])
	if color_distance(h[0], a[0]) < 90.0:
		ak = kit("#f8fafc", a[0], a[0], a[0])
	return [hk, ak]


static func kit(fill: String, rim: String, txt: String, primary: String) -> Dictionary:
	return {"fill": fill, "rim": rim, "text": txt, "primary": primary}


static func rgb(hex: String) -> Array:
	var s := hex.trim_prefix("#")
	return [GameState._hex(s.substr(0, 2)), GameState._hex(s.substr(2, 2)),
		GameState._hex(s.substr(4, 2))]


static func color_distance(a: String, b: String) -> float:
	var x := rgb(a)
	var y := rgb(b)
	var d := 0.0
	for i in range(3):
		var k: float = float(x[i]) - float(y[i])
		d += k * k
	return sqrt(d)


static func luminance(hex: String) -> float:
	var c := rgb(hex)
	return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


## White or near-black text, whichever reads better on `bg`.
static func on_color(bg: String) -> String:
	return "#111827" if luminance(bg) > 0.62 else "#ffffff"


## Darker shade of a colour (outline of a disc).
static func shade(hex: String, f: float) -> String:
	var c := rgb(hex)
	var out := "#"
	for i in range(3):
		var v := clampi(int(float(c[i]) * f), 0, 255)
		out += _hex2(v)
	return out


static func _hex2(v: int) -> String:
	var digits := "0123456789abcdef"
	return digits[v >> 4] + digits[v & 15]


## Three-letter team code from the game's short name: its first three
## letters (COL, BIL); if the two teams of a match would share a code, both
## use the first letter and the next two consonants (Browns BRW, Broncos BRN).
static func abbrev(short_name: String, other: String = "") -> String:
	var s := short_name.strip_edges().to_upper()
	var o := other.strip_edges().to_upper()
	if s.length() <= 3:
		return s
	if o == "" or o.substr(0, 3) != s.substr(0, 3):
		return s.substr(0, 3)
	return consonant_code(s)


static func consonant_code(s: String) -> String:
	var out := s.substr(0, 1)
	var rest := ""
	for i in range(1, s.length()):
		var ch := s.substr(i, 1)
		if ch == " ":
			continue
		if "AEIOU".contains(ch):
			rest += ch
		elif out.length() < 3:
			out += ch
	if out.length() < 3:
		out += rest.substr(0, 3 - out.length())
	return out
