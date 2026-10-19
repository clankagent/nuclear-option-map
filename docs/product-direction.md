# Demo product direction

The current demo is a clean, Satellite-first map browser. Start in Satellite when the selected map has that dataset. Keep Builtin and Topographic available as distinct views.

Preserve the footer controls, map scale, and overview map. These support map navigation and orientation. Keep desktop and phone layouts usable with the same core map controls.

Keep the retired toolbar measurement and pin-placement workflows removed. Temporary measurement uses the footer scale itself: click to enable, drag independent rulers, right-click to remove the latest, and press Escape or click the scale again to clear and exit. Rulers match the footer scale marks and stay anchored to map coordinates while navigating. Nothing is saved or shared; changing maps clears the measurements.

Map rulers use a thinner stroke than the footer scale. Keep the existing length-label font size. Endpoints drawn within 10 screen pixels join a group without snapping; each ruler retains its individual length and the group shows a combined total. Connections survive zoom, and undo recomputes affected groups. Interior crossings do not join rulers.

Places and mission-loading behavior are outside this redesign. Do not change those workflows as part of visual cleanup. Do not infer a new mission workflow, live data feed, or feature backlog from the map browser.

Define future use cases before expanding the UI. New workflows need a clear user task, required data, and acceptance criteria. Keep the reusable browser library independent of demo-specific controls.

Satellite imagery comes from precomputed map assets. Rendering and asset preparation remain offline work. Code builds and synthetic browser checks do not establish imagery quality; real-map visual review remains separate.
