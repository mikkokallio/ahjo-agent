# Contributing

Open an issue before substantial changes. Keep pull requests focused, include tests for changed behavior, and run:

```powershell
python -m unittest discover -s .\src\pdf-processor -p "test_*.py"
python -m py_compile .\src\ahjo-meetings\main.py .\src\pdf-processor\function_app.py .\ui\app.py .\ui\cache_db.py
az bicep build --file .\infra\main.bicep
```

Do not commit credentials, local caches, generated meeting data, or Azure environment files.