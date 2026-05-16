# aipywidgets

`aipywidgets` is an AI-assisted form toolkit for Jupyter Notebook and JupyterLab,
built on top of `ipywidgets`.

It is designed for metadata entry workflows: files, papers, images, experiments,
datasets, and other structured records. Users can fill out forms manually, while
developers can customize form behavior in Python. The same form can also use an
OpenAI-compatible API for prompt-based completion, normalization, validation, and
chat-based assistance.

## Concept

`aipywidgets` brings three ideas into a single form experience:

1. Composite fields and wizard-style multi-step forms
2. Dynamic form behavior through Python hooks and AI hooks
3. A chat assistant that can inspect form state and propose approved edits

Raw `ipywidgets` gives developers low-level UI components.
`aipywidgets` adds a thin layer for form schemas, state management, input events,
AI integration, chat tools, and approval flows.

## Use Cases

- Paper metadata entry, such as DOI, title, authors, publication year, and abstract
- File metadata entry for images, audio, video, PDFs, and other local assets
- Structured annotation for lab notes, experiments, and observations
- Dataset registration forms
- Semi-automated, reviewable data entry workflows inside notebooks

## Forms and Wizards

A form can be a single-page form or a multi-step wizard.

```python
from aipywidgets import AIForm, fields

form = AIForm(
    title="Paper metadata",
    fields=[
        fields.Text("doi", label="DOI"),
        fields.Text("title", label="Title"),
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
)

form
```

Wizard forms group fields into steps and provide previous / next navigation.

```python
form = AIForm(
    title="Dataset registration",
    steps=[
        {
            "id": "file",
            "label": "File",
            "fields": [
                fields.File("source_file", label="File"),
                fields.Text("checksum", label="Checksum"),
            ],
        },
        {
            "id": "metadata",
            "label": "Metadata",
            "fields": [
                fields.Text("title", label="Title"),
                fields.Textarea("description", label="Description"),
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
)
```

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

Nested values need a lightweight way to address individual fields. `aipywidgets`
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

Hook updates are part of the normal form event graph. `aipywidgets` should not
provide an escape hatch such as disabling hooks for a specific write. Instead,
the hook runner should detect cyclic updates and raise an error.

For example, this should be treated as a hook cycle:

```python
@form.on_change("title")
def update_slug(ctx):
    ctx.set_value("slug", make_slug(ctx.value))

@form.on_change("slug")
def update_title(ctx):
    ctx.set_value("title", title_from_slug(ctx.value))
```

The error should include the field path chain that caused the cycle, so the
developer can decide which hook owns the derived value.

## AI Hooks

In addition to traditional Python hooks, developers can define prompt-based AI
hooks.

```python
form.ai.on_change(
    "abstract",
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

AI hooks are designed for OpenAI-compatible clients. The first implementation
will focus on:

- Completing one field from existing form values
- Normalizing user input
- Generating candidate values
- Detecting missing or inconsistent fields
- Producing review summaries

AI-generated changes should be reviewable by default. The intended behavior is
to present proposed changes and apply them only after user approval.

## Chat Assistant

A form can display a chat window in the lower-left or lower-right corner.

```python
form.enable_chat(
    position="right",
    instructions="""
    You help the user complete metadata fields.
    Ask before applying changes to the form.
    """,
)
```

The chat assistant is intended to use the OpenAI Responses API. It can interact
with the form through tool calls.

Examples of built-in tools:

- `get_form_values`: return the current form values
- `get_field_schema`: return the field definitions
- `propose_form_update`: create a proposed update to the form

Developers can provide custom tools when enabling chat.

```python
form.enable_chat(
    tools=[
        lookup_crossref_tool,
        search_local_files_tool,
        validate_metadata_tool,
    ],
)
```

The assistant should not directly apply form updates through a tool call.
Instead, it proposes a patch, the UI presents the patch for review, and the
approval layer applies it only after the user accepts it.

Approval results are part of the conversation. When the user accepts, rejects,
or partially accepts a proposal, that result should be sent back to the
assistant so it can continue from the actual state of the workflow. This matters
because form completion is often iterative: the first proposal may be incomplete,
partially wrong, or blocked by validation errors.

Custom tools should be explicit about their behavior:

- read-only tools can inspect external or form-related data
- proposal tools can return candidate patches
- side-effecting tools should require approval before their effects are used

## Design Principles

- Work naturally with `ipywidgets`
- Keep the workflow inside notebooks
- Let Python define both form structure and behavior
- Treat AI integration as optional
- Make form changes observable and traceable
- Detect cyclic hook updates and report them as errors
- Require user approval for AI-initiated form edits by default
- Allow OpenAI-compatible API clients to be swapped

## Initial Scope

The MVP should include:

- Basic field definitions
- Array and object fields for nested metadata
- Field paths for nested reads, writes, hooks, and selections
- Single-page forms
- Wizard-style step display
- Reading and setting form values
- Python hooks
- Hook cycle detection
- Minimal AI hooks
- Chat UI display
- A chat tool for reading current form values
- A chat tool for proposing updates
- User-reviewed proposals and approval-driven form updates

## Non-Goals

The initial version does not aim to provide:

- A general-purpose low-code form builder
- Complex permission management
- Server-side persistence
- A large workflow engine
- A standalone web application outside notebooks

## Development Notes

The internal design is expected to separate the following responsibilities:

- `Field`: primitive and structured field definitions, including validation
- `ArrayField`: repeated field values backed by a child field schema
- `ObjectField`: dictionary-like grouped values backed by named child fields
- `FieldPath`: lightweight absolute and relative paths for nested values
- `FormState`: current values, initial values, and change history
- `AIForm`: the main API for display, events, and hook registration
- `HookContext`: context passed to hook functions
- `HookRunner`: hook execution, dependency tracking, and cycle detection
- `AIHook`: prompt, input values, and output field definitions
- `ChatPanel`: chat UI
- `ToolRegistry`: tools callable from chat
- `Approval`: review, accept, reject, and apply flow for AI-generated proposals
