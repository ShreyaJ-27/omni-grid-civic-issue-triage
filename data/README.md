# Sample data

`samples/` contains synthetic JSON examples for the shared contracts in
`core/omnigrid/schemas.py`. Media paths are illustrative and do not point to
real photographs or videos.

Validate the examples and run the schema tests from the repository root with:

```powershell
python -m pytest -q tests\test_schemas.py
```

The package can be imported as `core.omnigrid.schemas` from the repository
root, or as `omnigrid.schemas` when `core` is on `PYTHONPATH`. Nullable fields
without an explicit default in the contract (such as `EvidenceMeta.gps`) must
still be present in input data; use JSON `null` when no value is available.
