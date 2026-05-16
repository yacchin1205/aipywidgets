from __future__ import annotations

import sys
import unittest
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aipywidgets import AIForm, Action, fields


class CoreTests(unittest.TestCase):
    def test_get_and_set_value(self) -> None:
        form = AIForm(
            fields=[
                fields.Text("title"),
                fields.Object(
                    "license",
                    fields=[
                        fields.Text("name"),
                        fields.Text("url"),
                    ],
                ),
            ]
        )

        form.set_value("title", "Example")
        form.set_value("license.name", "CC BY 4.0")

        self.assertEqual(form.get_value("title"), "Example")
        self.assertEqual(form.get_value("license.name"), "CC BY 4.0")

    def test_array_object_paths(self) -> None:
        form = AIForm(
            fields=[
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
            ]
        )

        form.set_value("authors[0].family_name", "Lovelace")

        self.assertEqual(form.get_value("authors[0].family_name"), "Lovelace")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_array_rerender_preserves_existing_widget_values(self) -> None:
        form = AIForm(
            fields=[
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
            ]
        )

        root = form.widget()
        array_widget = root.children[0]
        add_button = array_widget.children[2]

        self.assertEqual(form._widgets["authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["authors[0].family_name"].value, "Lovelace")

        add_button.click()

        self.assertEqual(form.get_value("authors[0].given_name"), "Ada")
        self.assertEqual(form._widgets["authors[0].given_name"].value, "Ada")
        self.assertEqual(form._widgets["authors[0].family_name"].value, "Lovelace")
        self.assertEqual(form._widgets["authors[1].given_name"].value, "")

    def test_hook_updates_values(self) -> None:
        form = AIForm(fields=[fields.Text("title"), fields.Text("slug")])

        @form.on_change("title")
        def update_slug(ctx):
            ctx.set_value("slug", ctx.value.lower().replace(" ", "-"))

        form.set_value("title", "Example Paper")

        self.assertEqual(form.get_value("slug"), "example-paper")

    def test_cycle_detection(self) -> None:
        form = AIForm(fields=[fields.Text("title"), fields.Text("slug")])

        @form.on_change("title")
        def update_slug(ctx):
            ctx.set_value("slug", "example")

        @form.on_change("slug")
        def update_title(ctx):
            ctx.set_value("title", "Example")

        with self.assertRaisesRegex(RuntimeError, "Cyclic hook update detected"):
            form.set_value("title", "Start")

    def test_action_handler(self) -> None:
        form = AIForm(fields=[fields.Text("title")], actions=[Action(id="save", label="Save")])
        calls = []

        @form.on_action("save")
        def save(ctx):
            calls.append(ctx.action.id)

        form._run_action(form.actions[0])

        self.assertEqual(calls, ["save"])

    def test_missing_action_handler_fails_fast(self) -> None:
        form = AIForm(actions=[Action(id="save", label="Save")])

        with self.assertRaisesRegex(RuntimeError, "No handler registered"):
            form._run_action(form.actions[0])

    def test_step_without_fields_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required 'fields'"):
            AIForm(steps=[{"id": "broken"}])

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_array_without_item_schema_fails_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an item schema"):
            AIForm(fields=[fields.Array("authors")])

    def test_duplicate_field_ids_fail_fast(self) -> None:
        with self.assertRaisesRegex(ValueError, "Duplicate field id"):
            AIForm(fields=[fields.Text("title"), fields.Text("title")])

    def test_fields_and_steps_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot define both fields and steps"):
            AIForm(fields=[fields.Text("title")], steps=[{"id": "metadata", "fields": []}])

    def test_unknown_action_handler_fails_fast(self) -> None:
        form = AIForm(actions=[Action(id="save", label="Save")])

        with self.assertRaisesRegex(ValueError, "Unknown action id"):

            @form.on_action("deposit")
            def deposit(ctx):
                raise AssertionError("should not register")

    def test_duplicate_action_handler_fails_fast(self) -> None:
        form = AIForm(actions=[Action(id="save", label="Save")])

        @form.on_action("save")
        def save(ctx):
            return None

        with self.assertRaisesRegex(ValueError, "already registered"):

            @form.on_action("save")
            def save_again(ctx):
                raise AssertionError("should not register")


if __name__ == "__main__":
    unittest.main()
