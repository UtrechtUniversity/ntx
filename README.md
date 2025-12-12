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

## Development

```sh
# Update Python requirements
uv pip compile requirements/base.in --universal --output-file requirements/base.txt
uv pip compile requirements/dev.in --universal --output-file requirements/dev.txt
```
