"""Domain vocabulary, folded under the kernel (DESIGN-v2 §13).

No persistence, no I/O — the words the rest of the system is written in. The
pure, plugin-facing pieces that used to sit beside these (the workflow actions,
the validation DSL, the prompt primitives, `scrub`, the memory `Origin`) are the
bottom of the stack now and live in `friday.sdk`; what remains here — the models,
the state graph, conversation identity, the memory-write guard, and the triage
outcome types — is kernel-internal and builds on `sdk`.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package.
"""
