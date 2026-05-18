from __future__ import annotations

import asyncio
import json
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aipywidgets import AIConfig, AIForm, Action, WhenIdle, fields
from aipywidgets.ai import parse_patch_proposal


def single_step(*step_fields):
    return [{"id": "main", "label": "Main", "fields": list(step_fields)}]


def save_actions():
    return [Action(id="save", label="Save")]


class FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output=[
                {
                    "type": "function_call",
                    "name": "propose_form_update",
                    "call_id": f"call_{len(self.calls)}",
                    "arguments": self.output_text,
                }
            ],
            output_text=self.output_text,
        )


class FakeClient:
    def __init__(self, output_text: str) -> None:
        self.responses = FakeResponses(output_text)


class FailingResponses:
    def create(self, **kwargs):
        raise RuntimeError("upstream unavailable")


class FailingClient:
    def __init__(self) -> None:
        self.responses = FailingResponses()


class AITests(unittest.TestCase):
    def _set_value_in_running_loop(self, form: AIForm, path: str, value: str, *, cancel_assist_id: str | None = None) -> None:
        async def scenario() -> None:
            form.set_value(path, value)
            if cancel_assist_id is not None:
                form.ai._timers.pop(cancel_assist_id).cancel()

        asyncio.run(scenario())

    def test_parse_patch_proposal(self) -> None:
        proposal = parse_patch_proposal(
            json.dumps(
                {
                    "message": "Suggested keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["metadata", "dataset"]}
                    ],
                }
            )
        )

        self.assertEqual(proposal.message, "Suggested keywords.")
        self.assertEqual(proposal.operations[0].path, "keywords")
        self.assertEqual(proposal.operations[0].value, ["metadata", "dataset"])

    def test_ai_assist_creates_proposal_after_idle_without_applying_it(self) -> None:
        async def scenario() -> None:
            client = FakeClient(
                json.dumps(
                    {
                        "message": "Use generated keywords.",
                        "operations": [
                            {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                        ],
                    }
                )
            )
            form = AIForm(
                steps=single_step(
                    fields.Textarea("abstract"),
                    fields.Tags("keywords"),
                ),
                actions=save_actions(),
                ai=AIConfig(client=client, model="test-model"),
            )
            form.ai.assist(
                id="suggest_keywords",
                label="Suggest keywords",
                watch=["abstract"],
                trigger=WhenIdle(ms=10),
                prompt="Suggest keywords for {{ values.abstract }}",
                outputs={"keywords": "A list of keywords"},
            )
            form.widget()

            form.set_value("abstract", "This dataset contains notebook metadata.")
            self.assertEqual(form.proposals, [])

            await asyncio.sleep(0.05)

            self.assertEqual(form.get_value("keywords"), [])
            self.assertEqual(len(form.proposals), 1)
            self.assertEqual(form.proposals[0].operations[0].value, ["ai", "metadata"])
            self.assertEqual(client.responses.calls[0]["model"], "test-model")
            self.assertEqual(client.responses.calls[0]["tool_choice"]["name"], "propose_form_update")
            self.assertEqual(client.responses.calls[0]["tools"][0]["name"], "propose_form_update")

        asyncio.run(scenario())

    def test_ai_assist_schema_does_not_use_untyped_array_items(self) -> None:
        client = FakeClient('{"message": "", "operations": []}')
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )

        form.set_value("abstract", "Text")
        form.create_proposal("suggest_keywords")

        schema = client.responses.calls[0]["tools"][0]["parameters"]
        value_schema = schema["properties"]["operations"]["items"]["properties"]["value"]
        array_schema = value_schema["anyOf"][4]
        self.assertIn("type", array_schema["items"]["anyOf"][0])

    def test_rejected_proposal_feedback_is_used_by_next_message(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [{"op": "set", "path": "keywords", "value": ["ai"]}],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.set_value("abstract", "Text")
        form.create_proposal("suggest_keywords")
        form.reject_proposal(0)

        form.submit_assist_message("suggest_keywords", "Use domain-specific terms.")

        second_input = client.responses.calls[1]["input"]
        self.assertIn("rejected", json.dumps(second_input))
        self.assertIn("Use domain-specific terms.", json.dumps(second_input))
        self.assertEqual(len(form.proposals), 1)

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_chat_history_keeps_all_events_in_scroll_area(self) -> None:
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=FakeClient('{"message": "", "operations": []}'), model="test-model"),
        )
        for index in range(8):
            form._record_ai_event("user" if index % 2 else "assistant", f"event {index}")

        html = form._assist_history_html()

        self.assertIn("aipy-assist-history", html)
        self.assertIn("event 0", html)
        self.assertIn("event 7", html)
        self.assertIn("aipy-assist-event-row-user", html)
        self.assertIn("aipy-assist-event-row-assistant", html)
        self.assertLess(html.index("event 7"), html.index("event 0"))

        css = form._assist_css()
        self.assertIn("flex-direction: column-reverse", css)

    def test_accept_proposal_applies_patch_and_records_feedback(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                    ],
                }
            )
        )
        form = AIForm(
            steps=single_step(
                fields.Textarea("abstract"),
                fields.Tags("keywords"),
            ),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.set_value("abstract", "Text")
        form.create_proposal("suggest_keywords")

        form.accept_proposal(0)

        self.assertEqual(form.get_value("keywords"), ["ai", "metadata"])
        self.assertEqual(form.proposals, [])
        self.assertEqual(form.approval_events[0]["status"], "accepted")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_create_proposal_failure_logs_and_shows_error(self) -> None:
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=FailingClient(), model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.widget()
        self._set_value_in_running_loop(form, "abstract", "Text", cancel_assist_id="suggest_keywords")

        with self.assertLogs("aipywidgets.form", level="ERROR") as logs:
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                form.create_proposal("suggest_keywords")

        self.assertIn("AI assist proposal failed: suggest_keywords", "\n".join(logs.output))
        self.assertEqual(form._assist_state["suggest_keywords"], "error")
        self.assertIsInstance(form._assist_errors["suggest_keywords"], RuntimeError)
        error_bubble = form._assist_layer_widget.children[0].children[0].value
        self.assertIn("AI suggestion failed", error_bubble)
        self.assertIn("RuntimeError", error_bubble)
        self.assertIn("upstream unavailable", error_bubble)

    def test_reject_proposal_records_feedback_without_applying_patch(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                    ],
                }
            )
        )
        form = AIForm(
            steps=single_step(
                fields.Textarea("abstract"),
                fields.Tags("keywords"),
            ),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.set_value("abstract", "Text")
        form.create_proposal("suggest_keywords")

        form.reject_proposal(0)

        self.assertEqual(form.get_value("keywords"), [])
        self.assertEqual(form.proposals, [])
        self.assertEqual(form.approval_events[0]["status"], "rejected")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_reject_proposal_opens_chat_input(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                    ],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.widget()
        self._set_value_in_running_loop(form, "abstract", "Text", cancel_assist_id="suggest_keywords")
        form.create_proposal("suggest_keywords")

        form.reject_proposal(0)

        bubble = form._assist_layer_widget.children[0]
        self.assertIn("Add instructions", bubble.children[0].value)
        self.assertFalse(bubble.children[-1].children[0].disabled)
        self.assertEqual(form._assist_state["suggest_keywords"], "chat")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_enter_in_chat_input_sends_message(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                    ],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.widget()
        self._set_value_in_running_loop(form, "abstract", "Text", cancel_assist_id="suggest_keywords")
        form.create_proposal("suggest_keywords")
        form.reject_proposal(0)
        input_widget = form._assist_chat_inputs["suggest_keywords"]
        input_widget.value = "Use Japanese metadata terms."

        input_widget._submission_callbacks(input_widget)

        self.assertEqual(input_widget.value, "")
        self.assertEqual(len(client.responses.calls), 2)
        self.assertIn("Use Japanese metadata terms.", json.dumps(client.responses.calls[1]["input"]))
        self.assertEqual(form._assist_state["suggest_keywords"], "proposal")

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_clear_assist_surfaces_can_keep_active_surface(self) -> None:
        import ipywidgets as widgets

        form = AIForm(steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")), actions=save_actions())
        form.widget()
        active_child = widgets.HTML("active")
        form._assist_layer_widget.children = (active_child,)

        form._clear_assist_surfaces(except_path="abstract")

        self.assertEqual(form._assist_layer_widget.children, (active_child,))

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_accept_proposal_closes_assist_bubble(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [
                        {"op": "set", "path": "keywords", "value": ["ai", "metadata"]}
                    ],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.widget()
        self._set_value_in_running_loop(form, "abstract", "Text", cancel_assist_id="suggest_keywords")
        form.create_proposal("suggest_keywords")

        form.accept_proposal(0)

        self.assertEqual(form._assist_layer_widget.children, ())

    def test_unknown_proposal_index_fails_fast(self) -> None:
        form = AIForm(steps=single_step(fields.Text("title")), actions=save_actions())

        with self.assertRaisesRegex(IndexError, "Proposal index out of range"):
            form.accept_proposal(0)

    def test_ai_assist_requires_config(self) -> None:
        form = AIForm(steps=single_step(fields.Text("title"), fields.Text("slug")), actions=save_actions())

        with self.assertRaisesRegex(RuntimeError, "requires AIConfig"):
            form.ai.assist(
                id="make_slug",
                label="Make slug",
                watch=["title"],
                trigger=WhenIdle(),
                prompt="Make slug",
                outputs={"slug": "Slug"},
            )

    def test_ai_assist_replaces_stale_proposal(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [{"op": "set", "path": "keywords", "value": ["new"]}],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )

        form.set_value("abstract", "First")
        form.create_proposal("suggest_keywords")
        form.set_value("abstract", "Second")

        self.assertTrue(form.proposals[0].stale)
        form.create_proposal("suggest_keywords")

        self.assertEqual(len(form.proposals), 1)
        self.assertFalse(form.proposals[0].stale)

    def test_accepting_stale_proposal_fails_fast(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [{"op": "set", "path": "keywords", "value": ["old"]}],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        form.set_value("abstract", "First")
        form.create_proposal("suggest_keywords")
        form.set_value("abstract", "Second")

        with self.assertRaisesRegex(RuntimeError, "stale AI proposal"):
            form.accept_proposal(0)

    @unittest.skipIf(importlib.util.find_spec("ipywidgets") is None, "ipywidgets is not installed")
    def test_ai_assist_bubble_uses_zero_height_field_surface(self) -> None:
        client = FakeClient(
            json.dumps(
                {
                    "message": "Use generated keywords.",
                    "operations": [{"op": "set", "path": "keywords", "value": ["new"]}],
                }
            )
        )
        form = AIForm(
            steps=single_step(fields.Textarea("abstract"), fields.Tags("keywords")),
            actions=save_actions(),
            ai=AIConfig(client=client, model="test-model"),
        )
        form.ai.assist(
            id="suggest_keywords",
            label="Suggest keywords",
            watch=["abstract"],
            trigger=WhenIdle(ms=100000),
            prompt="Suggest",
            outputs={"keywords": "Keywords"},
        )
        rendered = form.widget()

        self._set_value_in_running_loop(form, "abstract", "Text", cancel_assist_id="suggest_keywords")

        abstract_shell = rendered.children[2].children[0]
        self.assertIs(abstract_shell.children[0], form._widgets["abstract"])
        self.assertEqual(len(abstract_shell.children), 2)
        bubble = form._assist_layer_widget.children[0]
        self.assertIn("aipy-assist-bubble-wrap", bubble._dom_classes)
        self.assertIn("AI will suggest", bubble.children[0].value)
        self.assertIn("aipy-assist-bubble", bubble.children[0].value)
        self.assertNotIn("aipy-assist-proposal-wrap {\n  top:", bubble.children[0].value)
        self.assertNotIn("aipy-assist-proposal-wrap::before", bubble.children[0].value)
        self.assertEqual(len(form._assist_layer_widget.children), 1)

    def test_ai_config_requires_explicit_client(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "AIConfig.client is required"):
            AIConfig(client=None, model="test-model")

    def test_ai_config_requires_model(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "AIConfig.model is required"):
            AIConfig(client=FakeClient('{"message": "", "operations": []}'), model="")


if __name__ == "__main__":
    unittest.main()
