bl_info = {
    "name": "Meine Objektliste",
    "author": "BlenderGott",
    "version": (1, 0),
    "blender": (3, 5, 0),   # Mindest-Blender‑Version
    "location": "View3D > Sidebar > Meine Addon",
    "description": "Zeigt eine eigene Liste von Objekten im N‑Panel an.",
    "category": "Object",
}

import bpy
from bpy.props import (
    StringProperty,
    CollectionProperty,
    IntProperty,
    PointerProperty,
)
from bpy.types import (
    PropertyGroup,
    Operator,
    Panel,
    UIList,
)

# ------------------------------------------------------------------
# 1. Datenstruktur für ein Listenelement
# ------------------------------------------------------------------
class MyObjectItem(PropertyGroup):
    """Ein einzelnes Element in der Liste."""
    # Der Name des Elements (kann später z.B. das Objekt‑Name sein)
    name: StringProperty(name="Name", default="Neues Objekt")

    # Optional: Pointer zu einem echten Blender‑Objekt
    obj_ref: PointerProperty(
        name="Blender Object",
        type=bpy.types.Object,
        description="Verweis auf ein reales Objekt"
    )

# ------------------------------------------------------------------
# 2. UIList – wie jedes Element gerendert wird
# ------------------------------------------------------------------
class OBJECT_UL_my_list(UIList):
    """UIList zum Anzeigen der MyObjectItem‑Elemente."""

    def draw_item(self, context, layout, data, item,
                  icon, active_data, active_propname, index):

        # 'data' ist die CollectionProperty (hier: Scene.my_object_list)
        # 'item' ist das aktuelle Element (MyObjectItem)

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Wenn ein Blender‑Objekt verlinkt ist, zeige dessen Icon
            if item.obj_ref is not None:
                row.prop(item.obj_ref, "name", text="", icon='OBJECT_DATAMODE')
            else:
                row.label(text=item.name, icon='EMPTY_DATA')
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(icon='OBJECT_DATAMODE')

# ------------------------------------------------------------------
# 3. Operatoren – Hinzufügen / Entfernen
# ------------------------------------------------------------------
class OBJECT_OT_my_list_add(Operator):
    bl_idname = "object.my_list_add"
    bl_label = "Objekt hinzufügen"
    bl_description = "Fügt ein neues Element zur Liste hinzu"

    def execute(self, context):
        scene = context.scene
        new_item = scene.my_object_list.add()
        # Beispiel: Name automatisch setzen
        new_index = len(scene.my_object_list) - 1
        new_item.name = f"Objekt {new_index}"
        # Optional: Das aktuelle ausgewählte Objekt referenzieren
        if context.active_object:
            new_item.obj_ref = context.active_object

        scene.my_object_index = new_index   # Fokus auf neues Element setzen
        return {'FINISHED'}

class OBJECT_OT_my_list_remove(Operator):
    bl_idname = "object.my_list_remove"
    bl_label = "Objekt entfernen"
    bl_description = "Entfernt das ausgewählte Element aus der Liste"

    @classmethod
    def poll(cls, context):
        scene = context.scene
        return len(scene.my_object_list) > 0

    def execute(self, context):
        scene = context.scene
        idx = scene.my_object_index
        if idx >= 0:
            scene.my_object_list.remove(idx)
            # Index anpassen
            if idx > 0:
                scene.my_object_index -= 1
            else:
                scene.my_object_index = 0
        return {'FINISHED'}

# ------------------------------------------------------------------
# 4. Panel – das N‑Panel im 3D Viewport
# ------------------------------------------------------------------
class OBJECT_PT_my_panel(Panel):
    bl_label = "Meine Objektliste"
    bl_idname = "OBJECT_PT_my_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'   # Sidebar (N‑Panel)
    bl_category = "Meine Addon"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        
#        for i in range(3):
        row = layout.row()
        
        # Die eigentliche UIList
        
        row.template_list(
            "OBJECT_UL_my_list",   # Name der UIList
            "",                    # Identifier (kann leer sein)
            scene,
            "my_object_list",
            scene,
            "my_object_index"
        )

        col = row.column(align=True)
        col.operator("object.my_list_add", icon='ADD', text="")
        col.operator("object.my_list_remove", icon='REMOVE', text="")

        # Optional: Detail‑Ansicht des ausgewählten Elements
        if scene.my_object_index >= 0 and len(scene.my_object_list):
            item = scene.my_object_list[scene.my_object_index]


# ------------------------------------------------------------------
# 5. Registrierung
# ------------------------------------------------------------------
classes = (
    MyObjectItem,
    OBJECT_UL_my_list,
    OBJECT_OT_my_list_add,
    OBJECT_OT_my_list_remove,
    OBJECT_PT_my_panel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    # CollectionProperty in der Szene
    bpy.types.Scene.my_object_list = CollectionProperty(
        type=MyObjectItem,
        name="Meine Objekte",
        description="Liste von Objekten im N‑Panel"
    )
    bpy.types.Scene.my_object_index = IntProperty(name="Index", default=0)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.my_object_list
    del bpy.types.Scene.my_object_index

if __name__ == "__main__":
    register()
