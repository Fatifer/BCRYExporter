"""Persistent export groups, independent of Blender's scene collections.

ExportNode/MemberView preserve the small name/objects contract used by the
existing COLLADA writer. Blender collections are read only for legacy import.
"""
import uuid
from contextlib import contextmanager

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from bpy.app.handlers import persistent

TYPE_ITEMS = [
    ('cgf', 'CGF', 'Statische Geometrie'),
    ('cga', 'CGA', 'Animierte Geometrie'),
    ('chr', 'CHR', 'Charakter / Skelett'),
    ('skin', 'SKIN', 'Skinned Render Mesh'),
    ('anm', 'ANM', 'Geometrieanimation'),
    ('i_caf', 'I_CAF', 'Skelettanimation'),
]
TYPES = {item[0] for item in TYPE_ITEMS}
_temporary_members = {}
_selection_stack = []


RANGE_ITEMS = [
    ('Timeline', 'Timeline', 'Start und Ende aus der Szenen-Timeline verwenden'),
    ('Values', 'Frames', 'Eigenen Start- und Endframe für diese Animation verwenden'),
    ('Markers', 'Marker', 'Start und Ende durch Timeline-Marker festlegen'),
]


def _range_changed(group, context):
    group.animation_range_initialized = True


class BCryObjectReference(PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)


class BCryExportGroup(PropertyGroup):
    uid: StringProperty(options={'HIDDEN'})
    base_name: StringProperty(name='Dateiname', default='Export')
    export_type: EnumProperty(name='Exporttyp', items=TYPE_ITEMS, default='cgf')
    members: CollectionProperty(type=BCryObjectReference)
    active_member_index: IntProperty(default=0, min=0)
    legacy_source: PointerProperty(type=bpy.types.Collection)
    animation_range_initialized: BoolProperty(default=False, options={'HIDDEN'})
    range_type: EnumProperty(name='Animationsbereich', items=RANGE_ITEMS,
                             default='Timeline', update=_range_changed)
    range_start: IntProperty(name='Startframe', default=1, update=_range_changed)
    range_end: IntProperty(name='Endframe', default=250, update=_range_changed)
    start_marker: StringProperty(name='Startmarker', update=_range_changed)
    end_marker: StringProperty(name='Endmarker', update=_range_changed)


class BCryExportSettings(PropertyGroup):
    initialized: BoolProperty(default=False)
    schema_version: IntProperty(default=2)
    groups: CollectionProperty(type=BCryExportGroup)
    active_group_index: IntProperty(default=0, min=0)


def legacy_nodes(scene):
    """Include unlinked export collections when they contain scene objects."""
    scene_objects = set(scene.objects)
    result = []
    for collection in bpy.data.collections:
        if collection.name.rpartition('.')[2] not in TYPES:
            continue
        if any(obj in scene_objects for obj in collection.objects):
            result.append(collection)
    return result


class MemberView:
    def __init__(self, node):
        self.node = node

    def _objects(self):
        objects = []
        for ref in self.node.group.members:
            if ref.object is not None and ref.object not in objects:
                objects.append(ref.object)
        for obj in _temporary_members.get(self.node.key, ()):
            try:
                if bpy.data.objects.get(obj.name) == obj and obj not in objects:
                    objects.append(obj)
            except ReferenceError:
                continue
        return objects

    def __iter__(self):
        return iter(self._objects())

    def __len__(self):
        return len(self._objects())

    def __getitem__(self, index):
        if isinstance(index, str):
            return next(obj for obj in self if obj.name == index)
        return self._objects()[index]

    def __contains__(self, obj):
        if isinstance(obj, str):
            return any(member.name == obj for member in self)
        return obj in self._objects()

    def link(self, obj):
        if obj in self:
            return
        if obj.get('fakebone') is not None:
            _temporary_members.setdefault(self.node.key, []).append(obj)
        else:
            self.node.group.members.add().object = obj

    def unlink(self, obj):
        for index in reversed(range(len(self.node.group.members))):
            if self.node.group.members[index].object == obj:
                self.node.group.members.remove(index)
        if self.node.key in _temporary_members:
            _temporary_members[self.node.key] = [
                item for item in _temporary_members[self.node.key] if item != obj]


class ExportNode:
    def __init__(self, scene, group):
        self.scene = scene
        self._uid = group.uid

    @property
    def group(self):
        # CollectionProperty growth can invalidate old RNA item wrappers.
        # Resolve the stable ID instead of retaining a pointer to an array item.
        for group in self.scene.bcry_export.groups:
            if group.uid == self._uid:
                return group
        raise ReferenceError('Exportgruppe wurde entfernt: ' + self._uid)

    @property
    def key(self):
        return self.scene.as_pointer(), self._uid

    @property
    def name(self):
        return '{}.{}'.format(self.group.base_name, self.group.export_type)

    @name.setter
    def name(self, value):
        base, separator, kind = value.rpartition('.')
        if not separator or kind not in TYPES:
            raise ValueError('Invalid export node name: ' + value)
        self.group.base_name = base
        self.group.export_type = kind

    @property
    def objects(self):
        return MemberView(self)

    def __eq__(self, other):
        return isinstance(other, ExportNode) and self.key == other.key

    def __hash__(self):
        return hash(self.key)


def get_nodes(scene=None, include_empty=False):
    scene = scene or bpy.context.scene
    settings = getattr(scene, 'bcry_export', None)
    if settings is None or not settings.initialized:
        nodes = legacy_nodes(scene)
    else:
        nodes = [ExportNode(scene, group) for group in settings.groups]
    return [node for node in nodes if include_empty or len(node.objects)]


def object_nodes(obj, scene=None):
    return [node for node in get_nodes(scene) if obj in node.objects[:]]


def active_node(scene):
    settings = scene.bcry_export
    index = settings.active_group_index
    if 0 <= index < len(settings.groups):
        return ExportNode(scene, settings.groups[index])
    return None


def configure_animation_range(node, range_type='Timeline', start=None, end=None,
                              start_marker='', end_marker=''):
    """Store per-group settings; do not change markers or the scene timeline."""
    group = node.group
    group.range_start = node.scene.frame_start if start is None else start
    group.range_end = node.scene.frame_end if end is None else end
    group.start_marker = start_marker or group.base_name + '_Start'
    group.end_marker = end_marker or group.base_name + '_End'
    group.range_type = range_type
    group.animation_range_initialized = True


def adopt_legacy_animation_range(node):
    """Read the same object/armature that the legacy animation exporter uses."""
    group = node.group
    objects = list(node.objects)
    if group.export_type == 'i_caf':
        armatures = [obj for obj in objects if obj.type == 'ARMATURE']
        target = armatures[0] if len(armatures) == 1 else None
    else:
        target = objects[0] if objects else None
    start = target.get(group.base_name + '_Start') if target is not None else None
    end = target.get(group.base_name + '_End') if target is not None else None
    if isinstance(start, str) and isinstance(end, str):
        configure_animation_range(node, 'Markers', start_marker=start, end_marker=end)
    elif isinstance(start, (int, float)) and isinstance(end, (int, float)):
        configure_animation_range(node, 'Values', start=int(start), end=int(end))
    else:
        configure_animation_range(node)


def resolve_animation_range(node, initial_start, initial_end):
    """Resolve UI settings at export time; moved markers stay live references."""
    from . import exceptions
    group = node.group
    if not group.animation_range_initialized:
        # Old internal-list files can also be read before the load handler runs.
        adopt_legacy_animation_range(node)
    if group.range_type == 'Timeline':
        return initial_start, initial_end
    if group.range_type == 'Values':
        start, end = group.range_start, group.range_end
    else:
        markers = node.scene.timeline_markers
        first = markers.get(group.start_marker)
        last = markers.get(group.end_marker)
        if first is None or last is None:
            raise exceptions.BCryException(
                'Animationsmarker fehlt in {}: {} / {}'.format(
                    node.name, group.start_marker, group.end_marker))
        start, end = first.frame, last.frame
    if end < start:
        raise exceptions.BCryException('Endframe liegt vor dem Startframe: ' + node.name)
    return start, end


@persistent
def upgrade_animation_ranges(_=None):
    """Upgrade previous list versions once, outside Panel.draw()."""
    for scene in bpy.data.scenes:
        settings = getattr(scene, 'bcry_export', None)
        if settings is not None and settings.initialized:
            for group in settings.groups:
                if not group.animation_range_initialized:
                    adopt_legacy_animation_range(ExportNode(scene, group))
            settings.schema_version = 2


def import_legacy(scene):
    settings = scene.bcry_export
    imported = 0
    sources = legacy_nodes(scene)
    for source in sources:
        if any(group.legacy_source == source for group in settings.groups):
            continue
        # An existing internal name is never overwritten on a repeated import.
        if any(ExportNode(scene, group).name == source.name for group in settings.groups):
            continue
        group = settings.groups.add()
        group.uid = uuid.uuid4().hex
        group.base_name, _, group.export_type = source.name.rpartition('.')
        group.legacy_source = source
        for obj in source.objects:
            group.members.add().object = obj
        adopt_legacy_animation_range(ExportNode(scene, group))
        imported += 1
    settings.initialized = True
    settings.schema_version = 2
    return imported


def initialize(scene):
    if not scene.bcry_export.initialized:
        import_legacy(scene)


def create_node(scene, base_name, kind, reuse=False):
    initialize(scene)
    from .utils import replace_invalid_rc_characters
    base_name = replace_invalid_rc_characters(base_name).strip('.') or 'Export'
    settings = scene.bcry_export
    if reuse:
        for index, group in enumerate(settings.groups):
            if group.base_name == base_name and group.export_type == kind:
                settings.active_group_index = index
                return ExportNode(scene, group)
    existing = {group.base_name for group in settings.groups}
    original = base_name
    index = 2
    while base_name in existing:
        base_name = '{}_{}'.format(original, index)
        index += 1
    group = settings.groups.add()
    group.uid = uuid.uuid4().hex
    group.base_name = base_name
    group.export_type = kind
    configure_animation_range(ExportNode(scene, group))
    settings.active_group_index = len(settings.groups) - 1
    return ExportNode(scene, group)


def copy_membership(source, target, scene=None):
    scene = scene or bpy.context.scene
    initialize(scene)
    for node in object_nodes(source, scene):
        node.objects.link(target)


def selected_nodes(scene=None):
    scene = scene or bpy.context.scene
    selection = _selection_stack[-1] if _selection_stack else tuple(bpy.context.selected_objects)
    # Preserve legacy ordering: selection order, then membership order.
    result = []
    for obj in selection:
        for node in object_nodes(obj, scene):
            if node not in result:
                result.append(node)
    return result


@contextmanager
def preserve_export_selection():
    _selection_stack.append(tuple(bpy.context.selected_objects))
    try:
        yield
    finally:
        _selection_stack.pop()


def clear_temporary_members():
    _temporary_members.clear()


def validate_names(animation=False):
    """Reject names which would generate duplicate/empty COLLADA export IDs."""
    from . import exceptions
    from .utils import replace_invalid_rc_characters
    kinds = {'anm', 'i_caf'} if animation else {'cgf', 'cga', 'chr', 'skin'}
    seen = set()
    for node in get_nodes():
        base, _, kind = node.name.rpartition('.')
        if kind not in kinds:
            continue
        name = replace_invalid_rc_characters(base)
        if not name or name in seen:
            raise exceptions.BCryException(
                'Exportgruppen brauchen eindeutige, nicht leere Dateinamen: ' + node.name)
        seen.add(name)


class BCRY_OT_import_export_groups(Operator):
    bl_idname = 'bcry.import_export_groups'
    bl_label = 'Bestehende Exportgruppen übernehmen'
    bl_description = 'Export-Collections kopieren; vorhandene Collections bleiben unverändert'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        count = import_legacy(context.scene)
        self.report({'INFO'}, '{} Exportgruppen übernommen'.format(count))
        return {'FINISHED'}


class BCRY_OT_add_export_group(Operator):
    bl_idname = 'bcry.add_export_group'
    bl_label = 'Exportgruppe anlegen'
    bl_options = {'REGISTER', 'UNDO'}

    base_name: StringProperty(name='Dateiname', default='Export')
    export_type: EnumProperty(name='Exporttyp', items=TYPE_ITEMS, default='cgf')
    add_selected: BoolProperty(name='Ausgewählte Objekte hinzufügen', default=True)

    def invoke(self, context, event):
        self.base_name = context.active_object.name if context.active_object else 'Export'
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        node = create_node(context.scene, self.base_name, self.export_type)
        if self.add_selected:
            for obj in context.selected_objects:
                node.objects.link(obj)
        return {'FINISHED'}


class BCRY_OT_remove_export_group(Operator):
    bl_idname = 'bcry.remove_export_group'
    bl_label = 'Exportgruppe entfernen'
    bl_description = 'Nur die Exportgruppe entfernen; Blender-Objekte bleiben erhalten'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene and active_node(context.scene) is not None

    def execute(self, context):
        settings = context.scene.bcry_export
        _temporary_members.pop(active_node(context.scene).key, None)
        settings.groups.remove(settings.active_group_index)
        settings.active_group_index = max(0, min(settings.active_group_index, len(settings.groups) - 1))
        return {'FINISHED'}


class BCRY_OT_export_group_members(Operator):
    bl_idname = 'bcry.export_group_members'
    bl_label = 'Exportgruppen-Mitglieder bearbeiten'
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=[('ADD', 'Hinzufügen', ''), ('REMOVE', 'Entfernen', ''),
                                ('ONE', 'Eintrag entfernen', ''), ('SELECT', 'Auswählen', '')])

    @classmethod
    def poll(cls, context):
        return context.scene and active_node(context.scene) is not None and context.mode == 'OBJECT'

    def execute(self, context):
        node = active_node(context.scene)
        group = node.group
        if self.action == 'ADD':
            for obj in context.selected_objects:
                node.objects.link(obj)
        elif self.action == 'REMOVE':
            for obj in tuple(context.selected_objects):
                node.objects.unlink(obj)
        elif self.action == 'ONE':
            if 0 <= group.active_member_index < len(group.members):
                group.members.remove(group.active_member_index)
        else:
            members = list(node.objects)
            for obj in context.view_layer.objects:
                if obj.visible_get() and not obj.hide_select:
                    obj.select_set(obj in members)
            available = [obj for obj in members if obj.name in context.view_layer.objects
                         and obj.select_get()]
            if available:
                context.view_layer.objects.active = available[0]
        group.active_member_index = max(0, min(group.active_member_index, len(group.members) - 1))
        return {'FINISHED'}


class BCRY_OT_create_animation_markers(Operator):
    bl_idname = 'bcry.create_animation_markers'
    bl_label = 'Fehlende Marker anlegen'
    bl_description = 'Marker an den eingestellten Frames anlegen; vorhandene Marker bleiben unverändert'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        node = active_node(context.scene) if context.scene else None
        return node is not None and node.group.export_type in {'anm', 'i_caf'}

    def execute(self, context):
        group = active_node(context.scene).group
        if not group.start_marker.strip() or not group.end_marker.strip():
            self.report({'ERROR'}, 'Bitte Start- und Endmarker benennen.')
            return {'CANCELLED'}
        if group.range_end < group.range_start:
            self.report({'ERROR'}, 'Endframe liegt vor dem Startframe.')
            return {'CANCELLED'}
        markers = context.scene.timeline_markers
        for name, frame in ((group.start_marker, group.range_start),
                            (group.end_marker, group.range_end)):
            if markers.get(name) is None:
                markers.new(name, frame=frame)
        return {'FINISHED'}


def draw_animation_range(layout, context, node):
    group = node.group
    if group.export_type not in {'anm', 'i_caf'}:
        return
    box = layout.box()
    box.label(text='Animationsbereich', icon='PREVIEW_RANGE')
    box.prop(group, 'range_type', expand=True)
    if group.range_type == 'Timeline':
        box.label(text='Szenenbereich: {} – {}'.format(
            context.scene.frame_start, context.scene.frame_end))
    elif group.range_type == 'Values':
        row = box.row(align=True)
        row.prop(group, 'range_start')
        row.prop(group, 'range_end')
        if group.range_end < group.range_start:
            box.label(text='Endframe liegt vor dem Startframe.', icon='ERROR')
    else:
        box.prop_search(group, 'start_marker', context.scene, 'timeline_markers')
        box.prop_search(group, 'end_marker', context.scene, 'timeline_markers')
        first = context.scene.timeline_markers.get(group.start_marker)
        last = context.scene.timeline_markers.get(group.end_marker)
        if first is None or last is None:
            box.label(text='Fehlende Marker können hier angelegt werden.', icon='INFO')
            box.prop(group, 'range_start', text='Startframe neuer Marker')
            box.prop(group, 'range_end', text='Endframe neuer Marker')
            box.operator('bcry.create_animation_markers', icon='ADD')
        else:
            box.label(text='Markerbereich: {} – {}'.format(first.frame, last.frame))
            if last.frame < first.frame:
                box.label(text='Endmarker liegt vor dem Startmarker.', icon='ERROR')


def draw_export_actions(layout):
    """Keep export entry points visible before any list/detail UI is drawn."""
    column = layout.column(align=True)
    column.operator_context = 'INVOKE_DEFAULT'
    column.enabled = True
    column.active = True
    column.emboss = 'NORMAL'
    column.scale_y = 1.35
    column.operator('scene.export_to_game', text='Export to CryEngine', icon='EXPORT', emboss=True)
    column.operator('scene.export_to_game_quick', text='Quick Export', icon='EXPORT', emboss=True)
    column.operator('scene.export_animations', text='Export Animations', icon='RENDER_ANIMATION', emboss=True)


class BCRY_PT_export_actions(Panel):
    bl_label = 'Export'
    bl_idname = 'BCRY_PT_export_actions'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BCry Exporter'
    bl_order = -100
    bl_options = set()

    def draw(self, context):
        from . import VERSION
        self.layout.label(text='BCry Exporter ' + VERSION)
        draw_export_actions(self.layout)


class BCRY_UL_export_groups(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.base_name, icon='OUTLINER_COLLECTION')
        row.label(text='.' + item.export_type)
        row.label(text=str(sum(ref.object is not None for ref in item.members)))


class BCRY_UL_export_members(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if item.object is None:
            layout.label(text='Objekt gelöscht – Eintrag entfernen', icon='ERROR')
        else:
            layout.label(text=item.object.name, icon='OBJECT_DATA')


class BCRY_PT_export_groups(Panel):
    bl_label = 'Exportgruppen / Dateien'
    bl_idname = 'BCRY_PT_export_groups'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BCry Exporter'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.bcry_export
        if not settings.initialized:
            nodes = legacy_nodes(context.scene)
            if nodes:
                layout.label(text='Vorhandene Export-Collections:')
                for node in nodes:
                    layout.label(text='{} ({})'.format(node.name, len(node.objects)))
                layout.operator('bcry.import_export_groups', icon='IMPORT')
            else:
                layout.label(text='Noch keine Exportgruppen vorhanden.')
            layout.operator('bcry.add_export_group', icon='ADD')
            return
        row = layout.row()
        row.template_list('BCRY_UL_export_groups', '', settings, 'groups',
                          settings, 'active_group_index', rows=4)
        buttons = row.column(align=True)
        buttons.operator('bcry.add_export_group', text='', icon='ADD')
        buttons.operator('bcry.remove_export_group', text='', icon='REMOVE')
        node = active_node(context.scene)
        if node is None:
            layout.label(text='Mit + eine Exportgruppe anlegen.')
        else:
            group = node.group
            layout.prop(group, 'base_name')
            layout.prop(group, 'export_type')
            layout.label(text='Datei: ' + node.name)
            draw_animation_range(layout, context, node)
            if not group.base_name.strip() or any(
                    other.uid != group.uid and other.base_name == group.base_name
                    for other in settings.groups):
                layout.label(text='Eindeutigen Dateinamen ohne Endung vergeben.', icon='ERROR')
            row = layout.row()
            row.template_list('BCRY_UL_export_members', '', group, 'members',
                              group, 'active_member_index', rows=5)
            row.column().operator('bcry.export_group_members', text='', icon='X').action = 'ONE'
            row = layout.row(align=True)
            row.operator('bcry.export_group_members', text='Auswahl hinzufügen', icon='ADD').action = 'ADD'
            row.operator('bcry.export_group_members', text='Auswahl entfernen', icon='REMOVE').action = 'REMOVE'
            layout.operator('bcry.export_group_members', text='Mitglieder auswählen', icon='RESTRICT_SELECT_OFF').action = 'SELECT'
            if context.mode != 'OBJECT':
                layout.label(text='Mitglieder im Objektmodus bearbeiten.', icon='INFO')
            if not len(node.objects):
                layout.label(text='Leere Gruppen werden nicht exportiert.', icon='INFO')
        layout.separator()
        layout.operator('bcry.import_export_groups', text='Weitere Export-Collections übernehmen', icon='IMPORT')


CLASSES = (BCryObjectReference, BCryExportGroup, BCryExportSettings,
           BCRY_OT_import_export_groups, BCRY_OT_add_export_group,
           BCRY_OT_remove_export_group, BCRY_OT_export_group_members,
           BCRY_OT_create_animation_markers,
           BCRY_PT_export_actions,
           BCRY_UL_export_groups, BCRY_UL_export_members, BCRY_PT_export_groups)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bcry_export = PointerProperty(type=BCryExportSettings)
    # addon_utils.enable() runs register() under RestrictBlend: scene access
    # is forbidden until registration has returned to Blender's event loop.
    bpy.app.timers.register(upgrade_animation_ranges, first_interval=0.1)
    bpy.app.handlers.load_post.append(upgrade_animation_ranges)


def unregister():
    if bpy.app.timers.is_registered(upgrade_animation_ranges):
        bpy.app.timers.unregister(upgrade_animation_ranges)
    if upgrade_animation_ranges in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(upgrade_animation_ranges)
    clear_temporary_members()
    _selection_stack.clear()
    if hasattr(bpy.types.Scene, 'bcry_export'):
        del bpy.types.Scene.bcry_export
    for cls in reversed(CLASSES):
        if getattr(cls, 'is_registered', False):
            bpy.utils.unregister_class(cls)
