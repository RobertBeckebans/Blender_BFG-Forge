# BFG Forge
# Based on Level Buddy by Matt Lucas
# https://matt-lucas.itch.io/level-buddy

#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.	 If not, see <http://www.gnu.org/licenses/>.

bl_info = {
	'name': 'BFG Forge',
	'author': 'Jonathan Young',
	'category': 'Game Engine'
	}
	
import bpy, bpy.utils.previews, bmesh, glob, json, math, os
from bpy_extras.io_utils import ExportHelper
from collections import OrderedDict
from mathutils import Euler, Vector

# used when creating light and entities, and exporting
_scale_to_game = 64.0
_scale_to_blender = 1.0 / _scale_to_game

preview_collections = {}

################################################################################
## LEXER 
################################################################################

class Lexer:
	valid_token_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_/\\-.&:"
	valid_single_tokens = "{}[]()+-*/%!=<>,"

	def __init__(self, filename):
		self.line, self.pos = 1, 0
		with open(filename) as file:
			self.data = file.read()
			
	def eof(self):
		return self.pos >= len(self.data)
		
	def expect_token(self, token):
		t = self.parse_token()
		if not token == t:
			raise Exception("expected token \"%s\", got \"%s\" on line %d" % (token, t, self.line))
		
	def parse_token(self):
		self.skip_whitespace()
		if self.eof():
			return None
		start = self.pos
		while True:
			if self.eof():
				break
			c = self.data[self.pos]
			nc = self.data[self.pos + 1] if self.pos + 1 < len(self.data) else None
			if c == "\"":
				if not start == self.pos:
					raise Exception("quote in middle of token")
				self.pos += 1
				while True:
					if self.eof():
						raise Exception("eof in quoted token")
					c = self.data[self.pos]
					self.pos += 1
					if c == "\"":
						return self.data[start + 1:self.pos - 1]
			elif (c == "/" and nc == "/") or (c == "/" and nc == "*"):
				break
			elif not c in self.valid_token_chars:
				if c in self.valid_single_tokens:
					if self.pos == start:
						# single character token
						self.pos += 1
				break
			self.pos += 1
		end = self.pos
		return self.data[start:end]
		
	def skip_bracket_delimiter_section(self, opening, closing):
		self.expect_token(opening)
		num_required_closing = 1
		while True:
			token = self.parse_token()
			if token == None:
				break
			elif token == opening:
				num_required_closing += 1
			elif token == closing:
				num_required_closing -= 1
				if num_required_closing == 0:
					break
		
	def skip_whitespace(self):
		while True:
			if self.eof():
				break
			c = self.data[self.pos]
			nc = self.data[self.pos + 1] if self.pos + 1 < len(self.data) else None
			if c == "\n":
				self.line += 1
				self.pos += 1
			elif ord(c) <= ord(" "):
				self.pos += 1
			elif c == "/" and nc == "/":
				while True:
					if self.eof() or self.data[self.pos] == "\n":
						break
					self.pos += 1
			elif c == "/" and nc == "*":
				while True:
					if self.eof():
						break
					c = self.data[self.pos]
					nc = self.data[self.pos + 1] if self.pos + 1 < len(self.data) else None
					if c == "*" and nc == "/":
						self.pos += 2
						break
					self.pos += 1
			else:
				break
				
################################################################################
## FILE SYSTEM
################################################################################

class FileSystem:
	def __init__(self):
		# highest priority first
		self.search_dirs = []
		if bpy.context.scene.bfg.mod_dir:
			self.search_dirs.append(bpy.context.scene.bfg.mod_dir)
		self.search_dirs.append("basedev")
		self.search_dirs.append("base")
		
	def calculate_relative_path(self, filename):
		# e.g. if game_path is "D:\Games\DOOM 3",
		# "D:\Games\DOOM 3\basedev\models\mapobjects\arcade_machine\arcade_machine.lwo"
		# should return
		# "models\mapobjects\arcade_machine\arcade_machine.lwo"
		for search_dir in self.search_dirs:
			full_search_path = os.path.join(os.path.realpath(bpy.path.abspath(bpy.context.scene.bfg.game_path)), search_dir).lower()
			full_file_path = os.path.realpath(bpy.path.abspath(filename)).lower()
			if full_file_path.startswith(full_search_path):
				return os.path.relpath(full_file_path, full_search_path)
		return None
		
	def find_file_path(self, filename):
		for search_dir in self.search_dirs:
			full_path = os.path.join(os.path.realpath(bpy.path.abspath(bpy.context.scene.bfg.game_path)), search_dir, filename)
			if os.path.exists(full_path):
				return full_path
		return None
		
	def find_image_file_path(self, filename):
		path = self.find_file_path(filename)
		if not path:
			split = os.path.splitext(filename)
			if split[1] == "":
				# no extension, try tga
				path = self.find_file_path(split[0] + ".tga")
		return path
		
	def for_each_file(self, pattern, callback):
		# don't touch the same file more than once
		# e.g.
		# mymod/materials/base_wall.mtr
		# basedev/materials/base_wall.mtr
		# ignore the second one
		touched_files = []
		for search_dir in self.search_dirs:
			full_path = os.path.join(os.path.realpath(bpy.path.abspath(bpy.context.scene.bfg.game_path)), search_dir)
			if os.path.exists(full_path):
				for f in glob.glob(os.path.join(full_path, pattern)):
					base = os.path.basename(f)
					if not base in touched_files:
						touched_files.append(base)
						callback(full_path, f)

################################################################################
## UTILITY FUNCTIONS
################################################################################

def ftos(a):
	return ("%f" % a).rstrip('0').rstrip('.')
	
def set_object_mode_and_clear_selection():
	if bpy.context.active_object:
		bpy.ops.object.mode_set(mode='OBJECT')
	bpy.ops.object.select_all(action='DESELECT')
	
def link_active_object_to_group(group):
	if not group in bpy.data.groups:
		bpy.ops.group.create(name=group)
	bpy.ops.object.group_link(group=group)
						
################################################################################
## MATERIALS
################################################################################
					
class MaterialDeclPathPropGroup(bpy.types.PropertyGroup):
	pass # name property inherited
					
class MaterialDeclPropGroup(bpy.types.PropertyGroup):
	# name property inherited
	diffuse_texture = bpy.props.StringProperty()
	editor_texture = bpy.props.StringProperty()
	heightmap_scale = bpy.props.FloatProperty() # 0 if normal_texture isn't a heightmap
	normal_texture = bpy.props.StringProperty()
	specular_texture = bpy.props.StringProperty()
	texture = bpy.props.StringProperty() # any stage texture map. will be the light texture for light materials.
	
def material_decl_preview_items(self, context):
	materials = []
	pcoll = preview_collections["material"]
	if pcoll.current_decl_path == context.scene.bfg.active_material_decl_path and not pcoll.force_refresh:
		return pcoll.materials
	fs = FileSystem()
	i = 0
	for decl in context.scene.bfg.material_decls:
		if os.path.dirname(decl.name) == context.scene.bfg.active_material_decl_path:
			if context.scene.bfg.hide_bad_materials and (decl.diffuse_texture == "" or not fs.find_image_file_path(decl.diffuse_texture)):
				continue # hide materials with missing diffuse texture
			if decl.editor_texture in pcoll: # workaround blender bug, pcoll.load is supposed to return cached preview if name already exists
				preview = pcoll[decl.editor_texture]
			else:
				preview = None
				if decl.editor_texture != "":
					filename = fs.find_image_file_path(decl.editor_texture)
					if filename:
						preview = pcoll.load(decl.editor_texture, filename, 'IMAGE')
			materials.append((decl.name, os.path.basename(decl.name), decl.name, preview.icon_id if preview else 0, i))
			i += 1
	materials.sort()
	pcoll.materials = materials
	pcoll.current_decl_path = context.scene.bfg.active_material_decl_path
	pcoll.force_refresh = False
	return pcoll.materials
					
class ImportMaterials(bpy.types.Operator):
	bl_idname = "scene.import_materials"
	bl_label = "Import Materials"
	
	def __init__(self):
		self.num_materials_created = 0
		self.num_materials_updated = 0
		
	def parse_heightmap(self, decl, lex):
		lex.expect_token("(")
		texture = lex.parse_token()
		lex.expect_token(",")
		scale = float(lex.parse_token())
		lex.expect_token(")")
		return (texture, scale)

	def parse_material_file(self, search_path, filename):
		lex = Lexer(filename)
		num_materials_created = 0
		num_materials_updated = 0
		scene = bpy.context.scene
		print("Parsing", os.path.basename(filename), "...", end="", flush=True)
		while True:
			token = lex.parse_token()
			if token == None:
				break
			if token in [ "particle", "skin", "table"]:
				lex.parse_token() # name
				lex.skip_bracket_delimiter_section("{", "}")
			else:
				if token == "material":
					name = lex.parse_token()
				else:
					name = token
				if name in scene.bfg.material_decls:
					decl = scene.bfg.material_decls[name]
					num_materials_updated += 1
				else:
					num_materials_created += 1
					decl = scene.bfg.material_decls.add()
					decl.name = name
				lex.expect_token("{")
				num_required_closing = 1
				in_stage = False
				stage_blend = None
				stage_heightmap_scale = 0
				stage_texture = None 
				while True:
					token = lex.parse_token()
					if token == None:
						break
					elif token == "{":
						num_required_closing += 1
						if num_required_closing == 2:
							# 2nd opening brace: now in a stage
							in_stage = True
							stage_blend = None
							stage_heightmap_scale = 0
							stage_texture = None
					elif token == "}":
						num_required_closing -= 1
						if num_required_closing == 0:
							break
						elif num_required_closing == 1:
							# one closing brace left: closing stage
							in_stage = False
							if stage_texture:
								decl.texture = stage_texture # any stage texture map. will be the light texture for light materials.
							if stage_blend and stage_texture:
								if stage_blend.lower() == "bumpmap":
									decl.normal_texture = stage_texture
									decl.heightmap_scale = stage_heightmap_scale
								elif stage_blend.lower() == "diffusemap":
									decl.diffuse_texture = stage_texture
								elif stage_blend.lower() == "specularmap":
									decl.specular_texture = stage_texture
					if in_stage:
						if token.lower() == "blend":
							stage_blend = lex.parse_token()
						elif token.lower() == "map":
							token = lex.parse_token()
							if token.lower() == "heightmap":
								(stage_texture, stage_heightmap_scale) = self.parse_heightmap(decl, lex)
							else:
								stage_texture = token
					else:
						if token.lower() == "bumpmap":
							token = lex.parse_token()
							if token.lower() == "heightmap":
								(decl.normal_texture, decl.heightmap_scale) = self.parse_heightmap(decl, lex)
							else:
								decl.normal_texture = token
						elif token.lower() == "diffusemap":
							decl.diffuse_texture = lex.parse_token()
						elif token.lower() == "qer_editorimage":
							decl.editor_texture = lex.parse_token()
						elif token.lower() == "specularmap":
							decl.specular_texture = lex.parse_token()
		print(" %d materials" % (num_materials_created + num_materials_updated))
		return (num_materials_created, num_materials_updated)
		
	def update_material_decl_paths(self, scene):
		scene.bfg.material_decl_paths.clear()
		for decl in scene.bfg.material_decls:
			name = os.path.dirname(decl.name)
			if name.startswith("textures") and not name in scene.bfg.material_decl_paths:
				path = scene.bfg.material_decl_paths.add()
				path.name = name
									
	def execute(self, context):
		if context.scene.bfg.game_path:
			self.num_materials_created = 0
			self.num_materials_updated = 0
		
			def pmf(search_path, filename):
				result = self.parse_material_file(search_path, filename)
				self.num_materials_created += result[0]
				self.num_materials_updated += result[1]

			fs = FileSystem()
			fs.for_each_file(r"materials\*.mtr", pmf)
			self.update_material_decl_paths(context.scene)
			preview_collections["light"].needs_refresh = True
			self.report({'INFO'}, "Imported %d materials, updated %d" % (self.num_materials_created, self.num_materials_updated))
		else:
			self.report({'ERROR'}, "RBDOOM-3-BFG path not set")
		return {'FINISHED'}
		
def create_material_texture(fs, mat, texture, slot_number):
	# textures may be shared between materials, so don't create one that already exists
	if texture in bpy.data.textures:
		tex = bpy.data.textures[texture]
	else:
		tex = bpy.data.textures.new(texture, type='IMAGE')
		
	# texture image may have changed
	img_filename = fs.find_image_file_path(texture)
	if img_filename:
		# try to use relative paths for image filenames
		print(img_filename)
		try:
			img_filename = bpy.path.relpath(img_filename)
		except ValueError:
			pass
	if not tex.image or tex.image.filepath != img_filename:
		try:
			img = bpy.data.images.load(img_filename)
		except:
			pass
		else:
		   tex.image = img	 
	
	# update/create the texture slot
	if not mat.texture_slots[slot_number] or not mat.texture_slots[slot_number].name == texture:
		texSlot = mat.texture_slots.create(slot_number)
		texSlot.texture_coords = 'UV'
		texSlot.texture = tex
	
	return (tex, mat.texture_slots[slot_number])
		
def create_material(decl):
	if decl.name in bpy.data.materials:
		mat = bpy.data.materials[decl.name]
	else:
		mat = bpy.data.materials.new(decl.name)
	mat.use_shadeless = bpy.context.scene.bfg.shadeless_materials
	fs = FileSystem()
	if decl.diffuse_texture != "":
		create_material_texture(fs, mat, decl.diffuse_texture, 0)
	if decl.normal_texture != "":
		(tex, slot) = create_material_texture(fs, mat, decl.normal_texture, 1)
		slot.use_map_color_diffuse = False
		if decl.heightmap_scale > 0:
			slot.use_map_displacement = True
			slot.displacement_factor = decl.heightmap_scale
		else:
			tex.use_normal_map = True
			slot.use_map_normal = True
	if decl.specular_texture != "":
		(_, slot) = create_material_texture(fs, mat, decl.specular_texture, 2)
		slot.use_map_color_diffuse = False
		slot.use_map_color_spec = True
		slot.use_map_specular = True
	return mat
	
def get_or_create_active_material(context):
	bfg = context.scene.bfg
	if bfg.active_material_decl in context.scene.bfg.material_decls:
		return create_material(context.scene.bfg.material_decls[bfg.active_material_decl])
	return None
	
class AssignMaterial(bpy.types.Operator):
	"""Assign the material to the selected objects or object faces"""
	bl_idname = "scene.assign_material"
	bl_label = "Assign"
	where = bpy.props.StringProperty(name="where", default='ALL')
	
	def assign_to_object(self, obj, mat):
		if obj.bfg.type == '2D_ROOM':
			if self.where == 'CEILING' or self.where == 'ALL':
				obj.bfg.ceiling_material = mat.name
			if self.where == 'WALL' or self.where == 'ALL':
				obj.bfg.wall_material = mat.name
			if self.where == 'FLOOR' or self.where == 'ALL':
				obj.bfg.floor_material = mat.name
			update_room_plane_materials(obj)
		else:
			if len(obj.data.materials) == 1:
				# one slot: easy, just reassign
				obj.data.materials[0] = mat
			else:
				obj.data.materials.clear()
				obj.data.materials.append(mat)
				
				# there was more than one material slot on this object
				# need to set material_index on all faces to 0
				bm = bmesh.new()
				bm.from_mesh(obj.data)
				for f in bm.faces:
					f.material_index = 0
				bm.to_mesh(obj.data)
				bm.free()
	
	def execute(self, context):
		obj = context.active_object
		if not obj or not hasattr(obj.data, "materials"):
			return {'FINISHED'}
		mat = get_or_create_active_material(context)
		if not mat:
			return {'FINISHED'}
		if obj.mode == 'EDIT':
			# edit mode: assign to selected mesh faces
			bm = bmesh.from_edit_mesh(obj.data)
			selected_faces = [f for f in bm.faces if f.select]
			if len(selected_faces) > 0:
				# create/find a slot
				material_index = -1
				for i, m in enumerate(obj.data.materials):
					if m == mat:
						material_index = i
						break
				if material_index == -1:
					obj.data.materials.append(mat)
					material_index = len(obj.data.materials) - 1
					
				# assign to faces
				for f in selected_faces:
					f.material_index = material_index
					
				# remove any material slots that are now unused
				# pop function update_data arg doesn't work, need to remap face material_index ourselves after removal
				old_material_names = []
				for m in obj.data.materials:
					old_material_names.append(m.name)
				remove_materials = []
				for i, m in enumerate(obj.data.materials):
					used = False
					for f in bm.faces:
						if f.material_index == i:
							used = True
							break
					if not used:
						remove_materials.append(m)
				if len(remove_materials) > 0:
					for m in remove_materials:
						obj.data.materials.pop(obj.data.materials.find(m.name), True)
				for f in bm.faces:
					f.material_index = obj.data.materials.find(old_material_names[f.material_index])
					
				bmesh.update_edit_mesh(obj.data)
			#bm.free() # bmesh.from_edit_mesh returns garbage after this is called
		else:
			for s in context.selected_objects:
				if hasattr(s.data, "materials"):
					self.assign_to_object(s, mat)
		return {'FINISHED'}
		
def refresh_object_materials(context, obj):
	if hasattr(obj.data, "materials"):
		for mat in obj.data.materials:
			if mat.name in context.scene.bfg.material_decls:
				decl = context.scene.bfg.material_decls[mat.name]
				create_material(decl)
		
class RefreshMaterials(bpy.types.Operator):
	"""Refresh the active object's materials, recreating them from their corresponding material decls"""
	bl_idname = "scene.refresh_materials"
	bl_label = "Refresh Materials"
	
	def execute(self, context):
		obj = context.active_object
		if obj:
			refresh_object_materials(context, obj)
		return {'FINISHED'}
		
################################################################################
## ENTITIES
################################################################################
		
class EntityPropGroup(bpy.types.PropertyGroup):
	# name property inherited
	color = bpy.props.StringProperty()
	usage = bpy.props.StringProperty()
	mins = bpy.props.StringProperty()
	maxs = bpy.props.StringProperty()

class ImportEntities(bpy.types.Operator):
	bl_idname = "scene.import_entities"
	bl_label = "Import Entities"
	
	def parse_def_file(self, scene, search_path, filename):
		lex = Lexer(filename)
		num_entities_created = 0
		num_entities_updated = 0
		print("Parsing", os.path.basename(filename), "...", end="", flush=True)
		while True:
			token = lex.parse_token()
			if token == None:
				break
			if not token == "entityDef":
				lex.parse_token() # name
				lex.skip_bracket_delimiter_section("{", "}")
			else:
				name = lex.parse_token()
				if name in scene.bfg.entities:
					entity = scene.bfg.entities[name]
					num_entities_updated += 1
				else:
					entity = scene.bfg.entities.add()
					entity.name = name
					num_entities_created += 1
				entity.color = "0 0 1" # "r g b"
				entity.mins = ""
				entity.maxs = ""
				entity.usage = ""
				lex.expect_token("{")
				num_required_closing = 1
				while True:
					token = lex.parse_token()
					if token == None:
						break
					elif token == "{":
						num_required_closing += 1
					elif token == "}":
						num_required_closing -= 1
						if num_required_closing == 0:
							break
					elif token == "editor_color":
						entity.color = lex.parse_token()
					elif token == "editor_mins":
						entity.mins = lex.parse_token()
					elif token == "editor_maxs":
						entity.maxs = lex.parse_token()
					elif token == "editor_usage":
						entity.usage = lex.parse_token()
		print(" %d entities" % (num_entities_created + num_entities_updated))
		return (num_entities_created, num_entities_updated)
	
	def execute(self, context):
		if context.scene.bfg.game_path:
			self.num_entities_created = 0
			self.num_entities_updated = 0
		
			def pdf(search_path, filename):
				result = self.parse_def_file(context.scene, search_path, filename)
				self.num_entities_created += result[0]
				self.num_entities_updated += result[1]

			fs = FileSystem()
			fs.for_each_file(r"def\*.def", pdf)
			self.report({'INFO'}, "Imported %d entities, updated %d" % (self.num_entities_created, self.num_entities_updated))
		else:
			self.report({'ERROR'}, "RBDOOM-3-BFG path not set")
		return {'FINISHED'}
		
def create_object_color_material():
	name = "_object_color"
	# create the material if it doesn't exist
	if name in bpy.data.materials:
		mat = bpy.data.materials[name]
	else:
		mat = bpy.data.materials.new(name)
	mat.use_fake_user = True
	mat.use_object_color = True
	mat.use_shadeless = True

class AddEntity(bpy.types.Operator):
	bl_idname = "scene.add_entity"
	bl_label = "Add Entity"
	
	def execute(self, context):
		ae = context.scene.bfg.active_entity
		if ae != None and ae != "":
			entity = context.scene.bfg.entities[ae]
			create_object_color_material()
			set_object_mode_and_clear_selection()
			bpy.ops.mesh.primitive_cube_add()
			obj = context.active_object
			obj.bfg.type = 'ENTITY'
			obj.bfg.classname = ae
			obj.name = ae
			obj.color = [float(i) for i in entity.color.split()] + [float(0.5)] # "r g b"
			obj.data.name = ae
			obj.data.materials.append(bpy.data.materials["_object_color"])
			obj.lock_rotation = [True, True, False]
			obj.lock_scale = [True, True, True]
			obj.show_axis = True # x will be forward
			obj.show_wire = True
			obj.show_transparent = True
			context.scene.objects.active = obj
			link_active_object_to_group("entities")
			context.object.hide_render = True

			# set entity dimensions
			mins = Vector([float(i) * _scale_to_blender for i in entity.mins.split()])
			maxs = Vector([float(i) * _scale_to_blender for i in entity.maxs.split()])
			size = maxs + -mins
			obj.dimensions = size
			
			# set entity origin
			origin = (mins + maxs) / 2.0
			bpy.ops.object.editmode_toggle()
			bpy.ops.mesh.select_all(action='SELECT')
			bpy.ops.transform.translate(value=origin)
			bpy.ops.object.editmode_toggle()
		return {'FINISHED'}
		
################################################################################
## LIGHTS
################################################################################
		
class AddLight(bpy.types.Operator):
	bl_idname = "scene.add_light"
	bl_label = "Add Light"
	
	def execute(self, context):
		set_object_mode_and_clear_selection()
		data = bpy.data.lamps.new(name="Light", type='POINT')
		obj = bpy.data.objects.new(name="Light", object_data=data)
		context.scene.objects.link(obj)
		obj.select = True
		context.scene.objects.active = obj
		obj.data.distance = 300.0 * _scale_to_blender
		obj.data.energy = obj.data.distance
		#obj.scale = obj.distance
		#obj.show_bounds = True
		#obj.draw_bounds_type = 'SPHERE'
		obj.data.use_sphere = True
		link_active_object_to_group("lights")
		return {'FINISHED'}
		
def get_light_radius(self):
	return self.data.distance
	
def set_light_radius(self, value):
	self.data.distance = value
	self.data.energy = value
	
def light_material_preview_items(self, context):
	lights = []
	pcoll = preview_collections["light"]
	if not pcoll.needs_refresh:
		return pcoll.lights
	fs = FileSystem()
	lights.append(("default", "default", "default", 0, 0))
	i = 1
	for decl in context.scene.bfg.material_decls:
		# material name must start with "lights" and its texture file must exists
		if os.path.dirname(decl.name).startswith("lights") and decl.texture:
			if decl.texture == "":
				continue
			filename = fs.find_image_file_path(decl.texture)
			if not filename:
				continue
			if decl.texture in pcoll: # workaround blender bug, pcoll.load is supposed to return cached preview if name already exists
				preview = pcoll[decl.texture]
			else:
				preview = pcoll.load(decl.texture, filename, 'IMAGE')
			lights.append((decl.name, os.path.basename(decl.name), decl.name, preview.icon_id, i))
			i += 1
	lights.sort()
	pcoll.lights = lights
	pcoll.needs_refresh = False
	return pcoll.lights
	
################################################################################
## STATIC MODELS
################################################################################
		
class AddStaticModel(bpy.types.Operator):
	"""Browse for a static model to add"""
	bl_idname = "scene.add_static_model"
	bl_label = "Add Static Model"
	filepath = bpy.props.StringProperty(default="", options={'HIDDEN', 'SKIP_SAVE'})
	filter_glob = bpy.props.StringProperty(default="*.lwo", options={'HIDDEN'})
	
	def execute(self, context):
		# the func_static entity model value looks like this:
		# "models/mapobjects/arcade_machine/arcade_machine.lwo"
		# so the file path must descend from one of the search paths
		fs = FileSystem()
		relative_path = fs.calculate_relative_path(self.properties.filepath)
		if not relative_path:
			self.report({'ERROR'}, "File path must descend from \"%s\"" % context.scene.bfg.game_path)
			return {'FINISHED'}
				
		# check that the required import addon is enabled
		extension = os.path.splitext(self.properties.filepath)[1]
		if extension.lower() == ".lwo":
			if not hasattr(bpy.types, "IMPORT_SCENE_OT_lwo"):
				self.report({'ERROR'}, "LightWave Object (.lwo) import addon not enabled")
				return {'FINISHED'}
		else:
			self.report({'ERROR'}, "Unsupported extension \"%s\"" % extension)
			return {'FINISHED'}
		
		set_object_mode_and_clear_selection()
		
		# if the model has already been loaded before, don't import - link to the existing mesh
		mesh = None
		for obj in context.scene.objects:
			if obj.bfg.type == 'STATIC_MODEL' and obj.bfg.entity_model == relative_path:
				mesh = obj.data
				break
		if mesh:
			obj = bpy.data.objects.new(os.path.splitext(os.path.basename(relative_path))[0], mesh)
			context.scene.objects.link(obj)
		else:
			# lwo importer doesn't select or make active the object in creates...
			# need to diff scene objects before and after import to find it
			obj_names = []
			for obj in context.scene.objects:
				obj_names.append(obj.name)
			bpy.ops.import_scene.lwo(filepath=self.properties.filepath, USE_EXISTING_MATERIALS=True)
			imported_obj = None
			for obj in context.scene.objects:
				if not obj.name in obj_names:
					imported_obj = obj
					break
			if not imported_obj:
				return {'FINISHED'} # import must have failed
			obj = imported_obj
		context.scene.objects.active = obj
		obj.select = True
		obj.bfg.type = 'STATIC_MODEL'
		obj.bfg.classname = "func_static"
		obj.bfg.entity_model = relative_path
		obj.scale = [_scale_to_blender, _scale_to_blender, _scale_to_blender]
		obj.lock_scale = [True, True, True]
		link_active_object_to_group("static models")
		refresh_object_materials(context, obj)
		return {'FINISHED'}

	def invoke(self, context, event):
		context.window_manager.fileselect_add(self)
		return {'RUNNING_MODAL'}
	
################################################################################
## MAP
################################################################################

def update_room_plane_modifier(obj):
	if obj.modifiers:
		mod = obj.modifiers[0]
		if mod.type == 'SOLIDIFY':
			mod.thickness = obj.bfg.room_height
			mod.material_offset = 1
			mod.material_offset_rim = 2

def update_room_plane_materials(obj):
	if bpy.data.materials.find(obj.bfg.ceiling_material) != -1:
		obj.material_slots[0].material = bpy.data.materials[obj.bfg.ceiling_material]
	if bpy.data.materials.find(obj.bfg.floor_material) != -1:
		obj.material_slots[1].material = bpy.data.materials[obj.bfg.floor_material]
	if bpy.data.materials.find(obj.bfg.wall_material) != -1:
		obj.material_slots[2].material = bpy.data.materials[obj.bfg.wall_material]

def update_room(self, context):
	obj = context.active_object
	if obj.bfg.type == '2D_ROOM':
		update_room_plane_modifier(obj)
		update_room_plane_materials(obj)

def apply_boolean(dest, src, bool_op):
	bpy.ops.object.select_all(action='DESELECT')
	dest.select = True
	me = src.to_mesh(bpy.context.scene, True, 'PREVIEW')
	ob_bool = bpy.data.objects.new("_bool", me)
	
	# copy transform
	ob_bool.location = src.location
	ob_bool.scale = src.scale
	ob_bool.rotation_euler = src.rotation_euler
	
	# copy materials
	for mat in src.data.materials:
		if not mat.name in dest.data.materials:
			dest.data.materials.append(mat)	
			
	mod = dest.modifiers.new(name=src.name, type='BOOLEAN')
	mod.object = ob_bool
	mod.operation = bool_op
	bpy.ops.object.modifier_apply(apply_as='DATA', modifier=src.name)

def flip_object_normals(obj):
	bpy.ops.object.select_all(action='DESELECT')
	obj.select = True
	bpy.ops.object.editmode_toggle()
	bpy.ops.mesh.select_all(action='SELECT')
	bpy.ops.mesh.flip_normals()
	bpy.ops.object.editmode_toggle()
	
def auto_texture(obj):
	bpy.ops.object.select_all(action='DESELECT')
	obj.select = True
	bpy.ops.object.editmode_toggle()
	bpy.ops.mesh.select_all(action='SELECT')
	bpy.ops.object.auto_uv_unwrap()
	bpy.ops.object.editmode_toggle()

def move_object_to_layer(obj, layer_number):
	layers = 20 * [False]
	layers[layer_number] = True
	obj.layers = layers

def add_all_materials(obj):
	i = 0
	for m in bpy.data.materials:
		if len(obj.data.materials) > i:
			has_material = False
			for mat in obj.data.materials:
				if mat.name == m.name:
					has_material = True
			if not has_material:
				obj.data.materials[i] = m
		else:
			obj.data.materials.append(m)
		i += 1
		
class AddRoom(bpy.types.Operator):
	bl_idname = "scene.add_room"
	bl_label = "Add Room"

	def execute(self, context):
		scene = context.scene
		set_object_mode_and_clear_selection()
		bpy.ops.mesh.primitive_plane_add(radius=1)
		bpy.ops.object.modifier_add(type='SOLIDIFY')
		obj = context.active_object
		obj.modifiers['Solidify'].offset = 1
		obj.modifiers['Solidify'].use_even_offset = True
		obj.modifiers['Solidify'].use_quality_normals = True
		obj.name = "room2D"
		obj.data.name = "room2D"
		obj.bfg.room_height = 4
		obj.bfg.type = '2D_ROOM'
		if context.scene.bfg.wireframe_rooms:
			obj.draw_type = 'WIRE'
		obj.game.physics_type = 'NO_COLLISION'
		obj.hide_render = True
		if len(bpy.data.materials) > 0:
			mat = get_or_create_active_material(context)
			if mat:
				obj.data.materials.append(mat)
				obj.data.materials.append(mat)
				obj.data.materials.append(mat)
				obj.bfg.ceiling_material = mat.name
				obj.bfg.wall_material = mat.name
				obj.bfg.floor_material = mat.name
			else:
				obj.data.materials.append(bpy.data.materials[0])
				obj.data.materials.append(bpy.data.materials[0])
				obj.data.materials.append(bpy.data.materials[0])
				obj.bfg.ceiling_material = bpy.data.materials[0].name
				obj.bfg.wall_material = bpy.data.materials[0].name
				obj.bfg.floor_material = bpy.data.materials[0].name
		else:
			bpy.ops.object.material_slot_add()
			bpy.ops.object.material_slot_add()
			bpy.ops.object.material_slot_add()
			obj.bfg.ceiling_material = ""
			obj.bfg.wall_material = ""
			obj.bfg.floor_material = ""
		scene.objects.active = obj
		update_room_plane_modifier(obj)
		update_room_plane_materials(obj)
		link_active_object_to_group("rooms")
		return {'FINISHED'}

class AddBrush(bpy.types.Operator):
	bl_idname = "scene.add_brush"
	bl_label = "Add Brush"
	s_type = bpy.props.StringProperty(name="s_type", default='BRUSH')

	def execute(self, context):
		scene = context.scene
		set_object_mode_and_clear_selection()
		bpy.ops.mesh.primitive_cube_add(radius=1)
		obj = context.active_object
		if context.scene.bfg.wireframe_rooms:
			obj.draw_type = 'WIRE'
		if self.s_type == '3D_ROOM':
			obj.name = "room3D"
			obj.data.name = "room3D"
		else:
			obj.name = "brush"
			obj.data.name = "brush"
		obj.bfg.type = self.s_type
		mat = get_or_create_active_material(context)
		if mat:
			obj.data.materials.append(mat)
		scene.objects.active = obj
		bpy.ops.object.editmode_toggle()
		bpy.ops.mesh.select_all(action='SELECT')
		bpy.ops.object.auto_uv_unwrap()
		bpy.ops.object.editmode_toggle()
		obj.game.physics_type = 'NO_COLLISION'
		obj.hide_render = True
		if self.s_type == '3D_ROOM':
			link_active_object_to_group("rooms")
		else:
			link_active_object_to_group("brushes")
		return {'FINISHED'}
		
class CopyRoom(bpy.types.Operator):
	bl_idname = "scene.copy_room"
	bl_label = "Copy Room"
	copy_op = bpy.props.StringProperty(name="copy_op", default='ALL')

	def execute(self, context):
		obj = context.active_object
		selected_objects = context.selected_objects
		for s in selected_objects:
			if s.bfg.type == '2D_ROOM':
				if self.copy_op == 'HEIGHT' or self.copy_op == 'ALL':
					s.bfg.room_height = obj.bfg.room_height
				if self.copy_op == 'MATERIAL_CEILING' or self.copy_op == 'MATERIAL_ALL' or self.copy_op == 'ALL':
					s.bfg.ceiling_material = obj.bfg.ceiling_material
				if self.copy_op == 'MATERIAL_WALL' or self.copy_op == 'MATERIAL_ALL' or self.copy_op == 'ALL':
					s.bfg.wall_material = obj.bfg.wall_material
				if self.copy_op == 'MATERIAL_FLOOR' or self.copy_op == 'MATERIAL_ALL' or self.copy_op == 'ALL':
					s.bfg.floor_material = obj.bfg.floor_material
				update_room_plane_modifier(s)
				update_room_plane_materials(s)
		return {'FINISHED'}

class BuildMap(bpy.types.Operator):
	bl_idname = "scene.build_map"
	bl_label = "Build Map"
	bool_op = bpy.props.StringProperty(name="bool_op", default='INTERSECT')

	def execute(self, context):
		scene = context.scene
		
		# get rooms and brushes
		room_list = []
		brush_list = []
		for obj in context.visible_objects:
			if obj.bfg.type in ['2D_ROOM', '3D_ROOM']:
				room_list.append(obj)
			elif obj.bfg.type == 'BRUSH':
				brush_list.append(obj)
					
		# get all the temp bool objects from the last time the map was built
		bool_objects = [obj for obj in bpy.data.objects if obj.name.startswith("_bool")]
					
		# create map object
		# if a map object already exists, its old mesh is removed
		# if there is at least one room, it is used as the starting point for the map mesh, otherwise an empty mesh is created
		set_object_mode_and_clear_selection()
		old_map_mesh = None
		map_name = "_map"
		map_mesh_name = map_name + "_mesh"
		if map_mesh_name in bpy.data.meshes:
			old_map_mesh = bpy.data.meshes[map_mesh_name]
			old_map_mesh.name = "map_old"
		if len(room_list) > 0:
			# first room: generate the mesh and transform to worldspace
			map_mesh = room_list[0].to_mesh(scene, True, 'PREVIEW')
			map_mesh.name = map_mesh_name
			map_mesh.transform(room_list[0].matrix_world)
		else:
			map_mesh = bpy.data.meshes.new(map_mesh_name)
		if map_name in bpy.data.objects:
			map = bpy.data.objects[map_name]
			map.data = map_mesh
		else:
			map = bpy.data.objects.new(map_name, map_mesh)
			scene.objects.link(map)
		if old_map_mesh:
			bpy.data.meshes.remove(old_map_mesh)
		map.layers[scene.active_layer] = True
		scene.objects.active = map
		map.select = True
					
		# combine rooms
		for i, room in enumerate(room_list):
			if i > 0:
				# not the first room: bool union with existing mesh
				apply_boolean(map, room, 'UNION')
		if len(room_list) > 0:
			flip_object_normals(map)
			
		# combine brushes
		for brush in brush_list:
			apply_boolean(map, brush, 'UNION')
			
		auto_texture(map)
		link_active_object_to_group("worldspawn")
		move_object_to_layer(map, scene.bfg.map_layer)
		map.hide_select = True
		bpy.ops.object.select_all(action='DESELECT')
		
		# cleanup temp bool objects
		for obj in bool_objects:
			mesh = obj.data
			bpy.data.objects.remove(obj)
			bpy.data.meshes.remove(mesh)

		return {'FINISHED'}
		
################################################################################
## UV UNWRAPPING
################################################################################

class AutoUnwrap(bpy.types.Operator):
	bl_idname = "object.auto_uv_unwrap"
	bl_label = "Unwrap"
	axis = bpy.props.StringProperty(name="Axis", default='AUTO')

	def execute(self, context):
		obj = context.active_object
		me = obj.data
		objectLocation = context.active_object.location
		objectScale = context.active_object.scale
		texelDensity = context.scene.bfg.texel_density
		textureWidth = 64
		textureHeight = 64
		if bpy.context.mode == 'EDIT_MESH' or bpy.context.mode == 'OBJECT':
			was_obj_mode = False
			if bpy.context.mode == 'OBJECT':
				was_obj_mode = True
				bpy.ops.object.editmode_toggle()
				bpy.ops.mesh.select_all(action='SELECT')
			bm = bmesh.from_edit_mesh(me)
			uv_layer = bm.loops.layers.uv.verify()
			bm.faces.layers.tex.verify()  # currently blender needs both layers.
			for f in bm.faces:
				if f.select:
					bpy.ops.uv.select_all(action='SELECT')
					matIndex = f.material_index
					if len(obj.data.materials) > matIndex:
						if obj.data.materials[matIndex] is not None:
							tex = context.active_object.data.materials[matIndex].active_texture
							if tex:
								if hasattr(tex, "image") and tex.image: # if the texture type isn't set to "Image or Movie", the image attribute won't exist
									textureWidth = tex.image.size[0]
									textureHeight = tex.image.size[1]
								nX = f.normal.x
								nY = f.normal.y
								nZ = f.normal.z
								if nX < 0:
									nX = nX * -1
								if nY < 0:
									nY = nY * -1
								if nZ < 0:
									nZ = nZ * -1
								faceNormalLargest = nX
								faceDirection = 'x'
								if faceNormalLargest < nY:
									faceNormalLargest = nY
									faceDirection = 'y'
								if faceNormalLargest < nZ:
									faceNormalLargest = nZ
									faceDirection = 'z'
								if faceDirection == 'x':
									if f.normal.x < 0:
										faceDirection = '-x'
								if faceDirection == 'y':
									if f.normal.y < 0:
										faceDirection = '-y'
								if faceDirection == 'z':
									if f.normal.z < 0:
										faceDirection = '-z'
								if self.axis == 'X':
									faceDirection = 'x'
								if self.axis == 'Y':
									faceDirection = 'y'
								if self.axis == 'Z':
									faceDirection = 'z'
								if self.axis == '-X':
									faceDirection = '-x'
								if self.axis == '-Y':
									faceDirection = '-y'
								if self.axis == '-Z':
									faceDirection = '-z'
								for l in f.loops:
									luv = l[uv_layer]
									if luv.select and l[uv_layer].pin_uv is not True:
										if faceDirection == 'x':
											luv.uv.x = ((l.vert.co.y * objectScale[1]) + objectLocation[1]) * texelDensity / textureWidth
											luv.uv.y = ((l.vert.co.z * objectScale[2]) + objectLocation[2]) * texelDensity / textureWidth
										if faceDirection == '-x':
											luv.uv.x = (((l.vert.co.y * objectScale[1]) + objectLocation[1]) * texelDensity / textureWidth) * -1
											luv.uv.y = ((l.vert.co.z * objectScale[2]) + objectLocation[2]) * texelDensity / textureWidth
										if faceDirection == 'y':
											luv.uv.x = (((l.vert.co.x * objectScale[0]) + objectLocation[0]) * texelDensity / textureWidth) * -1
											luv.uv.y = ((l.vert.co.z * objectScale[2]) + objectLocation[2]) * texelDensity / textureWidth
										if faceDirection == '-y':
											luv.uv.x = ((l.vert.co.x * objectScale[0]) + objectLocation[0]) * texelDensity / textureWidth
											luv.uv.y = ((l.vert.co.z * objectScale[2]) + objectLocation[2]) * texelDensity / textureWidth
										if faceDirection == 'z':
											luv.uv.x = ((l.vert.co.x * objectScale[0]) + objectLocation[0]) * texelDensity / textureWidth
											luv.uv.y = ((l.vert.co.y * objectScale[1]) + objectLocation[1]) * texelDensity / textureWidth
										if faceDirection == '-z':
											luv.uv.x = (((l.vert.co.x * objectScale[0]) + objectLocation[0]) * texelDensity / textureWidth) * 1
											luv.uv.y = (((l.vert.co.y * objectScale[1]) + objectLocation[1]) * texelDensity / textureWidth) * -1
										luv.uv.x = luv.uv.x - context.scene.bfg.offset_x
										luv.uv.y = luv.uv.y - context.scene.bfg.offset_y
			bmesh.update_edit_mesh(me)
			if was_obj_mode:
				bpy.ops.object.editmode_toggle()
		return {'FINISHED'}

class PinUV(bpy.types.Operator):
	bl_idname = "object.auto_uv_pin"
	bl_label = "Pin UV"
	p = bpy.props.BoolProperty(name="tp", default=True)

	def execute(self, context):
		obj = bpy.context.object
		if obj.mode == 'EDIT':
			me = obj.data
			bm = bmesh.from_edit_mesh(me)
			uv_layer = bm.loops.layers.uv.verify()
			bm.faces.layers.tex.verify()
			bpy.ops.uv.pin(clear=self.p)
			bmesh.update_edit_mesh(me)
		return {'FINISHED'}

class NudgeUV(bpy.types.Operator):
	bl_idname = "object.auto_uv_nudge"
	bl_label = "Nudge UV"
	dir = bpy.props.StringProperty(name="Some Floating Point", default='LEFT')

	def execute(self, context):
		obj = context.active_object
		me = obj.data
		bm = bmesh.from_edit_mesh(me)
		uv_layer = bm.loops.layers.uv.verify()
		bm.faces.layers.tex.verify()  # currently blender needs both layers.

		# adjust UVs on all selected faces
		for f in bm.faces:
			# is this face currently selected?
			if f.select:
				# make sure that all the uvs for the face are selected
				bpy.ops.uv.select_all(action='SELECT')
				# loop through the face uvs
				for l in f.loops:
					luv = l[uv_layer]
					# only work on the selected UV layer
					if luv.select:
						if self.dir == 'LEFT':
							luv.uv.x = luv.uv.x + context.scene.bfg.nudge_amount
						if self.dir == 'RIGHT':
							luv.uv.x = luv.uv.x - context.scene.bfg.nudge_amount
						if self.dir == 'UP':
							luv.uv.y = luv.uv.y - context.scene.bfg.nudge_amount
						if self.dir == 'DOWN':
							luv.uv.y = luv.uv.y + context.scene.bfg.nudge_amount
						if self.dir == 'HORIZONTAL':
							luv.uv.x = luv.uv.x * -1
						if self.dir == 'VERTICAL':
							luv.uv.y = luv.uv.y * -1
		# update the mesh
		bmesh.update_edit_mesh(me)
		return {'FINISHED'}
		
################################################################################
## EXPORT
################################################################################
				
class ExportMap(bpy.types.Operator, ExportHelper):
	bl_idname = "export_scene.rbdoom_map_json"
	bl_label = "Export RBDOOM-3-BFG JSON map"
	bl_options = {'PRESET'}
	filename_ext = ".json"
	indent = bpy.props.BoolProperty(name="Indent", default=False)
	
	def create_primitive(self, context, obj, index):
		# need a temp mesh to store the result of to_mesh and a temp object for mesh operator
		temp_mesh = obj.to_mesh(context.scene, True, 'PREVIEW')
		temp_mesh.name = "_export_mesh"
		temp_mesh.transform(obj.matrix_world)
		temp_obj = bpy.data.objects.new("_export_obj", temp_mesh)
		context.scene.objects.link(temp_obj)
		temp_obj.select = True
		context.scene.objects.active = temp_obj
		bpy.ops.object.editmode_toggle()
		bpy.ops.mesh.select_all(action='SELECT')
		#bpy.ops.mesh.vert_connect_concave() # make faces convex
		bpy.ops.mesh.quads_convert_to_tris() # triangulate
		bpy.ops.object.editmode_toggle()
		obj = temp_obj
		mesh = temp_mesh
	
		# vertex position and normal are decoupled from uvs
		# need to:
		# -create new vertices for each vertex/uv combination
		# -map the old vertex indices to the new ones
		vert_map = list(range(len(mesh.vertices)))
		for i in range(0, len(vert_map)):
			vert_map[i] = list()
		for p in mesh.polygons:
			for i in p.loop_indices:
				loop = mesh.loops[i]
				vert_map[loop.vertex_index].append([0, loop.index])
		num_vertices = 0
		for i, v in enumerate(mesh.vertices):
			for vm in vert_map[i]:
				vm[0] = num_vertices
				num_vertices += 1
				
		prim = OrderedDict()
		prim["primitive"] = index
		
		# vertices	
		verts = prim["verts"] = []		
		for i, v in enumerate(mesh.vertices):
			for vm in vert_map[i]:
				uv = mesh.uv_layers[0].data[vm[1]].uv
				vert = OrderedDict()
				vert["xyz"] = (v.co.x * _scale_to_game, v.co.y * _scale_to_game, v.co.z * _scale_to_game)
				vert["st"] = (uv.x, uv.y)
				vert["normal"] = (v.normal.x, v.normal.y, v.normal.z)
				verts.append(vert)
		
		# polygons
		polygons = prim["polygons"] = []
		for p in mesh.polygons:
			poly = OrderedDict()
			poly["material"] = obj.material_slots[p.material_index].name
			indices = poly["indices"] = []
			for i in p.loop_indices:
				loop = mesh.loops[i]
				v = mesh.vertices[loop.vertex_index]
				uv = mesh.uv_layers[0].data[loop.index].uv
				# find the vert_map nested list element with the matching loop.index
				vm = next(x for x in vert_map[loop.vertex_index] if x[1] == loop.index)
				indices.append(vm[0])
			polygons.append(poly)

		# finished, delete the temp object and mesh
		bpy.ops.object.delete()
		bpy.data.meshes.remove(mesh)
		return prim
		
	def execute(self, context):
		if not "worldspawn" in bpy.data.groups:
			self.report({'ERROR'}, "No worldspawn group found. Either build the map or create a group named \"worldspawn\" and link an object to it.")
		else:
			set_object_mode_and_clear_selection()
			data = OrderedDict()
			data["version"] = 3
			entities = data["entities"] = []
			entity_index = 0
			
			# write worldspawn
			worldspawn = OrderedDict()
			worldspawn["entity"] = entity_index
			worldspawn["classname"] = "worldspawn"
			primitives = worldspawn["primitives"] = []
			for i, obj in enumerate(bpy.data.groups["worldspawn"].objects):
				primitives.append(self.create_primitive(context, obj, i))
			entities.append(worldspawn)
			entity_index += 1
			
			# write the rest of the entities
			for obj in context.scene.objects:
				if obj.bfg.type == 'ENTITY' or obj.bfg.type == 'STATIC_MODEL' or obj.type == 'LAMP':
					ent = OrderedDict()
					ent["entity"] = entity_index
					ent["classname"] = "light" if obj.type == 'LAMP' else obj.bfg.classname
					ent["name"] = obj.name
					ent["origin"] = "%s %s %s" % (ftos(obj.location[0] * _scale_to_game), ftos(obj.location[1] * _scale_to_game), ftos(obj.location[2] * _scale_to_game))
					if obj.bfg.type == 'ENTITY':
						if obj.rotation_euler.z != 0.0:
							ent["angle"] = ftos(math.degrees(obj.rotation_euler.z))
					elif obj.bfg.type == 'STATIC_MODEL':
						ent["model"] = obj.bfg.entity_model.replace("\\", "/")
						angles = obj.rotation_euler
						rot = Euler((-angles[0], -angles[1], -angles[2]), 'XYZ').to_matrix()
						ent["rotation"] = "%s %s %s %s %s %s %s %s %s" % (
							ftos(rot[0][0]), ftos(rot[0][1]), ftos(rot[0][2]),
							ftos(rot[1][0]), ftos(rot[1][1]), ftos(rot[1][2]),
							ftos(rot[2][0]), ftos(rot[2][1]), ftos(rot[2][2])
						)
					elif obj.type == 'LAMP':
						ent["light_center"] = "0 0 0"
						radius = ftos(obj.data.distance * _scale_to_game)
						ent["light_radius"] = "%s %s %s" % (radius, radius, radius)
						ent["_color"] = "%s %s %s" % (ftos(obj.data.color[0]), ftos(obj.data.color[1]), ftos(obj.data.color[2]))
						ent["nospecular"] = "%d" % 0 if obj.data.use_specular else 1
						ent["nodiffuse"] = "%d" % 0 if obj.data.use_diffuse else 1
						if obj.bfg.light_material != "default":
							ent["texture"] = obj.bfg.light_material
					entities.append(ent)
					entity_index += 1
			with open(self.filepath, 'w') as f:
				json.dump(data, f, indent="\t" if self.indent else None)
		return {'FINISHED'}
	
def menu_func_export(self, context):
	self.layout.operator(ExportMap.bl_idname, "RBDOOM-3-BFG map (.json)")
		
################################################################################
## GUI PANELS
################################################################################
		
class SettingsPanel(bpy.types.Panel):
	bl_label = "Settings"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'TOOLS'
	bl_category = "BFGForge"

	def draw(self, context):
		scene = context.scene
		col = self.layout.column(align=True)
		col.prop(scene.bfg, "game_path", "Path")
		col.prop(scene.bfg, "mod_dir")
		col.operator(ImportMaterials.bl_idname, ImportMaterials.bl_label, icon='MATERIAL')
		col.operator(ImportEntities.bl_idname, ImportEntities.bl_label, icon='POSE_HLT')
		col = self.layout.column_flow(2)
		col.prop(scene.bfg, "wireframe_rooms")
		col.prop(scene.bfg, "backface_culling")
		col.prop(scene.bfg, "show_entity_names")
		col.prop(scene.bfg, "hide_bad_materials")
		col.prop(scene.bfg, "shadeless_materials")
		
class CreatePanel(bpy.types.Panel):
	bl_label = "Create"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'TOOLS'
	bl_category = "BFGForge"
	
	def draw(self, context):
		scene = context.scene
		col = self.layout.column(align=True)
		row = col.row(align=True)
		row.operator(BuildMap.bl_idname, "Build Map", icon='MOD_BUILD').bool_op = 'UNION'
		row.prop(context.scene.bfg, "map_layer")
		col.operator(AddRoom.bl_idname, "Add 2D Room", icon='SURFACE_NCURVE')
		col.operator(AddBrush.bl_idname, "Add 3D Room", icon='SNAP_FACE').s_type = '3D_ROOM'
		col.operator(AddBrush.bl_idname, "Add Brush", icon='SNAP_VOLUME').s_type = 'BRUSH'
		col = self.layout.column()
		if len(scene.bfg.entities) > 0:
			row = col.row(align=True)
			row.operator(AddEntity.bl_idname, AddEntity.bl_label, icon='POSE_HLT')
			row.prop_search(scene.bfg, "active_entity", scene.bfg, "entities", "", icon='SCRIPT')
		col.operator(AddLight.bl_idname, AddLight.bl_label, icon='LAMP_POINT')
		col.operator(AddStaticModel.bl_idname, AddStaticModel.bl_label, icon='MESH_MONKEY')
		
class MaterialPanel(bpy.types.Panel):
	bl_label = "Material"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'TOOLS'
	bl_category = "BFGForge"
	
	def draw(self, context):
		scene = context.scene
		if len(scene.bfg.material_decls) > 0:
			col = self.layout.column()
			col.prop_search(scene.bfg, "active_material_decl_path", scene.bfg, "material_decl_paths", "", icon='MATERIAL')
			col.template_icon_view(scene.bfg, "active_material_decl")
			col.prop(scene.bfg, "active_material_decl", "")
			if context.active_object and len(context.selected_objects) > 0 and hasattr(context.active_object.data, "materials"):
				if context.active_object.bfg.type == '2D_ROOM':
					col.label("Assign:", icon='MATERIAL')
					row = col.row(align=True)
					row.operator(AssignMaterial.bl_idname, "Ceiling").where = 'CEILING'
					row.operator(AssignMaterial.bl_idname, "Wall").where = 'WALL'
					row.operator(AssignMaterial.bl_idname, "Floor").where = 'FLOOR'
					row.operator(AssignMaterial.bl_idname, "All").where = 'ALL'
				else:
					col.operator(AssignMaterial.bl_idname, AssignMaterial.bl_label, icon='MATERIAL')

class ObjectPanel(bpy.types.Panel):
	bl_label = "Object"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'TOOLS'
	bl_category = "BFGForge"

	def draw(self, context):
		obj = context.active_object
		if obj and len(context.selected_objects) > 0:
			col = self.layout.column(align=True)
			obj_icon = 'OBJECT_DATAMODE'
			if obj.type == 'LAMP':
				obj_icon = 'LAMP_POINT'
			col.label(obj.name, icon=obj_icon)
			if obj.bfg.type != 'NONE':
				col.label("Type: " + obj.bfg.bl_rna.properties['type'].enum_items[obj.bfg.type].name)
			if obj.bfg.type == '2D_ROOM' and obj.modifiers:
				mod = obj.modifiers[0]
				if mod.type == 'SOLIDIFY':
					col.separator()
					col.prop(obj.bfg, "room_height")
					col.operator(CopyRoom.bl_idname, "Copy Room Height", icon='PASTEFLIPUP').copy_op = 'HEIGHT'
					col.separator()
					sub = col.column()
					sub.enabled = False
					sub.prop(obj.bfg, "ceiling_material", "Ceiling")
					sub.prop(obj.bfg, "wall_material", "Wall")
					sub.prop(obj.bfg, "floor_material", "Floor")
					col.separator()
					col.label("Copy Materials:", icon='PASTEFLIPUP')
					row = col.row(align=True)
					row.operator(CopyRoom.bl_idname, "Ceiling").copy_op = 'MATERIAL_CEILING'
					row.operator(CopyRoom.bl_idname, "Wall").copy_op = 'MATERIAL_WALL'
					row.operator(CopyRoom.bl_idname, "Floor").copy_op = 'MATERIAL_FLOOR'
					row.operator(CopyRoom.bl_idname, "All").copy_op = 'MATERIAL_ALL'
			elif obj.type == 'LAMP':
				col.separator()
				sub = col.row()
				sub.prop(obj, "bfg_light_radius")
				sub.prop(obj.data, "color", "")
				col.prop(obj.data, "use_specular")
				col.prop(obj.data, "use_diffuse")
				col.template_icon_view(obj.bfg, "light_material")
				col.separator()
				col.prop(obj.bfg, "light_material", "")
			elif obj.type == 'MESH':
				col.separator()
				col.operator(RefreshMaterials.bl_idname, RefreshMaterials.bl_label, icon='MATERIAL')

class UvPanel(bpy.types.Panel):
	bl_label = "UV"
	bl_space_type = 'VIEW_3D'
	bl_region_type = 'TOOLS'
	bl_category = "BFGForge"

	def draw(self, context):
		layout = self.layout
		col = layout.column(align=True)
		col.label("Texel Density", icon='LATTICE_DATA')
		col.prop(context.scene.bfg, "texel_density", "")
		if context.mode == 'EDIT_MESH' or context.mode == 'OBJECT':
			col = layout.column(align=True)
			col.label("Mapping", icon='FACESEL_HLT')
			row = layout.row(align=True)
			row.operator(AutoUnwrap.bl_idname, "Auto").axis = 'AUTO'
			row = layout.row(align=True)
			row.operator(AutoUnwrap.bl_idname, "X").axis = 'X'
			row.operator(AutoUnwrap.bl_idname, "Y").axis = 'Y'
			row.operator(AutoUnwrap.bl_idname, "Z").axis = 'Z'
			row = layout.row(align=True)
			row.operator(AutoUnwrap.bl_idname, "-X").axis = '-X'
			row.operator(AutoUnwrap.bl_idname, "-Y").axis = '-Y'
			row.operator(AutoUnwrap.bl_idname, "-Z").axis = '-Z'
			if context.mode == 'EDIT_MESH':
				row = layout.row(align=True)
				row.operator(PinUV.bl_idname, "Pin UVs").p = False
				row.operator(PinUV.bl_idname, "Un-Pin UVs").p = True
				col = layout.column(align=True)
				col.label("Offset", icon='FULLSCREEN_ENTER')
				row = layout.row(align=True)
				row.prop(context.scene.bfg, "offset_x", 'X')
				row.prop(context.scene.bfg, "offset_y", 'Y')
				col = layout.column(align=True)
				col.label("Nudge UVs", icon='FORWARD')
				row = layout.row(align=True)
				row.operator(NudgeUV.bl_idname, "Left").dir = 'LEFT'
				row.operator(NudgeUV.bl_idname, "Right").dir = 'RIGHT'
				row = layout.row(align=True)
				row.operator(NudgeUV.bl_idname, "Up").dir = 'UP'
				row.operator(NudgeUV.bl_idname, "Down").dir = 'DOWN'
				row = layout.row(align=True)
				row.prop(context.scene.bfg, "nudge_amount", "Amount")
				col = layout.column(align=True)
				col.label("Flip", icon='LOOP_BACK')
				row = layout.row(align=True)
				row.operator(NudgeUV.bl_idname, "Horizontal").dir = 'HORIZONTAL'
				row.operator(NudgeUV.bl_idname, "Vertical").dir = 'VERTICAL'
				
################################################################################
## PROPERTIES
################################################################################

def update_wireframe_rooms(self, context):
	for obj in context.scene.objects:
		if obj.bfg.type in ['2D_ROOM', '3D_ROOM', 'BRUSH']:
			obj.draw_type = 'WIRE' if context.scene.bfg.wireframe_rooms else 'TEXTURED'
			
def get_backface_culling(self):
	return bpy.context.space_data.show_backface_culling
	
def set_backface_culling(self, value):
	bpy.context.space_data.show_backface_culling = value
			
def update_show_entity_names(self, context):
	for obj in context.scene.objects:
		if obj.bfg.type == 'ENTITY':
			obj.show_name = context.scene.bfg.show_entity_names
			
def update_hide_bad_materials(self, context):
	preview_collections["material"].force_refresh = True
	
def update_shadeless_materials(self, context):
	for mat in bpy.data.materials:
		if mat.name != "_object_color":
			mat.use_shadeless = context.scene.bfg.shadeless_materials
	
class BfgScenePropertyGroup(bpy.types.PropertyGroup):
	game_path = bpy.props.StringProperty(name="RBDOOM-3-BFG Path", description="RBDOOM-3-BFG Path", subtype='DIR_PATH')
	mod_dir = bpy.props.StringProperty(name="Mod Directory")
	wireframe_rooms = bpy.props.BoolProperty(name="Wireframe rooms", default=True, update=update_wireframe_rooms)
	backface_culling = bpy.props.BoolProperty(name="Backface Culling", get=get_backface_culling, set=set_backface_culling)
	show_entity_names = bpy.props.BoolProperty(name="Show entity names", default=False, update=update_show_entity_names)
	hide_bad_materials = bpy.props.BoolProperty(name="Hide bad materials", description="Hide materials with missing diffuse textures", default=True, update=update_hide_bad_materials)
	shadeless_materials = bpy.props.BoolProperty(name="Fullbright materials", description="Disable lighting on materials", default=True, update=update_shadeless_materials)
	map_layer = bpy.props.IntProperty(name="Layer", default=0, min=0, max=19)
	material_decl_paths = bpy.props.CollectionProperty(type=MaterialDeclPathPropGroup)
	active_material_decl_path = bpy.props.StringProperty(name="", default="")
	material_decls = bpy.props.CollectionProperty(type=MaterialDeclPropGroup)
	active_material_decl = bpy.props.EnumProperty(name="", items=material_decl_preview_items)
	entities = bpy.props.CollectionProperty(type=EntityPropGroup)
	active_entity = bpy.props.StringProperty(name="Active Entity", default="")
	texel_density = bpy.props.IntProperty(name="Texel Density", default=128, step=128, min=8, max=512)
	offset_x = bpy.props.FloatProperty(name="Offset X", default=0)
	offset_y = bpy.props.FloatProperty(name="Offset Y", default=0)
	nudge_amount = bpy.props.FloatProperty(name="Nudge Amount", default=0.125)
	
class BfgObjectPropertyGroup(bpy.types.PropertyGroup):
	classname = bpy.props.StringProperty(name="Classname", default="")
	entity_model = bpy.props.StringProperty(name="Entity model", default="")
	room_height = bpy.props.FloatProperty(name="Room Height", default=4, step=20, precision=1, update=update_room)
	floor_material = bpy.props.StringProperty(name="Floor Material", update=update_room)
	wall_material = bpy.props.StringProperty(name="Wall Material", update=update_room)
	ceiling_material = bpy.props.StringProperty(name="Ceiling Material", update=update_room)
	light_material = bpy.props.EnumProperty(name="", items=light_material_preview_items)
	type = bpy.props.EnumProperty(items=[
		('NONE', "None", ""),
		('2D_ROOM', "2D Room", ""),
		('3D_ROOM', "3D Room", ""),
		('BRUSH', "Brush", ""),
		('ENTITY', "Entity", ""),
		('STATIC_MODEL', "Static Model", "")
	], name="BFG Forge Object Type", default='NONE')
	
################################################################################
## MAIN
################################################################################
	
def register():
	bpy.utils.register_module(__name__)
	bpy.types.INFO_MT_file_export.append(menu_func_export)
	bpy.types.Scene.bfg = bpy.props.PointerProperty(type=BfgScenePropertyGroup)
	bpy.types.Object.bfg = bpy.props.PointerProperty(type=BfgObjectPropertyGroup)
	# not in BfgObjectPropertyGroup because get/set self object would be BfgObjectPropertyGroup, not bpy.types.Object
	bpy.types.Object.bfg_light_radius = bpy.props.FloatProperty(name="Radius", get=get_light_radius, set=set_light_radius)
	pcoll = bpy.utils.previews.new()
	pcoll.materials = ()
	pcoll.current_decl_path = ""
	pcoll.force_refresh = False
	preview_collections["material"] = pcoll
	pcoll = bpy.utils.previews.new()
	pcoll.lights = ()
	pcoll.needs_refresh = True
	preview_collections["light"] = pcoll

def unregister():
	bpy.utils.unregister_module(__name__)
	bpy.types.INFO_MT_file_export.remove(menu_func_export)
	del bpy.types.Scene.bfg
	del bpy.types.Object.bfg
	del bpy.types.Object.bfg_light_radius
	for pcoll in preview_collections.values():
		bpy.utils.previews.remove(pcoll)
	preview_collections.clear()

if __name__ == "__main__":
	register()
	
	'''
	lex = Lexer(r"")
	while True:
		last_pos = lex.pos
		token = lex.parse_token()
		if token == None:
			break
		if lex.pos == last_pos:
			raise Exception("hang detected")
			break
		print(token)
	'''
