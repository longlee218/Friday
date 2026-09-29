"""The kernel registry: a plugin registers its contributions and they land.

The dependency-rule test proves the *shape* is right; this proves the shape
*works* — a `Plugin`'s `register` is handed the registry as a `PluginAPI`, its
`task_type`/`memory_kind` calls collect, and two claims on one id refuse rather
than shadow. The specs used here are throwaway; the real ones arrive in tickets
11 and 12.
"""

from __future__ import annotations

import pytest

from friday.kernel import DuplicateRegistration, Registry
from friday.kernel.registry import PluginRegistration
from friday.sdk import MemoryKindSpec, Origin, Plugin, PluginAPI, TaskTypeSpec


class _Params:
    """A stand-in parameter type — its docstring is what triage would read."""


def test_a_plugin_registers_its_contributions_through_the_api():
    def register(api: PluginAPI) -> None:
        api.task_type(TaskTypeSpec(name="demo:thing", params=_Params))
        api.memory_kind(
            MemoryKindSpec(name="demo.kind", writers=frozenset({Origin.ADMIN}))
        )

    registry = Registry()
    registry.apply(Plugin(id="demo", register=register))

    assert set(registry.task_types()) == {"demo:thing"}
    assert set(registry.memory_kinds()) == {"demo.kind"}


def test_what_a_plugin_is_handed_satisfies_the_plugin_api_protocol():
    """A plugin registers against the Protocol, never the concrete class — the
    dependency rule in the type system."""
    api = PluginRegistration(Registry(), Plugin(id="demo", register=lambda api: None))
    assert isinstance(api, PluginAPI)


def test_a_second_claim_on_a_task_type_id_refuses():
    registry = Registry()
    registry.task_type(TaskTypeSpec(name="demo:thing", params=_Params))

    with pytest.raises(DuplicateRegistration):
        registry.task_type(TaskTypeSpec(name="demo:thing", params=_Params))


def test_a_second_claim_on_a_memory_kind_id_refuses():
    registry = Registry()
    registry.memory_kind(MemoryKindSpec(name="demo.kind"))

    with pytest.raises(DuplicateRegistration):
        registry.memory_kind(MemoryKindSpec(name="demo.kind"))


def test_apply_hands_the_plugin_its_config():
    seen = {}

    def register(api: PluginAPI) -> None:
        seen["config"] = api.config

    Registry().apply(Plugin(id="demo", register=register), config={"k": "v"})

    assert seen["config"] == {"k": "v"}
