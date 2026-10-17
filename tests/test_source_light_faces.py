import dataclasses
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from source_light_faces import Corner, Triangle, plan_camera_facing_faces
from source_light_mesh import material_ids


def pair(material=153, z=0.0):
    points = ((0.0, 0.0, z), (1.0, 0.0, z), (0.0, 1.0, z))
    uv = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    corners = tuple(
        Corner(p, (u, (0.25, 0.5)), (1.0, 0.5, 0.25, 1.0), (0.0, 0.0, 1.0))
        for p, u in zip(points, uv)
    )
    front = Triangle(corners, material)
    back = Triangle(
        tuple(
            dataclasses.replace(c, normal=(0.0, 0.0, -1.0)) for c in reversed(corners)
        ),
        material,
    )
    return front, back


class SourceLightFaceTests(unittest.TestCase):
    def test_material_selection_requires_explicit_reconstruction_metadata(self):
        class Material(dict):
            def __init__(self, name, **metadata):
                super().__init__(metadata)
                self.name = name

        source = Material("copied light", sourceMaterial="resources.assets:153",
                          sourceShader="resources.assets:4022")
        blink = Material("any source name", staticMeshLight=(
            "zero-frequency additive camera effect; secondary rays transparent"))
        named_only = Material("MeshLight153-exact-source-RGBA-UV")
        wrong_shader = Material("other", sourceMaterial="resources.assets:153",
                                sourceShader="other shader")
        self.assertEqual(material_ids([source, blink, named_only, wrong_shader, None]),
                         {"copied light": 153, "any source name": 154})

    def test_retains_front_and_exact_original_data(self):
        front, back = pair()
        result = plan_camera_facing_faces([back, front], target_materials={153})
        self.assertEqual(result.retained, (1,))
        self.assertEqual(result.removed_pairs, ((1, 0),))
        self.assertEqual(front.corners[1].uv_layers, ((1.0, 0.0), (0.25, 0.5)))

    def test_result_does_not_depend_on_input_order(self):
        front, back = pair()
        for inputs in [(front, back), (back, front)]:
            result = plan_camera_facing_faces(inputs, target_materials={153})
            self.assertEqual([inputs[i] for i in result.retained], [front])

    def test_reflection_uses_transformed_ordered_geometry(self):
        front, back = pair()
        reflected = (
            (-1.0, 0.0, 0.0, 0.0),
            (0.0, 2.0, 0.0, 0.0),
            (0.0, 0.0, 3.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
        result = plan_camera_facing_faces(
            [front, back], target_materials={153}, world_matrix=reflected
        )
        self.assertEqual(result.retained, (1,))
        self.assertEqual(result.world_determinant, -6.0)

    def test_thin_offset_surface_is_not_welded(self):
        front, _ = pair()
        _, back = pair(z=1e-12)
        result = plan_camera_facing_faces([front, back], target_materials={153})
        self.assertEqual(result.retained, (0, 1))
        self.assertFalse(result.removed_pairs)

    def test_mixed_materials_remain_unchanged(self):
        target = pair(154)
        other = pair("building")
        inputs = (*other, *target)
        result = plan_camera_facing_faces(inputs, target_materials={153, 154})
        self.assertEqual(result.retained, (0, 1, 2))
        self.assertEqual(tuple(inputs[i] for i in result.retained[:2]), other)

    def test_equal_position_different_uv_or_color_refuses(self):
        front, back = pair()
        for field, value in [
            ("rgba", (0.0, 0.5, 0.25, 1.0)),
            ("uv_layers", ((0.0, 0.0), (0.25, 0.500001))),
        ]:
            changed = dataclasses.replace(back.corners[0], **{field: value})
            altered = dataclasses.replace(back, corners=(changed, *back.corners[1:]))
            with self.assertRaisesRegex(ValueError, "different UV or RGBA"):
                plan_camera_facing_faces([front, altered], target_materials={153})

    def test_inconsistent_custom_normal_refuses(self):
        front, back = pair()
        bad = dataclasses.replace(
            back,
            corners=tuple(
                dataclasses.replace(c, normal=(0.0, 0.0, 1.0)) for c in back.corners
            ),
        )
        with self.assertRaisesRegex(ValueError, "source winding"):
            plan_camera_facing_faces([front, bad], target_materials={153})

    def test_balanced_multiplicity_preserves_every_front(self):
        front, back = pair()
        result = plan_camera_facing_faces(
            [front, back, front, back], target_materials={153}
        )
        self.assertEqual(result.retained, (0, 2))
        with self.assertRaisesRegex(ValueError, "multiplicity"):
            plan_camera_facing_faces([front, back, front], target_materials={153})

    def test_edge_on_below_threshold_and_same_winding_are_unchanged(self):
        front, back = pair()
        result = plan_camera_facing_faces(
            [front, back],
            target_materials={153},
            surface_to_camera=(1.0, 0.0, 0.0),
            min_facing_cosine=0.1,
        )
        self.assertEqual(result.retained, (0, 1))
        result = plan_camera_facing_faces([front, front], target_materials={153})
        self.assertEqual(result.retained, (0, 1))

    def test_singular_transform_and_nonfinite_data_refuse(self):
        front, back = pair()
        with self.assertRaisesRegex(ValueError, "Invertible"):
            plan_camera_facing_faces(
                [front, back],
                target_materials={153},
                world_matrix=(
                    (0.0, 0.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0, 0.0),
                    (0.0, 0.0, 1.0, 0.0),
                    (0.0, 0.0, 0.0, 1.0),
                ),
            )
        changed = dataclasses.replace(
            front.corners[0], position=(float("nan"), 0.0, 0.0)
        )
        with self.assertRaisesRegex(ValueError, "Finite"):
            plan_camera_facing_faces(
                [dataclasses.replace(front, corners=(changed, *front.corners[1:]))],
                target_materials={153},
            )


if __name__ == "__main__":
    unittest.main()
