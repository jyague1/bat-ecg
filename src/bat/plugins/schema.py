"""The standard BAT plugin module schema.

Every BAT plugin module must expose a ``schema`` attribute: a
:class:`ModuleSchema` subclass (the *class itself*, not an instance) that
describes the module to the engine -- its dotted name, description,
citations, and the shape of its parameters/inputs/outputs::

    from pydantic import BaseModel, Field
    from bat.plugins.schema import ModuleSchema, InputField, OutputField

    class MyModuleSchema(ModuleSchema):
        class Meta:
            name = "lab.ecg.detect_rpeaks"
            description = "Detect R-peaks using the Pan-Tompkins algorithm."
            citations = ["Pan J, Tompkins W. ... IEEE TBME. 1985."]
            examples = []

        class Params(BaseModel):
            min_distance_ms: float = Field(default=200.0, gt=0)

        class Inputs(BaseModel):
            signal: InputField(artifact_type="signal", artifact_format="wfdb")

        class Outputs(BaseModel):
            rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")

    schema = MyModuleSchema

``Meta.citations`` is required for every module: either a non-empty list of
citation strings, or the literal string ``"none"`` for modules that do not
wrap any published algorithm (e.g. plain I/O modules). This is enforced at
plugin discovery time -- see ``bat.plugins.discovery``.

See ``cards/backlog/CARD-007-plugin-interface-schema.md`` for the full
spec.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from bat.artifacts.types import ARTIFACT_TYPES, DEFAULT_FORMATS

# --- Artifact types / formats -------------------------------------------

# Artifact-type vocabulary and default formats live in one place
# (bat.artifacts.types). ``DEFAULT_ARTIFACT_FORMATS`` is kept as an alias
# of the shared ``DEFAULT_FORMATS`` for the name this module has always
# exported.
DEFAULT_ARTIFACT_FORMATS = DEFAULT_FORMATS


def _artifact_reference_annotation(artifact_type: str, artifact_format: str | None) -> Any:
    """Build the ``Annotated[...]`` value used as an artifact-reference
    field's *type annotation* inside an ``Inputs``/``Outputs`` model.

    An artifact reference field is declared without a ``=`` in the class
    body, e.g. ``signal: InputField(artifact_type="signal", ...)`` -- the
    return value of ``InputField``/``OutputField`` therefore has to work as
    the annotation itself, not as an assigned default. ``Annotated[str,
    Field(...)]`` satisfies that: Pydantic treats the annotated ``str`` as
    the field's declared type (a placeholder for "a reference to an
    artifact of this type", e.g. an artifact id/path resolved by the
    engine at run time) and reads the ``FieldInfo`` for constraints/extra
    metadata, exactly as it would for ``x: str = Field(...)``.
    """
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(
            f"Invalid artifact_type {artifact_type!r}; must be one of "
            f"{sorted(ARTIFACT_TYPES)}"
        )

    if artifact_format is None:
        artifact_format = DEFAULT_ARTIFACT_FORMATS[artifact_type]

    return Annotated[
        str,
        Field(
            description=f"{artifact_type} artifact ({artifact_format})",
            json_schema_extra={
                "artifact_type": artifact_type,
                "artifact_format": artifact_format,
            },
        ),
    ]


def InputField(artifact_type: str, artifact_format: str | None = None) -> Any:
    """Declare a module input field carrying artifact type/format metadata.

    Used as the *annotation* of a field in a ``schema.Inputs`` model::

        class Inputs(BaseModel):
            signal: InputField(artifact_type="signal", artifact_format="wfdb")

    The artifact type/format are recoverable later via
    ``Inputs.model_fields["signal"].json_schema_extra``.
    """
    return _artifact_reference_annotation(artifact_type, artifact_format)


def OutputField(artifact_type: str, artifact_format: str | None = None) -> Any:
    """Declare a module output field carrying artifact type/format metadata.

    See :func:`InputField`; identical behavior, used for ``schema.Outputs``
    models.
    """
    return _artifact_reference_annotation(artifact_type, artifact_format)


# --- Citations -------------------------------------------------------------

#: The literal value allowed in ``Meta.citations`` in place of a non-empty
#: list of citation strings.
NO_CITATIONS = "none"


def citations_are_valid(citations: Any) -> bool:
    """Return whether ``citations`` is a legal ``Meta.citations`` value.

    Valid values are the literal string ``"none"``, or a non-empty list of
    strings.
    """
    if citations == NO_CITATIONS:
        return True
    if isinstance(citations, list) and len(citations) > 0:
        return all(isinstance(item, str) for item in citations)
    return False


# --- ModuleSchema ------------------------------------------------------


class ModuleSchema:
    """Base class every plugin module's ``schema`` attribute must subclass.

    Subclasses declare four nested classes:

    - ``Meta``: plain class carrying ``name``, ``description``,
      ``citations`` (required -- list[str] or the literal ``"none"``), and
      optionally ``examples``.
    - ``Params``: a ``pydantic.BaseModel`` describing the module's
      parameters.
    - ``Inputs``: a ``pydantic.BaseModel`` describing the module's inputs,
      whose fields are declared with :func:`InputField`.
    - ``Outputs``: a ``pydantic.BaseModel`` describing the module's
      outputs, whose fields are declared with :func:`OutputField`.

    Any of these may be omitted, in which case an empty default is used
    (an empty ``Meta``/``BaseModel``) -- except ``Meta.citations``, which
    is required and enforced separately at plugin discovery time (see
    ``bat.plugins.discovery``).
    """

    class Meta:
        name: str = ""
        description: str = ""
        citations: list[str] | Literal["none"] | None = None
        examples: list[dict] = []

    class Params(BaseModel):
        pass

    class Inputs(BaseModel):
        pass

    class Outputs(BaseModel):
        pass

    @classmethod
    def to_json_schema(cls) -> dict:
        """Export the full module schema (Meta + Params/Inputs/Outputs) as
        a JSON-Schema-flavored ``dict``.

        ``Params``/``Inputs``/``Outputs`` are exported via Pydantic's
        ``model_json_schema()``; artifact reference fields (declared with
        ``InputField``/``OutputField``) surface their ``artifact_type`` /
        ``artifact_format`` under each field's ``properties`` entry
        (via ``json_schema_extra``).
        """
        meta = cls.Meta
        return {
            "name": getattr(meta, "name", ""),
            "description": getattr(meta, "description", ""),
            "citations": getattr(meta, "citations", None),
            "examples": getattr(meta, "examples", []),
            "params": cls.Params.model_json_schema(),
            "inputs": cls.Inputs.model_json_schema(),
            "outputs": cls.Outputs.model_json_schema(),
        }
