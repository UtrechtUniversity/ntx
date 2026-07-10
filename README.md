# Neurotoxicology MEA

## Python

Examples use [uv](https://docs.astral.sh/uv/), but plain pip can be used.

```sh
# Create/activate a virtual environment and install dependencies
cd app
uv python install 3.14
uv venv --python 3.14 
source .venv/bin/activate
uv pip install -r requirements/dev.txt
```

## NodeJS

```sh
cd app/frontend
npm install
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

## Podman Compose

The default Compose file runs the local development image with Django's
development server and a PostgreSQL container:

```sh
podman compose up --build
```

Open <http://localhost:8000>. The local image runs migrations before starting
`runserver`. Source files and templates are mounted individually so image-built
static assets are not hidden by a broad `./app:/app` bind mount.

Media files are stored in a named volume mounted at `/app/media`. This is where Django stores uploaded ingest files.

To test the production image locally, use the standalone `docker-compose.prod.yaml`:

```sh
podman compose -f docker-compose.prod.yaml up --build
```

This uses the `prod` image target, does not mount source files, sets
`DJANGO_DEBUG=False`, runs migrations once in a short-lived `migrate` service,
and then starts the image default Gunicorn command.

## Ingest

For example, using the example data:

```sh
cd app
source .venv/bin/activate
python manage.py import_axion_folder ../data
# Replace existing experiments instead of raising IngestionError:
python manage.py import_axion_folder --replace-existing ../data/ENDpoiNTs
# Control conditions default to DMSO; override if needed:
python manage.py import_axion_folder ../data/ENDpoiNTs --control-chemical Water
```


## Development

```sh
# Update Python requirements
# Use uv instead of plain pip
uv pip compile requirements/base.in --universal --output-file requirements/base.txt
uv pip compile requirements/dev.in --universal --output-file requirements/dev.txt

# Use Ruff linter
ruff check

# Use Ruff formatter
ruff format

# Use Pyright type checking
pyright

# Run the tests from the app dir
# for pytest to discover the configuration
cd app
pytest

# Run Django management commands with activated venv:
cd app
python manage.py <command>
```
