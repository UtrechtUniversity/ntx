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

## Podman/Docker Compose

The default Compose file runs the local development image with Django's
development server and a PostgreSQL container:

```sh
# Optional: override the local admin/admin defaults.
cp .env.example .env
podman compose up --build
# Or: docker compose up --build
```

Open <http://localhost:8000>. The local image runs migrations before starting `runserver`.
It also creates the superuser configured by the `DJANGO_SUPERUSER_*` variables when that
username does not exist.
Source files and templates are mounted individually so image-built static assets are not
hidden by a broad `./app:/app` bind mount.

Compose uses `admin` for both `DJANGO_SUPERUSER_USERNAME` and
`DJANGO_SUPERUSER_PASSWORD` when they are unset or empty.
Values supplied by the shell or `.env` override these local defaults.
`DJANGO_SUPERUSER_EMAIL` is optional.
Direct image starts must provide the required username and password.
Any existing active staff superuser is left unchanged.
In case of an existing non-superuser with the configured username, startup fails.

Media files are stored in a named volume mounted at `/app/media`. This is where Django stores uploaded ingest files.
The development Compose stack uses the `ntx_dev` project name, so its database and media volumes are isolated from the production image test setup.

To test the production image locally, use the standalone `docker-compose.prod.yaml`:

```sh
podman compose -f docker-compose.prod.yaml up --build
```

This uses the `cloud` image target, does not mount source files, sets
`DJANGO_DEBUG=False`, and runs migrations and the superuser command
in the web container before starting Gunicorn.
It uses the `ntx_prod_local` project name to keep database and media volumes separate from dev.

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
