"""The workflow engine's home: the DBOS adapter beneath the `friday.sdk`
workflow port. `adapter` is the one module that imports `dbos`; everything else
in Friday reaches durability through the port and this package.
"""
