"""The pool: pulls pending tasks and hosts their graphs. Owns task lifecycle —
stand down, announce, host the graph, act on the outcome — and nothing about
what a graph decides.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package.
"""
