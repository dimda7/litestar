---
name: run-tests
description: How to run the project's pytest suite (parsers validation tests)
---

# Running tests

```bash
cd /my_hdd/ii/litestar && venv/bin/python -m pytest
```

- Config in `pytest.ini`: `asyncio_mode = auto`, `testpaths = tests`, `pythonpath = .`.
- Tests cover parser validation logic (`tests/test_*_parser_*.py`); they do not require a running server or DB.
- To run a single file: `venv/bin/python -m pytest tests/test_parser_validation.py -v`.
