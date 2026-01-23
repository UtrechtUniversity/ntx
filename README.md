# Neurotoxicology MEA

## Python

Examples use [uv](https://docs.astral.sh/uv/), but plain pip can be used.

```sh
# Create/activate a virtual environment and install dependencies
cd app
uv python install 3.14
uv venv
source .venv/bin/activate
uv pip install -r requirements/dev.txt
```

## NodeJS

```sh
cd app/frontend
npm install
npm install plotly.js-dist-min
# Live update tailwindcss:
npm run dev
```

## Django

```sh
cd app
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Ingest

For example, using the example data:

```sh
cd app
source .venv/bin/activate
python manage.py import_axion_folder ../data
# Control conditions default to DMSO; override if needed:
python manage.py import_axion_folder ../data --control-chemical Water
```


## Development

```sh
# Update Python requirements
# Use uv instead of plain pip
uv pip compile requirements/base.in --universal --output-file requirements/base.txt
uv pip compile requirements/dev.in --universal --output-file requirements/dev.txt
```

# Use Ruff linter
`ruff check`

# Use Ruff formatter
`ruff format`

# Use Pyright type checking
`pyright`

# Run the tests from the app dir
```sh
cd app
pytest
```

# Run Django management commands with activated venv:
```sh
cd app
python manage.py <command>
```
