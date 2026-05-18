from __future__ import annotations

import sys
import unittest
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aipywidgets import AIForm, Action, fields


def single_step(*step_fields):
    return [{"id": "main", "label": "Main", "fields": list(step_fields)}]


def save_actions():
    return [Action(id="save", label="Save")]


class CoreTests(unittest.TestCase):
    def test_get_and_set_value(self) -> None:
        form = AIForm(
            steps=single_step(
                fields.Text("title"),
                fields.Object(
                    "license",
                    fields=[
                        fields.Text("name"),
                        fields.Text("url"),
                    ],
                ),
            ),
            actions=save_actions(),
        )

        form.set_value("title", "Example")
        form.set_value("license.name", "CC BY 4.0")

        self.assertEqual(form.get_value("title"), "Example")
        self.assertEqual(form.get_value("license.name"), "CC BY 4.0")

    def test_array_object_paths(self) -> None:
        form = AIForm(
            steps=single_step(
                fields.Array(
                    "authors",
                    item=fields.Object(
                        fields=[
                            fields.Text("given_name"),
                            fields.Text("family_name"),
                        ]
                    ),
                    default=[{"given_name": "", "family_name": ""}],
                )
            ),
            actions=save_actions(),
        )

        form.set_value("authors[0].family_name", "Lovelace")

        self.assertEqual(form.get_value("authors[0].family_name"), "Lovelace")

    def test_nested_array_object_paths(self) -> None:
        form = AIForm(
            steps=single_step(
                fields.Array(
                    "sections",
                    item=fields.Object(
                        fields=[
                            fields.Text("title"),
                            fields.Array(
                                "authors",
                                item=fields.Object(
                                    fields=[
                                        fields.Text("given_name"),
                                        fields.Text("family_name"),
                                    ]
                                ),
                                default=[{"given_name": "", "family_name": ""}],
                            ),
                        ]
                    ),
                    default=[
                        {
                            "title": "Main",
                            "authors": [{"given_name": "", "family_name": ""}],
                        }
                    ],
                )
            ),
            actions=save_actions(),
        )

        form.set_value("sections[0].authors[0].family_name", "Lovelace")

        self.assertEqual(form.get_value("sections[0].authors[0].family_name"), "Lovelace")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_array_rerender_preserves_existing_widget_values(self) -> None:
        form = AIForm(
            steps=single_step(
                fields.Array(
                    "authors",
                    label="Authors",
                    item=fields.Object(
                        fields=[
                            fields.Text("given_name"),
                            fields.Text("family_name"),
                        ]
                    ),
                    default=[{"given_name": "Ada", "family_name": "Lovelace"}],
                )
            ),
            actions=save_actions(),
        )

        root = form.widget()
        array_widget = root.children[2].children[0]
        add_button = array_widget.children[3]

        self.assertEqual(form._widgets["authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["authors[0].family_name"].value, "Lovelace")

        add_button.click()

        self.assertEqual(form.get_value("authors[0].given_name"), "Ada")
        self.assertEqual(form._widgets["authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["authors[0].family_name"].value, "Lovelace")
        self.assertEqual(form._widgets["authors[1].given_name"].value, "")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_nested_array_rerender_preserves_existing_widget_values(self) -> None:
        form = AIForm(
            steps=single_step(
                fields.Array(
                    "sections",
                    item=fields.Object(
                        fields=[
                            fields.Text("title"),
                            fields.Array(
                                "authors",
                                item=fields.Object(
                                    fields=[
                                        fields.Text("given_name"),
                                        fields.Text("family_name"),
                                    ]
                                ),
                                default=[{"given_name": "Ada", "family_name": "Lovelace"}],
                            ),
                        ]
                    ),
                    default=[
                        {
                            "title": "Main",
                            "authors": [{"given_name": "Ada", "family_name": "Lovelace"}],
                        }
                    ],
                )
            ),
            actions=save_actions(),
        )

        root = form.widget()
        sections_widget = root.children[2].children[0]
        add_section_button = sections_widget.children[3]
        first_section_widget = sections_widget.children[2].children[0]
        nested_array_widget = first_section_widget.children[1].children[3]

        self.assertEqual(form._widgets["sections[0].authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["sections[0].authors[0].family_name"].value, "Lovelace")

        add_author_button = nested_array_widget.children[3]
        add_author_button.click()
        add_section_button.click()

        self.assertEqual(form.get_value("sections[0].authors[0].given_name"), "Ada")
        self.assertEqual(form._widgets["sections[0].authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["sections[0].authors[0].family_name"].value, "Lovelace")
        self.assertEqual(form._widgets["sections[0].authors[1].given_name"].value, "")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_next_button_validates_required_fields_before_advancing(self) -> None:
        form = AIForm(
            steps=[
                {
                    "id": "metadata",
                    "label": "Metadata",
                    "fields": [fields.Text("title", required=True)],
                },
                {
                    "id": "review",
                    "label": "Review",
                    "fields": [fields.Checkbox("confirmed")],
                },
            ]
            ,
            actions=save_actions(),
        )

        root = form.widget()
        next_button = root.children[3].children[0]
        next_button.click()

        self.assertEqual(form._current_step_index, 0)
        self.assertIn("Required", form._error_widgets["title"].value)
        self.assertIn("1 error in this step", root.children[1].value)

        form.set_value("title", "Example")
        next_button.click()

        self.assertEqual(form._current_step_index, 1)
        self.assertIn("Step 2 of 2", root.children[0].value)

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_previous_button_returns_to_prior_step(self) -> None:
        form = AIForm(
            steps=[
                {"id": "metadata", "label": "Metadata", "fields": [fields.Text("title")]},
                {"id": "review", "label": "Review", "fields": [fields.Checkbox("confirmed")]},
            ],
            actions=save_actions(),
        )

        root = form.widget()
        root.children[3].children[0].click()
        root.children[3].children[0].click()

        self.assertEqual(form._current_step_index, 0)
        self.assertIn("Step 1 of 2", root.children[0].value)

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_required_checkbox_blocks_next_until_checked(self) -> None:
        form = AIForm(
            steps=[
                {"id": "review", "label": "Review", "fields": [fields.Checkbox("confirmed", required=True)]},
                {"id": "done", "label": "Done", "fields": [fields.Text("summary")]},
            ],
            actions=save_actions(),
        )

        root = form.widget()
        next_button = root.children[3].children[0]
        next_button.click()

        self.assertEqual(form._current_step_index, 0)
        self.assertIn("Required", form._error_widgets["confirmed"].value)

        form.set_value("confirmed", True)
        next_button.click()

        self.assertEqual(form._current_step_index, 1)

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_actions_are_only_visible_on_last_step(self) -> None:
        form = AIForm(
            steps=[
                {"id": "metadata", "label": "Metadata", "fields": [fields.Text("title")]},
                {"id": "review", "label": "Review", "fields": [fields.Checkbox("confirmed")]},
            ],
            actions=save_actions(),
        )

        root = form.widget()

        self.assertEqual(root.children[3].children[0].description, "Next")

        root.children[3].children[0].click()

        self.assertEqual(root.children[3].children[0].description, "Previous")
        self.assertEqual(type(root.children[3].children[1]).__name__, "HBox")
        self.assertEqual(root.children[3].children[1].children[0].description, "Save")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_form_uses_full_width_and_right_aligned_navigation(self) -> None:
        form = AIForm(
            steps=single_step(fields.Text("title", full_width=True)),
            actions=save_actions(),
        )

        root = form.widget()

        self.assertEqual(root.layout.width, "100%")
        self.assertEqual(root.children[2].layout.width, "100%")
        self.assertEqual(root.children[3].layout.width, "100%")
        self.assertEqual(root.children[3].layout.justify_content, "flex-end")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_full_width_field_expands_widget_and_shell(self) -> None:
        form = AIForm(
            steps=single_step(fields.Text("title", full_width=True)),
            actions=save_actions(),
        )

        root = form.widget()
        shell = root.children[2].children[0]

        self.assertEqual(form._widgets["title"].layout.width, "calc(100% - 8px)")
        self.assertEqual(shell.layout.width, "100%")

    def test_hook_updates_values(self) -> None:
        form = AIForm(steps=single_step(fields.Text("title"), fields.Text("slug")), actions=save_actions())

        @form.on_change("title")
        def update_slug(ctx):
            ctx.set_value("slug", ctx.value.lower().replace(" ", "-"))

        form.set_value("title", "Example Paper")

        self.assertEqual(form.get_value("slug"), "example-paper")

    def test_cycle_detection(self) -> None:
        form = AIForm(steps=single_step(fields.Text("title"), fields.Text("slug")), actions=save_actions())

        @form.on_change("title")
        def update_slug(ctx):
            ctx.set_value("slug", "example")

        @form.on_change("slug")
        def update_title(ctx):
            ctx.set_value("title", "Example")

        with self.assertRaisesRegex(RuntimeError, "Cyclic hook update detected"):
            form.set_value("title", "Start")

    def test_action_handler(self) -> None:
        form = AIForm(
            steps=single_step(fields.Text("title")),
            actions=save_actions(),
        )
        calls = []

        @form.on_action("save")
        def save(ctx):
            calls.append(ctx.action.id)

        form._run_action(form.actions[0])

        self.assertEqual(calls, ["save"])

    def test_missing_action_handler_fails_fast(self) -> None:
        form = AIForm(steps=single_step(), actions=save_actions())

        with self.assertRaisesRegex(RuntimeError, "No handler registered"):
            form._run_action(form.actions[0])

    def test_step_without_fields_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required 'fields'"):
            AIForm(steps=[{"id": "broken"}], actions=save_actions())

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_array_without_item_schema_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an item schema"):
            AIForm(steps=single_step(fields.Array("authors")), actions=save_actions())

    def test_duplicate_field_ids_fail_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate field id"):
            AIForm(steps=single_step(fields.Text("title"), fields.Text("title")), actions=save_actions())

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_margin_bottom_style_adds_bottom_spacer(self) -> None:
        form = AIForm(steps=single_step(fields.Text("title")), actions=save_actions(), style={"margin_bottom": "360px"})

        root = form.widget()
        spacer = root.children[-1]

        self.assertIn("aipy-form-margin-bottom", spacer._dom_classes)
        self.assertEqual(spacer.layout.height, "360px")

    def test_unknown_style_key_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported AIForm style key"):
            AIForm(steps=single_step(fields.Text("title")), actions=save_actions(), style={"assist_margin": "360px"})

    def test_invalid_style_value_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            AIForm(steps=single_step(fields.Text("title")), actions=save_actions(), style={"margin_bottom": ""})

    def test_missing_actions_fail_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one action is required"):
            AIForm(steps=single_step(), actions=[])

    def test_unknown_action_handler_fails_fast(self) -> None:
        form = AIForm(steps=single_step(), actions=save_actions())

        with self.assertRaisesRegex(ValueError, "Unknown action id"):

            @form.on_action("deposit")
            def deposit(ctx):
                raise AssertionError("should not register")

    def test_duplicate_action_handler_fails_fast(self) -> None:
        form = AIForm(steps=single_step(), actions=save_actions())

        @form.on_action("save")
        def save(ctx):
            return None

        with self.assertRaisesRegex(ValueError, "already registered"):

            @form.on_action("save")
            def save_again(ctx):
                raise AssertionError("should not register")


if __name__ == "__main__":
    unittest.main()
