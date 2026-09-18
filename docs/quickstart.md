# Quickstart

This guide describes the basic steps for using the current features in the deployed application.

## 1. Introduction

Navigate to the URL of the deployed application.

There are two separate interfaces to the application:

1. The Django Admin interface, where experiments can be uploaded and edited.
2. The reporting frontend, with visualizations and (tbd) project/experiment details.

Note: permissions have not yet been implemented. Once this is added, both the administration interface and the frontend will use the same user system for authentication and access control.

Since the project visualizations requires experiment data to have been processed, we will start with the admin interface.

# Admin interface

The Django admin interface is accessible by navigating directly to the `/admin` page.

Login with a username/password provided by the administrators.

## Upload experiment data

There are two stages for adding new experiments to the database:

1. `Experiment Ingest`: for parsing metadata and then manually correcting it.
2. Promote to `Experiment`: after the metadata is validated/corrected, promoting parses and processes the measurement data and populates the entities that hold experiment data.

Adding new experiments is done manually per single experiment for now.
After the automatic metadata detection and manual verification workflow have been verified to work correctly, bulk uploading of Ingests can be added as a feature.
Verification of the metadata before the Ingest can be promoted to Experiment will remain a manual step.

### Experiment Ingest

- In the Admin bar on the left, click `+ Add` for `Experiment ingests`.
- Upload a layout xlsx and baseline + exposure CSV files, and set (or create) the Project the experiment belongs to.
- Click Save. The metadata is parsed from the filenames and layout file contents. You are then directed to the listing of Experiment Ingests.
- Click the newly added Experiment Ingest. You can change any of the editable fields.
  If metadata parsing worked correctly, you should only have to set `Exposure type`, and maybe change `Control` to the actual control chemical.

### Promoting to Experiment

- After the details of the Experiment Ingest have been verified, go back to the listing.
  Clicking `Experiment ingests` on the left takes you there.
- Check the checkbox for one or more verified ingests, and select the "Promote selected ingests to Experiments" action on top of the listing.
  When you click `Go`, the measurement data will be parsed and processed.
- If successful, a new `Experiment` and will have been created. Besides `Experiment`, the `Neuronal Metrics Frames` and `Conditions` contain experiment details. New `Chemicals` and `Concentration Units` are added when encountered.
  If something went wrong, the error is displayed on the detail page of the ingest you tried to promote.
- If one of the metadata fields was incorrect, run the `Parse/reparse selected uploads` action on the ingest, then correct the details and retry promotion.
  TODO: add a way to clear the error without reparsing the data.

## Projects

The main feature is currently organizing the experiments for the reporting views, and capturing a few project details.

Once permission handling is added, setting collaborators on a project can be used for access controls.

The default value for the `Outlier method` used in project reports can be configured.

## Neuronal Metrics Frames

You can inspect the recalculated well-level metrics and the control-normalized dataframe.

# Reporting interface

The reporting interface is visible on the front page.
The main entry point is the list of projects.

Click to a project detail page to see list of experiments contained.

The main feature is currently "View report" for project visualizations.
Which details should be displayed on the project detail page and individual experiment pages is to be determined.
