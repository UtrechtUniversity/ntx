from __future__ import annotations

import shutil
import time
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from ntx.ingest.discovery import parse_filename_metadata, scan_folder
from ntx.ingest.layout import parse_layout_xlsx
from ntx.ingest.service import IngestionError, create_experiment_from_files
from ntx.models import Chemical, Project


class Command(BaseCommand):
    help = "Import Axion MEA exports from a folder"

    def add_arguments(self, parser):
        parser.add_argument("path", help="Folder containing layout + baseline/exposure CSVs")
        parser.add_argument(
            "--project",
            default="default-project",
            help="Project slug to import into (default: default-project)",
        )
        parser.add_argument(
            "--chemical",
            help="Override exposure chemical name (otherwise parsed from filename)",
        )
        parser.add_argument(
            "--control-chemical",
            default="DMSO",
            help="Chemical name for the Control condition (default: DMSO)",
        )
        parser.add_argument(
            "--unit-symbol",
            default="uM",
            help="Concentration unit symbol (default: uM)",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Re-import and overwrite existing experiments with the same code",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Scan and print summary without writing to the database",
        )
        parser.add_argument(
            "--no-copy-into-storage",
            dest="copy_into_storage",
            action="store_false",
            default=True,
            help=(
                "Do not copy the provided folder into MEDIA_ROOT/axion before ingesting "
                "(default: copy)."
            ),
        )

    def handle(self, *args, **options):
        source_path = Path(options["path"])
        project_slug = options["project"]
        dry_run = options["dry_run"]
        unit_symbol = options["unit_symbol"]
        chemical_name = options.get("chemical")
        control_chemical_name = options.get("control_chemical")
        overwrite = options["overwrite"]
        copy_into_storage = options["copy_into_storage"]

        project = self._get_project(project_slug)
        chemical = self._resolve_chemical(chemical_name) if chemical_name else None
        control_chemical = (
            self._resolve_chemical(control_chemical_name) if control_chemical_name else None
        )

        ingest_path = self._prepare_ingest_folder(source_path, copy_into_storage)

        scan_result = scan_folder(ingest_path)
        for error in scan_result.errors:
            self.stdout.write(self.style.WARNING(str(error)))

        if not scan_result.experiments:
            raise CommandError("No experiment files found to import.")

        if dry_run:
            for experiment_folder in scan_result.experiments:
                self._print_summary(experiment_folder)
            return

        for experiment_folder in scan_result.experiments:
            try:
                experiment = create_experiment_from_files(
                    experiment_folder,
                    project=project,
                    chemical=chemical,
                    control_chemical=control_chemical,
                    default_unit_symbol=unit_symbol,
                    overwrite=overwrite,
                )
            except IngestionError as exc:
                raise CommandError(str(exc)) from exc
            except Exception as exc:  # pragma: no cover - defensive logging
                raise CommandError(
                    f"Unexpected error importing {experiment_folder.path}: {exc}"
                ) from exc

            self.stdout.write(self.style.SUCCESS(f"Ingested experiment {experiment.code}"))

    def _get_project(self, slug: str) -> Project:
        try:
            return Project.objects.get(slug=slug)
        except Project.DoesNotExist as exc:
            raise CommandError(f"Project with slug '{slug}' does not exist") from exc

    def _resolve_chemical(self, name: str) -> Chemical:
        chemical = Chemical.objects.filter(name__iexact=name).first()
        if chemical:
            return chemical
        return Chemical.objects.create(name=name)

    def _prepare_ingest_folder(self, source_path: Path, should_copy: bool) -> Path:
        if not source_path.exists() or not source_path.is_dir():
            raise CommandError(f"Path '{source_path}' does not exist or is not a directory")

        storage_root = getattr(default_storage, "location", None)
        if not storage_root:
            raise CommandError(
                "Default storage must expose a local filesystem location; ensure "
                "FileSystemStorage is configured."
            )

        dest_root = Path(storage_root) / "axion"
        dest_root.mkdir(parents=True, exist_ok=True)

        # If already under storage root/axion, reuse it directly.
        try:
            _ = Path(source_path).resolve().relative_to(dest_root.resolve())
            return source_path
        except ValueError:
            pass

        if not should_copy:
            raise CommandError(
                f"Source folder {source_path} is outside storage root {dest_root}; "
                "rerun without --no-copy-into-storage to copy it automatically."
            )

        dest_path = dest_root / source_path.name
        if dest_path.exists():
            timestamp = int(time.time())
            dest_path = dest_root / f"{source_path.name}_{timestamp}"

        shutil.copytree(source_path, dest_path)
        self.stdout.write(
            self.style.SUCCESS(
                f"Copied '{source_path}' into storage at '{dest_path}' for ingestion."
            )
        )
        return dest_path

    def _print_summary(self, experiment_folder):
        metadata = experiment_folder.metadata or parse_filename_metadata(
            experiment_folder.baseline_csv
        )
        try:
            layout = parse_layout_xlsx(experiment_folder.layout_file)
            layout_summary = (
                f"{layout.plate_wells}-well, "
                f"{len(layout.conditions)} conditions, "
                f"date={layout.date}"
            )
        except Exception as exc:
            layout_summary = f"layout parse failed: {exc}"

        self.stdout.write(f"[DRY RUN] {experiment_folder.path}")
        self.stdout.write(
            f"  Code={metadata.code} Chemical={metadata.chemical or 'n/a'} "
            f"Sex={metadata.sex or 'n/a'} DIV={metadata.div or 'n/a'} "
            f"Cell={metadata.cell_line or 'n/a'}"
        )
        self.stdout.write(
            "  Files: layout={layout} baseline={baseline} exposure={exposure}".format(
                layout=Path(experiment_folder.layout_file).name,
                baseline=Path(experiment_folder.baseline_csv).name,
                exposure=Path(experiment_folder.exposure_csv).name,
            )
        )
        self.stdout.write(f"  Layout: {layout_summary}")
