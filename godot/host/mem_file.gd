## Read-only in-memory file with the FileAccess calls dos.gd uses.  It serves
## the program's own executable (overlay section from ProgramBlob) to the
## overlay manager: bytes [origin, origin + data.size()) of a file of `length`
## bytes; anything else reads as zeros.
class_name MemFile
extends RefCounted

var data: PackedByteArray = PackedByteArray()
var origin: int = 0
var length: int = 0
var pos: int = 0


func _init(p_data: PackedByteArray, p_origin: int, p_length: int) -> void:
	data = p_data
	origin = p_origin
	length = p_length


func get_buffer(n: int) -> PackedByteArray:
	var out := PackedByteArray()
	var end := mini(pos + n, length)
	if end <= pos:
		return out
	out.resize(end - pos)
	for i in range(end - pos):
		var k := pos + i - origin
		out[i] = data[k] if k >= 0 and k < data.size() else 0
	pos = end
	return out


func seek(p: int) -> void:
	pos = p


func seek_end(p: int = 0) -> void:
	pos = length + p


func get_position() -> int:
	return pos


func get_length() -> int:
	return length


func store_buffer(_buf: PackedByteArray) -> void:
	pass


func flush() -> void:
	pass


func close() -> void:
	pass
