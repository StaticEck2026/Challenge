## The program image baked by tools/recomp/recompile.py (res://recomp/program.bin).
##
## At run time NFL.EXE is not needed: the relocated root image and the MZ
## header values come from here (DOS.install_program loads them), and the
## overlay section of the file is served to the native overlay manager as a
## read-only virtual file (MemFile) under the program's name.
##
## Layout (little endian):
##   0   8  magic "NFLRCMP1" (an MZ .EXE) or "NFLRCOM1" (a .COM program)
##   8  20  SHA-1 of the NFL.EXE it was made from (also in recomp_index.gd)
##   28  2  load segment the root image is relocated for
##   30 14  MZ header: CS IP SS SP minalloc maxalloc relocation-count (u16 each)
##   44  4  root image size        48  4  size of the original file
##   52  4  file offset of the overlay section   56  4  its length
##   60 16  program file name (ASCII, NUL padded)
##   76     root image (relocated), then the overlay section (as in the file)
class_name ProgramBlob
extends RefCounted

const MAGIC := "NFLRCMP1"
const MAGIC_COM := "NFLRCOM1"
const HEADER_SIZE := 76

## "exe" or "com".  For a .COM program `image` is the whole file (loaded at
## PSP:0100) and there is no overlay section.
var kind: String = "exe"
var sha1: String = ""
var load_seg: int = 0
var cs: int = 0
var ip: int = 0
var ss: int = 0
var sp: int = 0
var minalloc: int = 0
var maxalloc: int = 0
var relocs: int = 0
var exe_size: int = 0
var ovl_off: int = 0
var file_name: String = ""
var image: PackedByteArray = PackedByteArray()
var overlays: PackedByteArray = PackedByteArray()
var error: String = ""


## Parses the blob; on failure `error` is set.
static func from_bytes(data: PackedByteArray) -> ProgramBlob:
	var b := ProgramBlob.new()
	var magic := data.slice(0, 8).get_string_from_ascii() if data.size() >= 8 else ""
	if data.size() < HEADER_SIZE or (magic != MAGIC and magic != MAGIC_COM):
		b.error = "not a recompiled program image"
		return b
	b.kind = "com" if magic == MAGIC_COM else "exe"
	b.sha1 = data.slice(8, 28).hex_encode()
	b.load_seg = data.decode_u16(28)
	b.cs = data.decode_u16(30)
	b.ip = data.decode_u16(32)
	b.ss = data.decode_u16(34)
	b.sp = data.decode_u16(36)
	b.minalloc = data.decode_u16(38)
	b.maxalloc = data.decode_u16(40)
	b.relocs = data.decode_u16(42)
	var image_size := data.decode_u32(44)
	b.exe_size = data.decode_u32(48)
	b.ovl_off = data.decode_u32(52)
	var ovl_len := data.decode_u32(56)
	b.file_name = data.slice(60, 76).get_string_from_ascii()
	if data.size() != HEADER_SIZE + image_size + ovl_len:
		b.error = "truncated program image"
		return b
	b.image = data.slice(HEADER_SIZE, HEADER_SIZE + image_size)
	b.overlays = data.slice(HEADER_SIZE + image_size)
	return b


static func from_file(path: String) -> ProgramBlob:
	if not FileAccess.file_exists(path):
		var b := ProgramBlob.new()
		b.error = "missing " + path
		return b
	return from_bytes(FileAccess.get_file_as_bytes(path))
