## Screen classifier and modern layouts for every screen of the game.
##
## classify() recognises the screen from the text in video memory (and the
## video mode); build() lays it out as modern cards, lists and buttons.  All
## content comes from the screen text or GameState, never from built-in game
## data: a team list is the list the game shows, an option is the line the
## game prints.  Options that the game asks for with a key ("Press R for
## Right keypad", "1  Team disk in drive A") become buttons whose hotspot
## injects that same key ("key:R", "key:1").  A screen that is not recognised
## is drawn as the original text (HD text renderer) inside a modern frame.
class_name Screens
extends RefCounted

const TOP := 52.0

var art: UiArt = null
var match_view: MatchView = null
## Last classification and its windows (for tests).
var last_id: String = ""
var last_windows: Array = []


func _init(p_art: UiArt = null) -> void:
	art = p_art if p_art != null else UiArt.new()
	match_view = MatchView.new(art)


# ---------------------------------------------------------------- classify
func classify(gs: GameState) -> String:
	if gs.graphics:
		return "play" if gs.play_active else "graphics"
	var t := gs.screen_text()
	if gs.field_up:
		return "field"
	if t.contains("CONFIGURATION"):
		return "config"
	if t.contains("Choose the home team") or t.contains("Choose the visiting team") \
			or t.contains("Choose the HOME team") or t.contains("Choose the VISITING team") \
			or (t.contains("A F C") and t.contains("N F C")):
		return "teams"
	var head := gs.text_at(0, 0, 80) + gs.text_at(1, 0, 80)
	if head.contains("NFL Challenge") and t.contains("Coach:"):
		return "matchup"
	if t.contains("Copyright") and t.contains("XOR"):
		return "title"
	if t.contains("TEAM STATISTICS"):
		return "stats"
	if t.contains("PgUp and PgDn") and t.contains(" - ") and t.contains("No."):
		return "player_stats"
	if t.strip_edges() == "":
		return "blank"
	if t.count("*") > 200:
		return "splash"
	return "text"


## Lays out the current screen; returns its id.
func build(dl: DrawList, gs: GameState, ui: Dictionary) -> String:
	dl.clear()
	var id := classify(gs)
	last_id = id
	last_windows = []
	if id == "field":
		match_view.build(dl, gs, ui)
		_field_windows(dl, gs)
	elif id == "play":
		match_view.build(dl, gs, ui)
	elif id == "config":
		_config(dl, gs, ui)
	elif id == "teams":
		_teams(dl, gs, ui)
	elif id == "matchup":
		_matchup(dl, gs, ui)
	elif id == "stats":
		_stats(dl, gs, ui)
	elif id == "player_stats":
		_player_stats(dl, gs, ui)
	elif id == "title" or id == "splash" or id == "graphics" or id == "blank":
		_title(dl, gs, ui, id)
	else:
		_fallback(dl, gs, ui)
	return id


# ------------------------------------------------------------------ chrome
func frame(dl: DrawList, ui: Dictionary, subtitle: String) -> void:
	dl.rect(0.0, 0.0, UiTheme.W, UiTheme.H, UiTheme.BG)
	var u := ui.duplicate()
	u["subtitle"] = subtitle
	match_view.toolbar(dl, u)


func card(dl: DrawList, x: float, y: float, w: float, h: float, accent: String = "",
		accent_h: float = 0.0) -> void:
	var a := accent if accent != "" else UiTheme.CARD
	var ah := accent_h if accent != "" else 0.0
	dl.art("card", x, y, w, h, {"W": w, "H": h, "R": 14.0, "W1": w - 1.5, "H1": h - 1.5,
		"fill": UiTheme.CARD, "accent": a, "AH": maxf(ah, 0.0), "AH2": maxf(ah - 14.0, 0.0)})


## A full-width option row with a key badge; clicking it types the key.
func option_row(dl: DrawList, x: float, y: float, w: float, h: float, key: String, label: String,
		selected: bool = false) -> void:
	var fill := UiTheme.CARD_HI if not selected else "#1e3a8a"
	dl.rrect(x, y, w, h, 10.0, fill, 1.0, UiTheme.CARD_LINE if not selected else UiTheme.ACCENT)
	if key != "":
		dl.rrect(x + 12.0, y + (h - 34.0) * 0.5, 34.0, 34.0, 8.0, UiTheme.ACCENT)
		dl.text_mid(x + 29.0, y + h * 0.5, key, 18.0, "#ffffff", "c", true)
		dl.hot(x, y, w, h, "key:" + key)
	dl.text_mid(x + (60.0 if key != "" else 18.0), y + h * 0.5, label, 20.0, UiTheme.TEXT, "l", false)


func key_button(dl: DrawList, x: float, y: float, w: float, h: float, key: String, label: String,
		primary: bool = true) -> void:
	dl.rrect(x, y, w, h, 12.0, UiTheme.ACCENT if primary else UiTheme.CARD_HI, 1.0,
		UiTheme.ACCENT_2 if primary else UiTheme.CARD_LINE)
	var kw := maxf(32.0, dl.measure(key, 15.0, true) + 18.0)
	dl.rrect(x + 12.0, y + (h - 32.0) * 0.5, kw, 32.0, 8.0, "#ffffff2e")
	dl.text_mid(x + 12.0 + kw * 0.5, y + h * 0.5, key, 15.0 if key.length() > 1 else 18.0, "#ffffff",
		"c", true)
	dl.text_mid(x + 12.0 + kw + 14.0, y + h * 0.5, label, 19.0, "#ffffff", "l", true)
	dl.hot(x, y, w, h, "key:" + key)


# ------------------------------------------------------------ text parsing
## Boxes drawn with box characters: [{top, left, bottom, right, title, lines}].
static func find_boxes(rows: PackedStringArray) -> Array:
	var out := []
	for r in range(rows.size()):
		var row: String = rows[r]
		var c := 0
		while c < row.length():
			var ch := row.substr(c, 1)
			if ch == "╔" or ch == "┌":
				var dbl := ch == "╔"
				var tr := "╗" if dbl else "┐"
				var bl := "╚" if dbl else "└"
				var right := row.find(tr, c + 1)
				if right > c:
					var bottom := -1
					for r2 in range(r + 1, rows.size()):
						if rows[r2].substr(c, 1) == bl:
							bottom = r2
							break
					if bottom > r:
						var lines := []
						for r3 in range(r + 1, bottom):
							lines.append(rows[r3].substr(c + 1, right - c - 1))
						var title := row.substr(c + 1, right - c - 1)
						title = title.replace("═", " ").replace("─", " ").strip_edges()
						out.append({"top": r, "left": c, "bottom": bottom, "right": right,
							"title": title, "lines": lines, "double": dbl})
						c = right
			c += 1
	return out


## Numbered options in text lines: "1  Team disk in drive A" -> ["1", "Team..."].
static func numbered(lines: Array) -> Array:
	var out := []
	for l in lines:
		var s: String = str(l).strip_edges()
		if s.length() >= 3:
			var k := 0
			while k < s.length() and k < 2 and "0123456789".contains(s.substr(k, 1)):
				k += 1
			if k > 0 and s.substr(k, 1) == " ":
				out.append([s.substr(0, k), s.substr(k).strip_edges()])
	return out


## "Press R for Right keypad, L for Left keypad" -> [["R", "Right keypad"], ["L", "Left keypad"]]
static func key_prompts(s: String) -> Array:
	var out := []
	var t := s.strip_edges().trim_prefix("Press ").replace(" or ", ", ")
	for part in t.split(",", false):
		var p := str(part).strip_edges()
		var f := p.find(" for ")
		if f > 0 and f <= 7:
			var key := p.substr(0, f).strip_edges().trim_prefix("<").trim_suffix(">")
			if key.length() == 1:
				out.append([key.to_upper(), p.substr(f + 5).strip_edges()])
	return out


# ----------------------------------------------------------------- screens
func _config(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "Setup")
	var boxes := find_boxes(gs.rows)
	var lines: Array = boxes[0]["lines"] if boxes.size() > 0 else []
	var title := ""
	var desc := []
	var opts := numbered(lines)
	var footer := ""
	for l in lines:
		var s: String = str(l).strip_edges()
		if s == "":
			continue
		if title == "":
			title = s
		elif numbered([s]).size() == 0:
			if s.begins_with("Press"):
				footer = s
			else:
				desc.append(s)
	var w := 760.0
	var h := 150.0 + opts.size() * 68.0 + (64.0 if footer != "" else 16.0)
	var x := (UiTheme.W - w) * 0.5
	var y := maxf(TOP + 40.0, (UiTheme.H - h) * 0.5)
	card(dl, x, y, w, h)
	dl.rect(x + 40.0, y + 30.0, 36.0, 4.0, UiTheme.ACCENT)
	dl.text(x + 40.0, y + 72.0, title.capitalize(), 30.0, UiTheme.TEXT, "l", true)
	dl.text(x + 40.0, y + 110.0, " ".join(PackedStringArray(desc)), 19.0, UiTheme.TEXT_2)
	var oy := y + 140.0
	for o in opts:
		option_row(dl, x + 32.0, oy, w - 64.0, 56.0, o[0], o[1])
		oy += 68.0
	if footer != "":
		dl.text(x + 40.0, oy + 30.0, footer, 16.0, UiTheme.TEXT_3)
		if footer.contains("Esc"):
			key_button(dl, x + w - 32.0 - 150.0, oy + 4.0, 150.0, 40.0, "Esc", "Back", false)
	last_windows = boxes


func _title(dl: DrawList, gs: GameState, ui: Dictionary, id: String) -> void:
	frame(dl, ui, "")
	var cxm := UiTheme.W * 0.5
	dl.art("mark", cxm - 48.0, 300.0, 96.0, 96.0)
	dl.text(cxm, 470.0, "NFL Challenge", 64.0, UiTheme.TEXT, "c", true)
	var sub := ""
	var status := []
	if id == "title":
		for r in range(gs.rows.size()):
			var s: String = gs.rows[r].strip_edges()
			if s.contains("Copyright") or s.begins_with("Version"):
				sub += ("   ·   " if sub != "" else "") + s
		for b in find_boxes(gs.rows):
			for l in b["lines"]:
				var s2: String = str(l).replace("►", " ").strip_edges()
				if s2 != "":
					status.append(s2)
	dl.text(cxm, 520.0, sub if sub != "" else "Football simulation · 1989", 20.0, UiTheme.TEXT_2, "c")
	var y := 600.0
	for s3 in status:
		dl.rrect(cxm - 300.0, y, 600.0, 44.0, 10.0, UiTheme.CARD, 1.0, UiTheme.CARD_LINE)
		dl.circle(cxm - 276.0, y + 22.0, 5.0, UiTheme.GOOD)
		dl.text_mid(cxm - 258.0, y + 22.0, s3, 18.0, UiTheme.TEXT)
		y += 54.0


## Team selection: the game's AFC/NFC division boxes as cards, one row of
## three division cards per conference; the game's cursor (►) is the
## highlighted row.
func _teams(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "Team selection")
	var prompt := ""
	var picked := ""
	var prompt_row := -1
	for r in range(3):
		var s: String = gs.rows[r]
		if s.contains("Choose"):
			var p := s.find("Choose")
			var e := s.find("∙", p)
			prompt = s.substr(p, (e - p) if e > p else 60).strip_edges().trim_suffix(".")
			prompt_row = r
	for r2 in range(maxi(prompt_row, 0)):
		var s2: String = gs.rows[r2].strip_edges()
		if s2 != "" and not s2.contains("Games:"):
			picked = s2
	dl.text(UiTheme.W * 0.5, TOP + 70.0, prompt, 34.0, UiTheme.TEXT, "c", true)
	if picked != "":
		var label := "Home team"
		var pw := dl.measure(label + "   " + picked, 18.0, true) + 48.0
		var px := (UiTheme.W - pw) * 0.5
		dl.rrect(px, TOP + 92.0, pw, 36.0, 18.0, UiTheme.CARD, 1.0, UiTheme.CARD_LINE)
		dl.text_mid(px + 24.0, TOP + 110.0, label, 16.0, UiTheme.TEXT_3, "l", true)
		dl.text_mid(px + 24.0 + dl.measure(label + "   ", 16.0, true), TOP + 110.0, picked, 18.0,
			UiTheme.TEXT, "l", true)
	var boxes := find_boxes(gs.rows)
	last_windows = boxes
	var margin := 120.0
	var gap := 24.0
	var cw := (UiTheme.W - 2.0 * margin - 2.0 * gap) / 3.0
	var row_y := [TOP + 200.0, TOP + 545.0]
	var heads := ["AFC", "NFC"]
	var count := [0, 0]
	for side in range(2):
		dl.text(margin, row_y[side] - 18.0, heads[side], 22.0, UiTheme.ACCENT_2, "l", true)
		var rule_w := UiTheme.W - 2.0 * margin - 64.0
		dl.rect(margin + 64.0, row_y[side] - 26.0, rule_w, 1.0, UiTheme.CARD_LINE)
	for b in boxes:
		var side := 0 if int(b["left"]) < 40 else 1
		var div := gs.text_at(int(b["top"]) - 1, int(b["left"]), int(b["right"]) - int(b["left"]) + 1)
		div = div.strip_edges()
		var names := []
		var sel := -1
		for k in range(b["lines"].size()):
			var nm := str(b["lines"][k])
			if nm.contains("►"):
				sel = names.size()
			nm = nm.replace("►", " ").strip_edges()
			if nm != "":
				names.append(nm)
		var x: float = margin + count[side] * (cw + gap)
		var y: float = row_y[side]
		count[side] += 1
		var h := 58.0 + 5.0 * 46.0
		card(dl, x, y, cw, h)
		dl.text(x + 24.0, y + 38.0, div if div != "" else "Division", 18.0, UiTheme.TEXT_2, "l", true)
		for k2 in range(names.size()):
			var ry := y + 54.0 + k2 * 46.0
			var nm2: String = names[k2]
			var short := nm2.get_slice(" ", nm2.count(" "))
			var cols: Array = UiTheme.team_colors(short, 1 + side)
			var on := k2 == sel
			if on:
				dl.rrect(x + 10.0, ry, cw - 20.0, 42.0, 10.0, "#1e3a8a", 1.5, UiTheme.ACCENT)
			dl.circle(x + 34.0, ry + 21.0, 10.0, cols[0])
			dl.ring(x + 34.0, ry + 21.0, 10.0, 2.0, cols[1])
			dl.text_mid(x + 56.0, ry + 21.0, dl.fit(nm2, 19.0, cw - 150.0, on), 19.0,
				UiTheme.TEXT if on else UiTheme.TEXT_2, "l", on)
			if on:
				dl.rrect(x + cw - 92.0, ry + 8.0, 70.0, 26.0, 8.0, UiTheme.ACCENT)
				dl.text_mid(x + cw - 57.0, ry + 21.0, "Enter", 14.0, "#ffffff", "c", true)
	_nav_keys(dl, UiTheme.H - 84.0)


## Arrow and Enter keys the list screens accept, as buttons.
func _nav_keys(dl: DrawList, y: float) -> void:
	var keys := [["Up", "↑"], ["Down", "↓"], ["Left", "←"], ["Right", "→"], ["Enter", "Enter"],
		["Esc", "Esc"]]
	var total := 0.0
	for k in keys:
		total += maxf(56.0, dl.measure(k[1], 18.0, true) + 36.0) + 10.0
	var x := (UiTheme.W - total) * 0.5
	for k in keys:
		var w := maxf(56.0, dl.measure(k[1], 18.0, true) + 36.0)
		dl.rrect(x, y, w, 44.0, 10.0, UiTheme.CARD_HI, 1.0, UiTheme.CARD_LINE)
		dl.text_mid(x + w * 0.5, y + 22.0, k[1], 18.0, UiTheme.TEXT, "c", true)
		dl.hot(x, y, w, 44.0, "key:" + str(k[0]))
		x += w + 10.0


## Match-up, coach, keypad and coin toss: two team cards and the prompt.
func _matchup(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "Match setup")
	var boxes := find_boxes(gs.rows)
	last_windows = boxes
	var vis := ""
	var hom := ""
	var prompt_lines := []
	for b in boxes:
		var joined := ""
		for l in b["lines"]:
			var s: String = str(l).strip_edges()
			if s != "":
				joined += (" | " if joined != "" else "") + s
		var at := joined.find(" at ")
		if at > 0 and vis == "":
			var parts := joined.split(" at ", false)
			vis = str(parts[0]).strip_edges()
			hom = str(parts[parts.size() - 1]).strip_edges()
		else:
			for l2 in b["lines"]:
				var s2: String = str(l2).strip_edges()
				if s2 != "":
					prompt_lines.append(s2)
	var coach := _two_values(gs, "Coach:")
	var keypad := _two_values(gs, "Keypad:")
	var names := [vis, hom]
	var teams := [2, 1]
	var hshort := hom.get_slice(" ", hom.count(" "))
	var mkits := UiTheme.kit_colors(hshort, vis.get_slice(" ", vis.count(" ")))
	var cw := 600.0
	var y := TOP + 70.0
	for i in range(2):
		var x := 200.0 if i == 0 else UiTheme.W - 200.0 - cw
		var nm: String = names[i]
		var k: Dictionary = mkits[1] if teams[i] == 2 else mkits[0]
		card(dl, x, y, cw, 300.0, k["primary"], 112.0)
		dl.text(x + 32.0, y + 42.0, "VISITOR" if i == 0 else "HOME", 15.0, str(k["primary_text"]) + "b3",
			"l", true)
		dl.text(x + 32.0, y + 86.0, dl.fit(nm, 34.0, cw - 64.0, true), 34.0, k["primary_text"], "l",
			true)
		_kv(dl, x + 32.0, y + 148.0, "icon_coach", "Coach", coach[i])
		_kv(dl, x + 32.0, y + 214.0, "icon_keyboard", "Keypad", keypad[i])
	dl.text(UiTheme.W * 0.5, y + 165.0, "at", 30.0, UiTheme.TEXT_3, "c", true)
	# prompt card
	var py := y + 340.0
	var pw := 1120.0
	var px := (UiTheme.W - pw) * 0.5
	var keys := []
	var texts := []
	for s3 in prompt_lines:
		var kp := key_prompts(str(s3))
		if kp.size() > 0:
			keys.append_array(kp)
		else:
			texts.append(str(s3))
	var coin := _coin_result(gs)
	var ph := 120.0 + (84.0 if keys.size() > 0 else 0.0)
	card(dl, px, py, pw, ph)
	var ty := py + 58.0
	if keys.size() == 0:
		ty = py + ph * 0.5 + 10.0 - (texts.size() - 1) * 19.0
	for s4 in texts:
		dl.text(UiTheme.W * 0.5, ty, s4, 26.0, UiTheme.TEXT, "c", true)
		ty += 38.0
	if keys.size() > 0:
		var bw := 300.0
		var gap := 24.0
		var bx := (UiTheme.W - (bw * keys.size() + gap * (keys.size() - 1))) * 0.5
		for i2 in range(keys.size()):
			key_button(dl, bx + i2 * (bw + gap), py + ph - 84.0, bw, 56.0, keys[i2][0], keys[i2][1], i2 == 0)
	if coin != "" or gs.screen_text().contains("call"):
		var cy0 := py + ph + 40.0
		dl.art("coin", UiTheme.W * 0.5 - 70.0, cy0, 140.0, 140.0)
		if coin != "":
			dl.text(UiTheme.W * 0.5, cy0 + 82.0, coin.left(1), 46.0, "#7c4a03", "c", true)
			dl.text(UiTheme.W * 0.5, cy0 + 190.0, "It is " + coin, 26.0, UiTheme.WARN, "c", true)


func _kv(dl: DrawList, x: float, y: float, icon: String, k: String, v: String) -> void:
	dl.rrect(x, y, 536.0, 52.0, 10.0, UiTheme.CARD_HI)
	dl.art(icon, x + 16.0, y + 14.0, 24.0, 24.0, {"color": UiTheme.TEXT_2})
	dl.text_mid(x + 52.0, y + 26.0, k, 17.0, UiTheme.TEXT_3, "l", true)
	var val := v.capitalize() if v != "" else "—"
	dl.text_mid(x + 516.0, y + 26.0, val, 19.0, UiTheme.TEXT if v != "" else UiTheme.TEXT_3, "r", true)


## The two values after a label that appears twice on a row (visitor, home).
func _two_values(gs: GameState, label: String) -> Array:
	for r in range(gs.rows.size()):
		var s: String = gs.rows[r]
		var a := s.find(label)
		if a >= 0:
			var b := s.find(label, a + 1)
			if b > a:
				return [s.substr(a + label.length(), b - a - label.length()).strip_edges(),
					s.substr(b + label.length()).strip_edges()]
	return ["", ""]


func _coin_result(gs: GameState) -> String:
	for r in range(gs.rows.size()):
		var s: String = gs.rows[r]
		var p := s.find("It is ")
		if p >= 0:
			return s.substr(p + 6).strip_edges()
	return ""


## Windows the game opens over the field overview (play lists, groups,
## kick/penalty menus) as side cards; numbered entries type their number.
func _field_windows(dl: DrawList, gs: GameState) -> void:
	var boxes := []
	for b in find_boxes(gs.rows):
		var first: String = str(b["lines"][0]) if b["lines"].size() > 0 else ""
		if first.contains("░") or int(b["top"]) <= 3 and int(b["right"]) - int(b["left"]) > 60:
			continue
		if int(b["right"]) - int(b["left"]) > 60:
			continue
		boxes.append(b)
	last_windows = boxes
	var ys := [MatchView.BAR_H + 96.0, MatchView.BAR_H + 96.0]
	if match_view.vertical:
		# left column: under the scoreboard; right column: from the top
		ys = [MatchView.BAR_H + 128.0, MatchView.BAR_H + 16.0]
	# windows with numbered entries (play lists, kick, fourth-down and penalty
	# menus) and rosters are side panels; other windows (injury report,
	# penalty description) are notices
	var side_boxes := []
	for b0 in boxes:
		var wide := int(b0["right"]) - int(b0["left"]) >= 34
		if numbered(b0["lines"]).size() > 0 or _is_table(b0["lines"]) \
				or (int(b0["top"]) < 18 and not wide):
			side_boxes.append(b0)
		else:
			_notice(dl, gs, b0)
	_yes_no(dl, gs)
	_play_windows_prompt(dl, gs)
	for b2 in side_boxes:
		var side := 0 if int(b2["left"]) < 40 else 1
		if _is_table(b2["lines"]):
			ys[side] = _table_window(dl, b2, side, ys[side]) + 14.0
			continue
		var opts := numbered(b2["lines"])
		var other := []
		for l in b2["lines"]:
			var s: String = str(l).strip_edges()
			if s != "" and numbered([s]).size() == 0:
				other.append(s)
		var w := 380.0
		var x := _win_x(side, w)
		var pad := gs.coach_side(str(b2["title"]), side)
		var who := gs.coach_label(pad)
		var head := 44.0 if str(b2["title"]) != "" or who == "Human" else 14.0
		var h := head + opts.size() * 38.0 + other.size() * 30.0 + 14.0
		var y: float = ys[side]
		dl.art("panel", x, y, w, h, {"W": w, "H": h, "R": 14.0, "W1": w - 1.5, "H1": h - 1.5})
		if str(b2["title"]) != "":
			var tc := UiTheme.TEXT
			dl.text(x + 20.0, y + 30.0, dl.fit(str(b2["title"]), 18.0, w - 150.0, true), 18.0, tc,
				"l", true)
		if who == "Human":
			# whose keys: F1-F10 for the left coach, the numeric keypad for the right
			var tag := "F-keys" if pad == "L" else "Keypad"
			var tgw := dl.measure(tag, 13.0, true) + 20.0
			dl.rrect(x + w - 16.0 - tgw, y + 14.0, tgw, 24.0, 12.0, "#1e3a8a", 1.0, UiTheme.ACCENT)
			dl.text_mid(x + w - 16.0 - tgw * 0.5, y + 26.0, tag, 13.0, "#dbeafe", "c", true)
		var oy := y + head
		for s2 in other:
			dl.text(x + 20.0, oy + 21.0, dl.fit(str(s2), 16.0, w - 40.0), 16.0, UiTheme.TEXT_2)
			oy += 30.0
		for o in opts:
			var key: String = o[0]
			# the coach's own keys: digit d is F<d> (left) or keypad <d> (right);
			# entry 10 is F10 / keypad 0
			var digit := key if key.length() == 1 else ("0" if key == "10" else "")
			dl.rrect(x + 12.0, oy + 2.0, w - 24.0, 34.0, 8.0, UiTheme.CARD_HI)
			var badge := UiTheme.ACCENT if digit != "" else UiTheme.CARD_LINE
			var label := key if pad == "R" or digit == "" else "F" + key
			var bwid := maxf(30.0, dl.measure(label, 14.0, true) + 12.0)
			dl.rrect(x + 18.0, oy + 7.0, bwid, 24.0, 6.0, badge)
			dl.text_mid(x + 18.0 + bwid * 0.5, oy + 19.0, label, 14.0, "#ffffff", "c", true)
			dl.text_mid(x + 30.0 + bwid, oy + 19.0, dl.fit(str(o[1]), 17.0, w - 54.0 - bwid), 17.0,
				UiTheme.TEXT)
			if digit != "":
				dl.hot(x + 12.0, oy + 2.0, w - 24.0, 34.0, "pad:%s:%s" % [pad, digit])
			oy += 38.0
		ys[side] = y + h + 14.0
	# status words the game prints under the field ("Offense selected")
	var last := ""
	for r in range(22, mini(gs.rows.size(), 25)):
		var row: String = gs.rows[r]
		if row.substr(0, 15).strip_edges() != "" and not _is_frame(row):
			last = row
	var st := []
	for part in last.split("  ", false):
		var t := str(part).strip_edges()
		if t != "":
			st.append(t)
	var sx := [28.0, UiTheme.W - 28.0]
	for i in range(mini(st.size(), 2)):
		var label: String = st[i]
		var tw := dl.measure(label, 15.0, true) + 40.0
		var left := i == 0 and last.find(label) < 40
		var x2: float = sx[0] if left else UiTheme.W - 28.0 - tw
		if match_view.vertical and match_view.columns.size() == 4:
			var k := 0 if left else 2
			x2 = (float(match_view.columns[k]) + float(match_view.columns[k + 1]) - tw) * 0.5
		var sy := match_view.status_y()
		dl.rrect(x2, sy, tw, 32.0, 16.0, "#14532dee", 1.0, "#22c55e66")
		dl.circle(x2 + 16.0, sy + 16.0, 4.0, UiTheme.GOOD)
		dl.text_mid(x2 + 26.0, sy + 16.0, label, 15.0, "#bbf7d0", "l", true)


## Unrecognised screen: the original text (HD text renderer) in a modern frame.
func _fallback(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "")
	var w := 1600.0
	var h := 900.0
	var x := (UiTheme.W - w) * 0.5
	var y := TOP + (UiTheme.H - TOP - h) * 0.5
	dl.art("panel", x - 16.0, y - 16.0, w + 32.0, h + 32.0, {"W": w + 32.0, "H": h + 32.0, "R": 18.0,
		"W1": w + 30.5, "H1": h + 30.5})
	dl.textgrid(x, y, w, h, gs.rows, gs.attrs)


static func _is_frame(s: String) -> bool:
	for ch in ["║", "═", "╔", "╚", "│", "─"]:
		if s.contains(ch):
			return true
	return false


## A window the game opens under the field (injury report, messages) as a
## card above the info strip; block-letter captions ("SHAKEN UP") become a badge.
func _notice(dl: DrawList, gs: GameState, b: Dictionary) -> void:
	var badge := ""
	for r in range(int(b["top"]), int(b["bottom"]) + 1):
		var row: String = gs.rows[r].substr(0, int(b["left"]))
		var a := row.find("█")
		var z := row.rfind("█")
		if a >= 0 and z > a + 1:
			var inner := row.substr(a + 1, z - a - 1).replace("█", " ").strip_edges()
			if inner != "":
				badge += (" " if badge != "" else "") + inner
	var lines := []
	for l in b["lines"]:
		var t := str(l).strip_edges()
		if t != "":
			lines.append(t)
	var w := minf(640.0, _notice_w())
	var h := 40.0 + lines.size() * 34.0 + 24.0
	var x := _notice_cx() - w * 0.5
	var y := match_view.status_y() - h + 8.0
	dl.art("panel", x, y, w, h, {"W": w, "H": h, "R": 16.0, "W1": w - 1.5, "H1": h - 1.5})
	var tx := x + 28.0
	if badge != "":
		var bw := dl.measure(badge, 15.0, true) + 28.0
		dl.rrect(x + 24.0, y + 22.0, bw, 30.0, 15.0, "#7f1d1d", 1.0, "#ef444488")
		dl.text_mid(x + 24.0 + bw * 0.5, y + 37.0, badge, 15.0, "#fecaca", "c", true)
		tx = x + 24.0 + bw + 18.0
	var ly := y + 44.0
	for t2 in lines:
		dl.text(tx, ly, dl.fit(str(t2), 20.0, x + w - 24.0 - tx, true), 20.0, UiTheme.TEXT, "l", true)
		ly += 34.0


## "(y/n)" questions under the field as a card with Y and N buttons.
func _yes_no(dl: DrawList, gs: GameState) -> void:
	for r in range(18, mini(gs.rows.size(), 25)):
		var row: String = gs.rows[r]
		var p := row.find("(y/n)")
		if p < 0:
			continue
		var q := row.strip_edges().replace(" (y/n)", "").replace("(y/n)", "").strip_edges()
		var w := minf(720.0, _notice_w())
		var h := 150.0
		var mx := _notice_cx()
		var x := mx - w * 0.5
		var y := match_view.status_y() - h + 8.0
		var bw := minf(220.0, (w - 60.0) * 0.5)
		dl.art("panel", x, y, w, h, {"W": w, "H": h, "R": 16.0, "W1": w - 1.5, "H1": h - 1.5})
		dl.text(mx, y + 50.0, dl.fit(q, 24.0, w - 40.0, true), 24.0, UiTheme.TEXT, "c", true)
		key_button(dl, mx - bw - 10.0, y + 76.0, bw, 52.0, "Y", "Yes", true)
		key_button(dl, mx + 10.0, y + 76.0, bw, 52.0, "N", "No", false)
		return


## "Show play windows (L,R):  LEFT On   RIGHT Off" (the W key): a card with
## a toggle button per side (typing L or R) and Enter to close it.
func _play_windows_prompt(dl: DrawList, gs: GameState) -> void:
	for r in range(18, mini(gs.rows.size(), 25)):
		var row: String = gs.rows[r]
		var p := row.find("(L,R)")
		if p < 0:
			continue
		var q := row.substr(0, p).strip_edges()
		var rest := row.substr(p + 5).replace(":", " ")
		var w := minf(720.0, _notice_w())
		var h := 150.0
		var mx := _notice_cx()
		var x := mx - w * 0.5
		var y := match_view.status_y() - h + 8.0
		var bw := minf(200.0, (w - 80.0) / 3.0)
		dl.art("panel", x, y, w, h, {"W": w, "H": h, "R": 16.0, "W1": w - 1.5, "H1": h - 1.5})
		dl.text(mx, y + 50.0, dl.fit(q, 24.0, w - 40.0, true), 24.0, UiTheme.TEXT, "c", true)
		var sides := [["L", "LEFT", "Left"], ["R", "RIGHT", "Right"]]
		for i in range(2):
			var sd: Array = sides[i]
			var f := rest.find(str(sd[1]))
			var state := ""
			if f >= 0:
				var tail := rest.substr(f + str(sd[1]).length()).strip_edges()
				state = tail.get_slice(" ", 0)
			key_button(dl, mx - bw * 1.5 - 10.0 + i * (bw + 10.0), y + 76.0, bw, 52.0, str(sd[0]),
				"%s %s" % [sd[2], state] if state != "" else str(sd[2]), state == "On")
		key_button(dl, mx + bw * 0.5 + 10.0, y + 76.0, bw, 52.0, "Enter", "Done", false)
		return


## The per-player pages behind PgDn (Rushing, Passing, ..., Sacks): the
## game's two "<Team> - <Category>" boxes, with the rows printed under them,
## as two cards side by side; the rows after "---" are the totals.
func _player_stats(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "Statistics")
	var heads := []
	for b in find_boxes(gs.rows):
		if b["lines"].size() >= 2 and str(b["lines"][0]).contains(" - ") \
				and str(b["lines"][1]).contains("No."):
			heads.append(b)
	if heads.size() == 0:
		_fallback(dl, gs, ui)
		return
	var footer := ""
	var last_row := gs.rows.size()
	for r in range(gs.rows.size()):
		if gs.rows[r].contains("PgUp"):
			footer = gs.rows[r].strip_edges()
			last_row = r
	var away := gs.short_name(2)
	var home := gs.short_name(1)
	var kits := UiTheme.kit_colors(home, away)
	var cat := str(heads[0]["lines"][0]).get_slice(" - ", 1).strip_edges()
	var x0 := 120.0
	var y0 := TOP + 28.0
	dl.text(x0, y0 + 34.0, cat, 30.0, UiTheme.TEXT, "l", true)
	var when := "Final" if gs.clock_text == "0:00" and gs.quarter >= 4 else gs.clock_text
	dl.text(x0, y0 + 62.0, "Player statistics  ·  %s  ·  %s" % [MatchView.quarter_label(gs.quarter),
		when], 16.0, UiTheme.TEXT_3, "l", true)
	var gap := 32.0
	var cw := (UiTheme.W - 2.0 * x0 - gap) / 2.0
	for i in range(mini(heads.size(), 2)):
		var b: Dictionary = heads[i]
		var team := str(b["lines"][0]).get_slice(" - ", 0).strip_edges()
		var k: Dictionary = kits[1] if team == away else kits[0]
		var left := int(b["left"]) + 1
		var span := int(b["right"]) - left
		var hdr := str(b["lines"][1])
		# column centres from the header words, in characters
		var cols := []
		var c := 0
		while c < hdr.length():
			if hdr.substr(c, 1) != " ":
				var e := c
				while e < hdr.length() and hdr.substr(e, 1) != " ":
					e += 1
				cols.append([hdr.substr(c, e - c), (c + e) * 0.5])
				c = e
			else:
				c += 1
		var rows := []
		var total := false
		for r2 in range(int(b["bottom"]) + 1, last_row):
			var seg := gs.rows[r2].substr(left, span)
			if seg.strip_edges() == "":
				continue
			if seg.contains("---"):
				total = true
				continue
			# cells: runs separated by two or more spaces, placed under the
			# nearest header word; text far right of the last column is a note
			var cells := {}
			var note := ""
			var j := 0
			while j < seg.length():
				if seg.substr(j, 1) == " ":
					j += 1
					continue
				var e2 := j
				while e2 < seg.length() and not (seg.substr(e2, 1) == " " \
						and (e2 + 1 >= seg.length() or seg.substr(e2 + 1, 1) == " ")):
					e2 += 1
				var cell := seg.substr(j, e2 - j)
				var mid := (j + e2) * 0.5
				var best := -1
				var bd := 1e9
				for ci in range(cols.size()):
					var d := absf(float(cols[ci][1]) - mid)
					if d < bd:
						bd = d
						best = ci
				if best == cols.size() - 1 and mid - float(cols[best][1]) > 6.0:
					note = (note + " " + cell).strip_edges()
				elif best >= 0:
					cells[best] = cell
				j = e2
			rows.append([cells, note, total])
		var x := x0 + i * (cw + gap)
		var y := y0 + 96.0
		var h := 70.0 + 40.0 + rows.size() * 42.0 + 20.0
		card(dl, x, y, cw, h, k["primary"], 70.0)
		dl.text_mid(x + 28.0, y + 35.0, team, 24.0, k["primary_text"], "l", true)
		dl.text_mid(x + cw - 28.0, y + 35.0, cat, 17.0, k["primary_text"], "r", true)
		var unit := (cw - 56.0) / maxf(float(span), 1.0)
		var hy := y + 70.0 + 22.0
		for col in cols:
			dl.text_mid(x + 28.0 + float(col[1]) * unit, hy, str(col[0]).to_upper(), 13.0,
				UiTheme.TEXT_3, "c", true)
		var ry := y + 70.0 + 40.0
		for rw in rows:
			var is_total: bool = rw[2]
			if is_total:
				dl.rect(x + 20.0, ry, cw - 40.0, 1.0, UiTheme.CARD_LINE)
			else:
				dl.rrect(x + 16.0, ry + 3.0, cw - 32.0, 36.0, 8.0, UiTheme.CARD_HI)
			var cells2: Dictionary = rw[0]
			for ci2 in cells2:
				var cx := x + 28.0 + float(cols[int(ci2)][1]) * unit
				dl.text_mid(cx, ry + 21.0, str(cells2[ci2]), 18.0,
					UiTheme.TEXT if int(ci2) > 1 or is_total else UiTheme.TEXT_2, "c", is_total or int(ci2) == 0)
			if str(rw[1]) != "":
				var lastc := x + 28.0 + float(cols[cols.size() - 1][1]) * unit
				dl.text_mid(lastc + 28.0, ry + 21.0, str(rw[1]), 16.0, UiTheme.TEXT_2, "l", false)
			ry += 42.0
	if footer != "":
		var by := UiTheme.H - 70.0
		key_button(dl, UiTheme.W * 0.5 - 420.0, by, 260.0, 52.0, "PgUp", "Previous", false)
		key_button(dl, UiTheme.W * 0.5 - 130.0, by, 260.0, 52.0, "PgDn", "More stats", false)
		key_button(dl, UiTheme.W * 0.5 + 160.0, by, 260.0, 52.0, "Enter", "Continue", true)


## TEAM STATISTICS: the game's table as section cards with comparison bars.
func _stats(dl: DrawList, gs: GameState, ui: Dictionary) -> void:
	frame(dl, ui, "Statistics")
	var head_row := -1
	for r in range(gs.rows.size()):
		if gs.rows[r].contains("STATISTICS"):
			head_row = r
			break
	if head_row < 0:
		_fallback(dl, gs, ui)
		return
	var head: String = gs.rows[head_row]
	var title := head.substr(0, head.find("STATISTICS") + 10).replace("║", "").strip_edges()
	# team names: the last two words of the header row; their right ends are
	# where the value columns end
	var words := head.replace("║", " ").strip_edges().split(" ", false)
	var t1: String = words[words.size() - 2] if words.size() >= 2 else ""
	var t2: String = words[words.size() - 1] if words.size() >= 1 else ""
	var sections := []
	var cur := {"name": "", "rows": []}
	var footer := ""
	for r2 in range(head_row + 1, gs.rows.size()):
		var row: String = gs.rows[r2]
		if row.contains("═") or row.strip_edges() == "":
			continue
		if row.contains("──"):
			if cur["rows"].size() > 0:
				sections.append(cur)
			cur = {"name": "", "rows": []}
			continue
		if row.contains("PgUp") or row.contains("any other key"):
			footer = row.strip_edges()
			continue
		var sec := row.substr(0, 12).strip_edges()
		if sec != "":
			var nm0 := str(cur["name"])
			cur["name"] = (nm0 + ("" if nm0.ends_with("/") else " ") + sec).strip_edges()
		var cells := []
		for part in row.substr(12).split("  ", false):
			var c := str(part).strip_edges()
			if c != "":
				cells.append(c)
		if cells.size() < 3:
			continue
		var label: String = cells[0]
		var v1: String = cells[cells.size() - 2]
		var v2: String = cells[cells.size() - 1]
		if label != "":
			cur["rows"].append([label, v1, v2, row.substr(12, 3) == "   "])
	if cur["rows"].size() > 0:
		sections.append(cur)
	var away := gs.short_name(2)
	var home := gs.short_name(1)
	var kits := UiTheme.kit_colors(home, away)
	var k1: Dictionary = kits[1] if t1 == away else kits[0]
	var k2: Dictionary = kits[0] if t2 == home else kits[1]
	var c1: String = k1["primary"]
	var c2: String = k2["primary"]
	if UiTheme.oklab_distance(c1, c2) < UiTheme.CLASH_DISTANCE:
		c2 += "80"      # same-looking colours: the second bar is lighter
	var x := 160.0
	var w := UiTheme.W - 320.0
	var y := TOP + 28.0
	dl.text(x, y + 34.0, title.capitalize(), 30.0, UiTheme.TEXT, "l", true)
	# the score, from the game's scoreboard rows kept above the table
	var s1 := gs.score_away if t1 == away else gs.score_home
	var s2 := gs.score_home if t2 == home else gs.score_away
	_team_chip(dl, x + w - 520.0, y + 6.0, 250.0, "%s  %d" % [t1, s1], k1)
	_team_chip(dl, x + w - 250.0, y + 6.0, 250.0, "%s  %d" % [t2, s2], k2)
	var when := "Final" if gs.clock_text == "0:00" and gs.quarter >= 4 else gs.clock_text
	dl.text(x, y + 62.0, "%s  ·  %s" % [MatchView.quarter_label(gs.quarter), when], 16.0,
		UiTheme.TEXT_3, "l", true)
	y += 70.0
	var col_v1 := x + w - 395.0
	var col_v2 := x + w - 125.0
	for sct in sections:
		var rows: Array = sct["rows"]
		var h := 22.0 + rows.size() * 34.0 + 14.0
		card(dl, x, y, w, h)
		dl.text(x + 24.0, y + 34.0, str(sct["name"]).capitalize(), 15.0, UiTheme.ACCENT_2, "l", true)
		var ry := y + 22.0
		for rw in rows:
			var sub: bool = rw[3]
			dl.text_mid(x + (240.0 if sub else 220.0), ry + 17.0, str(rw[0]), 17.0,
				UiTheme.TEXT_2 if sub else UiTheme.TEXT, "l", not sub)
			dl.text_mid(col_v1, ry + 17.0, str(rw[1]), 18.0, UiTheme.TEXT, "c", true)
			dl.text_mid(col_v2, ry + 17.0, str(rw[2]), 18.0, UiTheme.TEXT, "c", true)
			_compare_bar(dl, col_v1 - 120.0, ry + 31.0, col_v2 - col_v1 + 240.0, str(rw[1]), str(rw[2]),
				c1, c2)
			ry += 34.0
		y += h + 12.0
	if footer != "":
		var by := UiTheme.H - 70.0
		key_button(dl, UiTheme.W * 0.5 - 420.0, by, 260.0, 52.0, "PgUp", "Previous", false)
		key_button(dl, UiTheme.W * 0.5 - 130.0, by, 260.0, 52.0, "PgDn", "More stats", false)
		key_button(dl, UiTheme.W * 0.5 + 160.0, by, 260.0, 52.0, "Enter", "Continue", true)


func _team_chip(dl: DrawList, x: float, y: float, w: float, nm: String, k: Dictionary) -> void:
	dl.rrect(x, y, w - 20.0, 44.0, 12.0, k["primary"], 2.0, UiTheme.shade(k["primary"], 0.62))
	dl.text_mid(x + (w - 20.0) * 0.5, y + 22.0, nm, 19.0, k["primary_text"], "c", true)


## Thin two-colour bar comparing the first numbers of two values.
func _compare_bar(dl: DrawList, x: float, y: float, w: float, a: String, b: String, ca: String,
		cb: String) -> void:
	var va := _first_number(a)
	var vb := _first_number(b)
	if va < 0.0 or vb < 0.0 or va + vb <= 0.0:
		return
	var split := w * va / (va + vb)
	dl.rect(x, y, split - 1.0, 3.0, ca)
	dl.rect(x + split + 1.0, y, w - split - 1.0, 3.0, cb)


static func _first_number(s: String) -> float:
	var t := s.strip_edges()
	var n := ""
	for i in range(t.length()):
		var ch := t.substr(i, 1)
		if "0123456789.".contains(ch):
			n += ch
		elif ch == ":" and n != "":
			n += "."
		elif n != "":
			break
	if n == "" or n == ".":
		return -1.0
	return n.to_float()


## A roster-like window (a header with column names, rows of numbers).
static func _is_table(lines: Array) -> bool:
	for l in lines:
		var t := str(l)
		if t.contains("No.") or t.contains("Pos "):
			return true
	return false


func _table_window(dl: DrawList, b: Dictionary, side: int, y: float) -> float:
	var lines := []
	for l in b["lines"]:
		var t := str(l)
		if t.strip_edges() != "" and not t.strip_edges().begins_with("──") and not t.contains("─────"):
			lines.append(t)
	var w := 460.0
	var x := _win_x(side, w)
	var head := 44.0
	var h := head + lines.size() * 30.0 + 14.0
	dl.art("panel", x, y, w, h, {"W": w, "H": h, "R": 14.0, "W1": w - 1.5, "H1": h - 1.5})
	dl.text(x + 20.0, y + 30.0, str(b["title"]), 18.0, UiTheme.TEXT, "l", true)
	var cw := (w - 40.0) / maxf(float(int(b["right"]) - int(b["left"]) - 1), 1.0)
	var fs := minf(cw / 0.6, 17.0)
	var ly := y + head
	for i in range(lines.size()):
		var t2: String = lines[i]
		var sel := t2.contains("►")
		if sel:
			dl.rrect(x + 10.0, ly + 2.0, w - 20.0, 28.0, 7.0, "#1e3a8a", 1.0, UiTheme.ACCENT)
		var col := UiTheme.TEXT_3 if i == 0 else (UiTheme.TEXT if sel else UiTheme.TEXT_2)
		dl.text(x + 20.0, ly + 21.0, t2.replace("►", " "), fs, col, "l", sel, 0.0, true)
		ly += 30.0
	return y + h


## Left edge of a side window: at the screen edge over the horizontal field,
## centred in the free column beside the vertical one.
func _win_x(side: int, w: float) -> float:
	var mv := match_view
	if mv.vertical and mv.columns.size() == 4:
		var c0: float = mv.columns[2 * side]
		var c1: float = mv.columns[2 * side + 1]
		return maxf(c0 + 16.0, c0 + (c1 - c0 - w) * 0.5)
	return 28.0 if side == 0 else UiTheme.W - 28.0 - w


## Notices and questions: centred over the horizontal field; in the right
## column beside the vertical one.
func _notice_cx() -> float:
	var mv := match_view
	if mv.vertical and mv.columns.size() == 4:
		return (float(mv.columns[2]) + float(mv.columns[3])) * 0.5
	return UiTheme.W * 0.5


func _notice_w() -> float:
	var mv := match_view
	if mv.vertical and mv.columns.size() == 4:
		return float(mv.columns[3]) - float(mv.columns[2]) - 48.0
	return 720.0
