# [AI]pywidgets

[![Launch on Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/yacchin1205/aipywidgets/HEAD?labpath=examples%2Fbasic_form.ipynb)

`[AI]pywidgets` is an AI-assisted form toolkit for Jupyter Notebook and JupyterLab,
built on top of `ipywidgets`.

It is designed for metadata entry workflows: files, papers, images, experiments,
datasets, and other structured records. Users can fill out forms manually, while
developers can customize form behavior in Python. The same form can also use an
OpenAI-compatible API for prompt-based completion, normalization, validation, and
chat-based assistance.

## Concept

`[AI]pywidgets` brings three ideas into a single form experience:

1. Composite fields and wizard-style multi-step forms
2. Dynamic form behavior through Python hooks and AI assists
3. A chat assistant that can inspect form state and propose approved edits

Raw `ipywidgets` gives developers low-level UI components.
`[AI]pywidgets` adds a thin layer for form schemas, state management, input events,
AI integration, chat tools, and approval flows.

## Getting Started

Install directly from Git:

```bash
pip install "aipywidgets @ git+https://github.com/yacchin1205/aipywidgets.git@v2026.05.1"
```

During early development, install from a local checkout:

```bash
pip install -e ".[dev]"
```

Open JupyterLab:

```bash
jupyter lab
```

Create and display a form in a notebook:

```python
from aipywidgets import AIForm, Action, fields

form = AIForm(
    title="Paper metadata",
    steps=[
        {
            "id": "main",
            "label": "Main",
            "fields": [
                fields.Text("doi", label="DOI", full_width=True),
                fields.Text("title", label="Title", full_width=True),
                fields.Int("year", label="Year"),
            ],
        },
    ],
    actions=[Action(id="save", label="Save", style="primary")],
)

form
```

Read and update values from Python:

```python
form.get_values()
form.set_value("title", "Example paper")
```

## Forms and Wizards

Every form is defined as one or more steps. A compact form uses a single step;
larger workflows can split fields across multiple steps.

```python
from aipywidgets import AIForm, Action, fields

form = AIForm(
    title="Paper metadata",
    steps=[
        {
            "id": "main",
            "label": "Main",
            "fields": [
                fields.Text("doi", label="DOI", full_width=True),
                fields.Text("title", label="Title", full_width=True),
                fields.Array(
                    "authors",
                    label="Authors",
                    item=fields.Object(
                        fields=[
                            fields.Text("given_name", label="Given name"),
                            fields.Text("family_name", label="Family name"),
                            fields.Text("orcid", label="ORCID"),
                        ],
                    ),
                ),
                fields.Int("year", label="Year"),
                fields.Textarea("abstract", label="Abstract"),
            ],
        },
    ],
    actions=[Action(id="save", label="Save", style="primary")],
)

form
```

Multi-step forms group fields into labeled steps. The UI shows one step at a time,
uses `Previous` / `Next` navigation, and validates the current step before moving
forward.

```python
form = AIForm(
    title="Dataset registration",
    steps=[
        {
            "id": "file",
            "label": "File",
            "fields": [
                fields.File("source_file", label="File"),
                fields.Text("checksum", label="Checksum", full_width=True),
            ],
        },
        {
            "id": "metadata",
            "label": "Metadata",
            "fields": [
                fields.Text("title", label="Title", full_width=True),
                fields.Textarea("description", label="Description", full_width=True),
                fields.Tags("keywords", label="Keywords"),
            ],
        },
        {
            "id": "review",
            "label": "Review",
            "fields": [
                fields.Checkbox("confirmed", label="Confirmed"),
            ],
        },
    ],
    actions=[Action(id="save", label="Save", style="primary")],
)
```

Mark fields as required when the wizard should block `Next` until they are filled:

```python
fields.Text("title", label="Title", required=True)
```

Mark fields as `full_width=True` when they should expand to the full notebook form width:

```python
fields.Textarea("abstract", label="Abstract", full_width=True)
```

## Actions

Forms must define explicit user actions such as saving, submitting, or depositing
metadata. Action labels are defined in the form schema, while Python handlers are
registered by action id.

```python
from aipywidgets import Action

form = AIForm(
    title="Dataset deposit",
    steps=[...],
    actions=[
        Action(
            id="deposit",
            label="Deposit",
            style="primary",
            requires_confirmation=True,
        ),
    ],
)

@form.on_action("deposit")
def deposit(ctx):
    result = deposit_to_repository(ctx.values)
    ctx.info(f"Deposited: {result.url}")
```

Actions are user-triggered UI operations. They are separate from chat tools:
the assistant can help prepare metadata, but final operations such as deposit
should run through an explicit action.

## Field Types

Fields can represent primitive values, repeated values, and nested objects.
This allows metadata schemas to stay close to their natural JSON-like shape.

Primitive fields include:

- `fields.Text`
- `fields.Textarea`
- `fields.Int`
- `fields.Float`
- `fields.Checkbox`
- `fields.File`
- `fields.Select`
- `fields.Tags`

Structured fields include:

- `fields.Array`: a repeated list of items
- `fields.Object`: a dictionary-like group of named child fields

For example, a paper author list can be represented as an array of objects:

```python
fields.Array(
    "authors",
    label="Authors",
    item=fields.Object(
        fields=[
            fields.Text("given_name", label="Given name"),
            fields.Text("family_name", label="Family name"),
            fields.Text("orcid", label="ORCID"),
        ],
    ),
)
```

An object field can also be used for grouped metadata:

```python
fields.Object(
    "license",
    label="License",
    fields=[
        fields.Text("name", label="Name"),
        fields.Text("url", label="URL"),
    ],
)
```

The resulting form values should be plain Python data structures:

```python
{
    "authors": [
        {
            "given_name": "Ada",
            "family_name": "Lovelace",
            "orcid": "",
        }
    ],
    "license": {
        "name": "CC BY 4.0",
        "url": "https://creativecommons.org/licenses/by/4.0/",
    },
}
```

## Field Paths

Nested values need a lightweight way to address individual fields. `[AI]pywidgets`
uses field paths rather than XPath or a full query language.

Absolute paths start at the form root:

```python
form.get_value("title")
form.get_value("license.url")
form.get_value("authors[0].family_name")

form.set_value("license.name", "CC BY 4.0")
form.set_value("authors[0].orcid", "0000-0000-0000-0000")
```

The path syntax is intentionally small:

- `name`: a top-level field or object property
- `object.child`: a child field inside an object
- `array[0]`: an item inside an array
- `array[0].child`: a child field inside an array item
- `array[*].child`: a wildcard path for hooks and selection

Wildcard paths are useful for reacting to repeated structures:

```python
@form.on_change("authors[*].orcid")
def normalize_orcid(ctx):
    normalized = normalize_orcid_value(ctx.value)
    ctx.set_value(".", normalized)
```

Hooks can also use relative paths. Relative paths are evaluated from the current
hook scope. For changes inside an object array, the scope is the current array
item, which makes sibling updates concise.

```python
@form.on_change("authors[*].given_name")
def update_author_display_name(ctx):
    given_name = ctx.get_value("./given_name")
    family_name = ctx.get_value("./family_name")

    ctx.set_value("./display_name", f"{given_name} {family_name}".strip())
```

Supported relative forms:

- `.`: the changed field itself
- `./child`: a child or sibling inside the current hook scope
- `../child`: a value in the parent scope

For direct writes, paths should resolve to one concrete field. Wildcards are for
observation, validation, selection, and batch-style helpers, not implicit
multi-field writes.

## Python Hooks

Developers can customize form reactions in Python.

For example, when a DOI is entered, a hook can call an external API and fill in
the title, authors, and publication year.

```python
@form.on_change("doi")
def fill_from_doi(ctx):
    doi = ctx.values["doi"]
    metadata = lookup_crossref(doi)

    ctx.set_value("title", metadata.title)
    ctx.set_value("authors", metadata.authors)
    ctx.set_value("authors[0].orcid", metadata.primary_author_orcid)
    ctx.set_value("year", metadata.year)
```

Hooks receive a context object with the current form values, the changed field,
the form instance, logging helpers, and APIs for updating values.

Circular hook updates are treated as errors.

For example:

```python
@form.on_change("title")
def update_slug(ctx):
    ctx.set_value("slug", make_slug(ctx.value))

@form.on_change("slug")
def update_title(ctx):
    ctx.set_value("title", title_from_slug(ctx.value))
```

## AI Assists

AI assists generate prompt-based suggestions from current form state.
They are suited to tasks such as completion, normalization, and review.

```python
from aipywidgets import WhenIdle

form.ai.assist(
    id="suggest_keywords",
    label="Suggest keywords",
    watch=["abstract"],
    trigger=WhenIdle(ms=1200, min_chars=80),
    prompt="""
    The user entered the following abstract.
    Suggest 3 to 6 concise keywords.

    Abstract:
    {{ values.abstract }}
    """,
    outputs={
        "keywords": "A list of short keywords",
    },
)
```

AI assists are designed for OpenAI-compatible clients. AI-generated changes
should be reviewable by default: the UI presents proposed changes and applies
them only after user approval.

## Credentials

`[AI]pywidgets` does not collect, render, store, or persist API keys. Pass an
already configured OpenAI-compatible client instead:

```python
from openai import OpenAI
from aipywidgets import AIConfig

ai = AIConfig(
    client=OpenAI(api_key=api_key, base_url=base_url),
    model="gpt-5.4-mini",
)
```

Credential handling belongs to the caller or deployment layer. Avoid
`ipywidgets.Password` for API keys because widget state may be saved in
notebooks.

## Chat Assistant

The chat assistant supports conversational field completion and revision.
Suggested form updates remain reviewable: users can accept or reject them
before any values are applied.

## Design Principles

- Work naturally with `ipywidgets`
- Keep the workflow inside notebooks
- Let Python define both form structure and behavior
- Treat AI integration as optional
- Make form changes observable and traceable
- Detect cyclic hook updates and report them as errors
- Require user approval for AI-initiated form edits by default
- Allow OpenAI-compatible API clients to be swapped
