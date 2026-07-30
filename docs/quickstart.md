# Quickstart

This guide describes the basic steps for testing the current NTX features in the
deployed OpenShift application.

## 1. Open the Application

Open the NTX application in your browser using the OpenShift application URL: ???

Useful pages:

- Project overview: `/projects/`
  - List of uploaded projects
  - The project name is linked to project detail page
  - Each experiment is linked to experiment condition detail page
  - If you have admin access, you can also check the project settings in the admin panel. The project-level outlier method acts as the default analysis setting
  - [View report button](#view-report)
- Experiment overview: `/experiments/`
  - List of experiments
  - Each experiment opens from the overview page
  - The experiment detail page shows the expected conditions
  - If you have admin access, you can also check the experiment settings in the admin panel

- [Admin panel](#admin-actions): `/admin/`
  -  Use a superuser account to access the Django admin panel
  - Project records can be viewed and edited
  - The project-level outlier method can be changed
  - Experiment and ingest records can be inspected
    - Experiment ingests:
      - Click on Add, for uploading new experiments
      - Parse or reparse selected uploads
      - Promote selected ingests with confirmed metadata to experiments
      - Promote selected ingests and replace existing experiments when needed
      - Download edited metadata as a JSON file for uploading to Yoda
    - Experiments:
      - Inspect promoted experiment records
      - Check linked projects, conditions, files, and metrics




# View report



