## Front-end for the statically recompiled NFL Challenge.
##
## * Finds the game files (res://dos_c, user://dos_c, --dos-root=<path> or a
##   folder picked in the app) and shows instructions if NFL.EXE is missing.
## * The program itself is GDScript generated ahead of time from the user's
##   NFL.EXE by tools/recomp/recompile.py (res://recomp/, not committed).  It
##   runs on its own Thread (it blocks while it waits for a key, the way the DOS
##   program does) on the native host layer (res://host/).  This thread only
##   renders video memory, feeds keys and wakes the program thread; the BIOS
##   clock follows real time x the speed setting.
## * Text modes are drawn by TextScreen with a vector font at the window's native
##   resolution, filling a 16:9 screen ("HD text"). CGA graphics (instant replay)
##   and the optional "retro" text view are rendered into an Image -> ImageTexture
##   and shown 4:3, pillarboxed.
## * The toolbar floats over the screen and hides itself; move the mouse to the
##   top edge to show it. F11 toggles fullscreen, F12 cycles the speed.
## * Maps Godot key events to PC/XT scancodes (make + break), so the native
##   BIOS keyboard handler produces the right ASCII/scan words, incl. Alt/Ctrl.
## * Optional PC-speaker square wave through AudioStreamGenerator.
extends Control

const TICK_HZ := 18.2065
const SPEEDS := [1.0, 2.0, 4.0, 8.0, 0.0]   # 0 = turbo
const TURBO_FACTOR := 32.0
const MIX_RATE := 22050.0
const RECOMP_DIR := "res://recomp"

## Godot physical keycode -> PC/XT set-1 scancode.
var scancodes: Dictionary = {}

var machine: Machine = null
var bios: BIOS = null
var dos: DOS = null
var host: RecompHost = null
var thread: Thread = null
var load_error: String = ""
var video: Video = null
var image: Image = null
var texture: ImageTexture = null
var tex_rect: TextureRect = null
var text_screen: TextScreen = null
var toolbar: PanelContainer = null
var hd_button: Button = null
var colors_button: Button = null
var aspect: AspectRatioContainer = null
var hud: Label = null
var message: Label = null
var speed_button: Button = null
var pause_button: Button = null
var file_dialog: FileDialog = null
var player: AudioStreamPlayer = null
var playback: AudioStreamGeneratorPlayback = null
var phase: float = 0.0

var dos_root: String = ""
var write_root: String = ""
var team_dir: String = "89"
var program: String = "NFL.EXE"
var program_args: String = ""
var speed_index: int = 0
var paused: bool = false
var blink_timer: float = 0.0
var blink_phase: int = 0
var ips_timer: float = 0.0
var ips_last: int = 0
var ips: float = 0.0
var sound_on: bool = true
var hd_text: bool = true
var start_fullscreen: bool = false


func _ready() -> void:
	_build_keymap()
	_build_ui()
	_parse_args()
	_start()
	_apply_speed()
	_apply_view_buttons()
	if start_fullscreen:
		_toggle_fullscreen()


# ------------------------------------------------------------------ setup
func _parse_args() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--dos-root="):
			dos_root = a.substr(11)
		elif a.begins_with("--args="):
			program_args = a.substr(7)
		elif a.begins_with("--team-dir="):
			team_dir = a.substr(11)
		elif a.begins_with("--program="):
			program = a.substr(10)
		elif a == "--turbo":
			speed_index = SPEEDS.size() - 1
		elif a == "--mute":
			sound_on = false
		elif a == "--retro":
			hd_text = false
		elif a == "--modern-colors":
			text_screen.set_modern_colors(true)
		elif a == "--fullscreen":
			start_fullscreen = true


func _has_game(dir: String) -> bool:
	if dir == "" or not DirAccess.dir_exists_absolute(dir):
		return false
	for f in DirAccess.get_files_at(dir):
		if f.to_upper() == program.to_upper():
			return true
	return false


func _pick_root() -> bool:
	write_root = ""
	if dos_root != "" and _has_game(dos_root):
		return true
	if _has_game("user://dos_c"):
		dos_root = "user://dos_c"
		return true
	if _has_game("res://dos_c"):
		dos_root = "res://dos_c"
		# res:// is read-only in exported builds: overlay writes in user://dos_c
		write_root = "user://dos_c"
		DirAccess.make_dir_recursive_absolute(write_root)
		return true
	return false


func _start() -> void:
	_stop()
	if not _pick_root():
		_show_message(
			"Game files not found.\n\n"
			+ "Copy your own NFL Challenge files (NFL.EXE, A..Q, LEAGUE.NFL, SEASON.COM,\n"
			+ "and the team folder 89/) into the project folder  godot/dos_c/\n"
			+ "(or into %s),\n" % ProjectSettings.globalize_path("user://dos_c")
			+ "or start Godot with  -- --dos-root=/path/to/nfl\n"
			+ "or press the \"Game folder...\" button above.\n\n"
			+ "The files are not included: bring your own copy.")
		return
	if not ResourceLoader.exists(RECOMP_DIR + "/recomp_index.gd"):
		_show_message(
			"The recompiled program is missing.\n\n"
			+ "Run the static recompiler once on your own NFL.EXE:\n\n"
			+ "    python3 tools/recomp/recompile.py /path/to/NFL.EXE\n\n"
			+ "It writes godot/recomp/ (generated from your copy, never committed).")
		return
	message.visible = false
	machine = Machine.new()
	machine.speed = _speed_factor()
	var now := Time.get_datetime_dict_from_system()
	var secs: int = int(now["hour"]) * 3600 + int(now["minute"]) * 60 + int(now["second"])
	var midnight: int = int(Time.get_unix_time_from_datetime_dict(
		{"year": now["year"], "month": now["month"], "day": now["day"],
		"hour": 0, "minute": 0, "second": 0}))
	bios = BIOS.new(machine, FontCP437.font8(), "user://printer.txt", midnight, secs)
	var root_pair := [dos_root, write_root]
	var drives := {"A": root_pair, "B": root_pair, "C": root_pair}
	var team := DOS.join_path(dos_root, team_dir)
	if DirAccess.dir_exists_absolute(team):
		var team_write := ""
		if write_root != "":
			team_write = DOS.join_path(write_root, team_dir)
			DirAccess.make_dir_recursive_absolute(team_write)
		drives["D"] = [team, team_write]
	dos = DOS.new(machine, bios, drives, "C", "user://printer.txt")
	var info := dos.load_program("C:" + program, program_args)
	if info.is_empty():
		_show_message("Could not load %s from %s" % [program, dos_root])
		machine = null
		return
	if int(info.get("load_seg", 0)) != 0x122:
		_show_message("%s is not the NFL.EXE the recompiled code was made from." % program)
		machine = null
		return
	machine.log_msg("[load] %s from %s %s" % [program, dos_root, str(info)])
	video = Video.new()
	host = RecompHost.new()
	load_error = ""
	thread = Thread.new()
	thread.start(_program_thread)


## Program thread: load and link the generated scripts (compiling them takes a
## moment, so not on the main thread), then run the program until it exits.
func _program_thread() -> void:
	var index_script: GDScript = load(RECOMP_DIR + "/recomp_index.gd")
	if index_script == null:
		load_error = "cannot load " + RECOMP_DIR + "/recomp_index.gd"
		return
	host.setup(machine, bios, dos, index_script.new(), _load_unit)
	host.run()


func _load_unit(unit_name: String) -> RecompUnit:
	var scr: GDScript = load("%s/%s.gd" % [RECOMP_DIR, unit_name])
	return scr.new()


## Stops the program thread (reset, folder change, quit).
func _stop() -> void:
	if thread != null:
		if machine != null:
			machine.request_abort()
		thread.wait_to_finish()
		thread = null
	machine = null
	host = null


func _exit_tree() -> void:
	_stop()


func _speed_factor() -> float:
	var s: float = SPEEDS[speed_index]
	return TURBO_FACTOR if s == 0.0 else s


func _show_message(text: String) -> void:
	message.text = text
	message.visible = true


func _build_ui() -> void:
	var bg := ColorRect.new()
	bg.color = Color.BLACK
	bg.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(bg)
	# The emulated screen fills the whole window: HD text or the 4:3 bitmap view.
	text_screen = TextScreen.new()
	text_screen.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(text_screen)
	var ui_theme := Theme.new()
	ui_theme.default_font = text_screen.font
	ui_theme.default_font_size = 18
	theme = ui_theme
	aspect = AspectRatioContainer.new()
	aspect.set_anchors_preset(Control.PRESET_FULL_RECT)
	aspect.ratio = 4.0 / 3.0
	aspect.stretch_mode = AspectRatioContainer.STRETCH_FIT
	aspect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(aspect)
	tex_rect = TextureRect.new()
	tex_rect.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	tex_rect.stretch_mode = TextureRect.STRETCH_SCALE
	tex_rect.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	aspect.add_child(tex_rect)
	# Floating toolbar along the top edge (auto-hides, see _update_toolbar).
	toolbar = PanelContainer.new()
	toolbar.set_anchors_preset(Control.PRESET_TOP_WIDE)
	var panel_style := StyleBoxFlat.new()
	panel_style.bg_color = Color(0.06, 0.07, 0.09, 0.88)
	panel_style.content_margin_left = 10
	panel_style.content_margin_right = 10
	panel_style.content_margin_top = 6
	panel_style.content_margin_bottom = 6
	toolbar.add_theme_stylebox_override("panel", panel_style)
	add_child(toolbar)
	var bar := HBoxContainer.new()
	bar.add_theme_constant_override("separation", 8)
	toolbar.add_child(bar)
	speed_button = _add_button(bar, "Speed: 1x", _on_speed_pressed)
	pause_button = _add_button(bar, "Pause", _on_pause_pressed)
	_add_button(bar, "Reset", _on_reset_pressed)
	_add_button(bar, "Game folder...", _on_folder_pressed)
	hd_button = _add_button(bar, "Text: HD", _on_hd_pressed)
	colors_button = _add_button(bar, "Colors: classic", _on_colors_pressed)
	_add_button(bar, "Fullscreen (F11)", _toggle_fullscreen)
	_add_button(bar, "Screenshot", _on_screenshot_pressed)
	_add_button(bar, "Sound", _on_sound_pressed)
	hud = Label.new()
	hud.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	hud.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	hud.add_theme_color_override("font_color", Color(0.65, 0.7, 0.78))
	bar.add_child(hud)
	message = Label.new()
	message.set_anchors_preset(Control.PRESET_FULL_RECT)
	message.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	message.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	message.add_theme_color_override("font_color", Color(1.0, 0.85, 0.3))
	message.visible = false
	add_child(message)
	file_dialog = FileDialog.new()
	file_dialog.access = FileDialog.ACCESS_FILESYSTEM
	file_dialog.file_mode = FileDialog.FILE_MODE_OPEN_DIR
	file_dialog.title = "Choose the folder that contains NFL.EXE"
	file_dialog.dir_selected.connect(_on_dir_selected)
	add_child(file_dialog)
	player = AudioStreamPlayer.new()
	var gen := AudioStreamGenerator.new()
	gen.mix_rate = MIX_RATE
	gen.buffer_length = 0.1
	player.stream = gen
	add_child(player)
	player.play()
	playback = player.get_stream_playback()


func _add_button(parent: Control, text: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.focus_mode = Control.FOCUS_NONE
	b.pressed.connect(cb)
	parent.add_child(b)
	return b


# ---------------------------------------------------------------- buttons
func _on_speed_pressed() -> void:
	speed_index = (speed_index + 1) % SPEEDS.size()
	_apply_speed()


func _apply_speed() -> void:
	if machine:
		machine.speed = _speed_factor()
	var s: float = SPEEDS[speed_index]
	speed_button.text = "Speed: %s" % ("turbo" if s == 0.0 else "%dx" % int(s))


func _on_pause_pressed() -> void:
	paused = not paused
	pause_button.text = "Resume" if paused else "Pause"
	if machine:
		machine.set_paused(paused)


func _on_reset_pressed() -> void:
	_start()


func _on_folder_pressed() -> void:
	file_dialog.popup_centered_ratio(0.7)


func _on_dir_selected(dir: String) -> void:
	dos_root = dir
	_start()


func _on_sound_pressed() -> void:
	sound_on = not sound_on


func _on_hd_pressed() -> void:
	hd_text = not hd_text
	_apply_view_buttons()


func _on_colors_pressed() -> void:
	text_screen.set_modern_colors(not text_screen.modern_colors)
	_apply_view_buttons()


func _apply_view_buttons() -> void:
	hd_button.text = "Text: HD" if hd_text else "Text: retro"
	colors_button.text = "Colors: modern" if text_screen.modern_colors else "Colors: classic"
	if video:
		video.last_mode = -1      # force a full bitmap repaint when switching back


func _toggle_fullscreen() -> void:
	var win := get_window()
	if win.mode == Window.MODE_FULLSCREEN or win.mode == Window.MODE_EXCLUSIVE_FULLSCREEN:
		win.mode = Window.MODE_MAXIMIZED
	else:
		win.mode = Window.MODE_FULLSCREEN


## Shows the toolbar while the mouse is near the top edge, when paused, or when
## a message is up; otherwise fades it out so the game fills the screen.
func _update_toolbar(delta: float) -> void:
	var mouse_y := get_viewport().get_mouse_position().y
	var want := mouse_y < toolbar.size.y + 24.0 or paused or message.visible or machine == null
	var target := 1.0 if want else 0.0
	toolbar.modulate.a = move_toward(toolbar.modulate.a, target, delta * 6.0)
	toolbar.visible = toolbar.modulate.a > 0.01


func _on_screenshot_pressed() -> void:
	var shot := get_viewport().get_texture().get_image()
	if shot == null:
		return
	var path := "user://screenshot_%d.png" % Time.get_unix_time_from_system()
	shot.save_png(path)
	if machine:
		machine.log_msg("[shot] " + ProjectSettings.globalize_path(path))


# ------------------------------------------------------------- main loop
func _process(delta: float) -> void:
	_update_toolbar(delta)
	if machine == null:
		return
	machine.wake()
	_update_video(delta)
	_update_audio()
	ips_timer += delta
	if ips_timer >= 1.0:
		ips = (machine.cpu.icount - ips_last) / ips_timer
		ips_last = machine.cpu.icount
		ips_timer = 0.0
	var state := "running"
	if load_error != "":
		state = load_error
	elif host != null and host.error_msg != "":
		state = "stopped: " + host.error_msg
	elif machine.exited:
		state = "exited (code %d)" % machine.exit_code
	elif host == null or host.units.is_empty():
		state = "loading recompiled code..."
	elif paused:
		state = "paused"
	hud.text = "%s | %.0f services/s | ticks %d | mode %d" % [
		state, ips, machine.ticks, machine.rb(BIOS.BDA_MODE)]


func _update_video(delta: float) -> void:
	blink_timer += delta
	if blink_timer >= 0.267:
		blink_timer = 0.0
		blink_phase ^= 1
	var md := machine.rb(BIOS.BDA_MODE)
	var text_mode := md <= 3 or md == 7
	var use_hd := hd_text and text_mode
	text_screen.visible = use_hd
	aspect.visible = not use_hd
	if use_hd:
		text_screen.refresh(machine, blink_phase)
		return
	if video.render(machine, blink_phase) or image == null:
		var w := video.width
		var h := video.height
		if image == null or image.get_width() != w or image.get_height() != h:
			image = Image.create_from_data(w, h, false, Image.FORMAT_RGB8, video.rgb)
			texture = ImageTexture.create_from_image(image)
			tex_rect.texture = texture
		else:
			image.set_data(w, h, false, Image.FORMAT_RGB8, video.rgb)
			texture.update(image)


func _update_audio() -> void:
	if playback == null:
		return
	var freq := machine.speaker_freq() if sound_on else 0
	var frames := playback.get_frames_available()
	for _i in range(frames):
		var v := 0.0
		if freq > 20 and freq < 12000:
			phase = fmod(phase + freq / MIX_RATE, 1.0)
			v = 0.15 if phase < 0.5 else -0.15
		playback.push_frame(Vector2(v, v))


# --------------------------------------------------------------- keyboard
func _unhandled_input(event: InputEvent) -> void:
	if not (event is InputEventKey) or machine == null:
		return
	var k := event as InputEventKey
	var code: int = k.physical_keycode if k.physical_keycode != KEY_NONE else k.keycode
	if k.pressed and code == KEY_F11:
		_toggle_fullscreen()
		get_viewport().set_input_as_handled()
		return
	if k.pressed and code == KEY_F12:
		_on_speed_pressed()
		get_viewport().set_input_as_handled()
		return
	if not scancodes.has(code):
		return
	var sc: int = scancodes[code]
	var seq := PackedInt32Array()
	if k.pressed:
		seq.append(sc)
	else:
		seq.append(sc | 0x80)
	machine.key_scancodes(seq)
	get_viewport().set_input_as_handled()


func _build_keymap() -> void:
	var letters := {KEY_Q: 0x10, KEY_W: 0x11, KEY_E: 0x12, KEY_R: 0x13, KEY_T: 0x14,
		KEY_Y: 0x15, KEY_U: 0x16, KEY_I: 0x17, KEY_O: 0x18, KEY_P: 0x19,
		KEY_A: 0x1E, KEY_S: 0x1F, KEY_D: 0x20, KEY_F: 0x21, KEY_G: 0x22, KEY_H: 0x23,
		KEY_J: 0x24, KEY_K: 0x25, KEY_L: 0x26, KEY_Z: 0x2C, KEY_X: 0x2D, KEY_C: 0x2E,
		KEY_V: 0x2F, KEY_B: 0x30, KEY_N: 0x31, KEY_M: 0x32}
	scancodes.merge(letters)
	var digits := [KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0]
	for i in range(10):
		scancodes[digits[i]] = 0x02 + i
	var fkeys := [KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6, KEY_F7, KEY_F8, KEY_F9, KEY_F10]
	for i in range(10):
		scancodes[fkeys[i]] = 0x3B + i
	scancodes.merge({
		KEY_ESCAPE: 0x01, KEY_MINUS: 0x0C, KEY_EQUAL: 0x0D, KEY_BACKSPACE: 0x0E,
		KEY_TAB: 0x0F, KEY_BRACKETLEFT: 0x1A, KEY_BRACKETRIGHT: 0x1B, KEY_ENTER: 0x1C,
		KEY_KP_ENTER: 0x1C, KEY_CTRL: 0x1D, KEY_SEMICOLON: 0x27, KEY_APOSTROPHE: 0x28,
		KEY_QUOTELEFT: 0x29, KEY_SHIFT: 0x2A, KEY_BACKSLASH: 0x2B, KEY_COMMA: 0x33,
		KEY_PERIOD: 0x34, KEY_SLASH: 0x35, KEY_KP_DIVIDE: 0x35, KEY_KP_MULTIPLY: 0x37,
		KEY_ALT: 0x38, KEY_SPACE: 0x39, KEY_CAPSLOCK: 0x3A, KEY_NUMLOCK: 0x45,
		KEY_SCROLLLOCK: 0x46, KEY_HOME: 0x47, KEY_KP_7: 0x47, KEY_UP: 0x48, KEY_KP_8: 0x48,
		KEY_PAGEUP: 0x49, KEY_KP_9: 0x49, KEY_KP_SUBTRACT: 0x4A, KEY_LEFT: 0x4B,
		KEY_KP_4: 0x4B, KEY_KP_5: 0x4C, KEY_RIGHT: 0x4D, KEY_KP_6: 0x4D, KEY_KP_ADD: 0x4E,
		KEY_END: 0x4F, KEY_KP_1: 0x4F, KEY_DOWN: 0x50, KEY_KP_2: 0x50, KEY_PAGEDOWN: 0x51,
		KEY_KP_3: 0x51, KEY_INSERT: 0x52, KEY_KP_0: 0x52, KEY_DELETE: 0x53,
		KEY_KP_PERIOD: 0x53})
