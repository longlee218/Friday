"""The store's repositories: cohesive method groups the `Database` facade composes
(ticket 16). Each is a mixin on `Database`, so `self._sessions` and cross-repo
`self.` calls work exactly as they did in the one class."""
