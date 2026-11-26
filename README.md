# WhereDaBus SG

## Run the submission on Windows

1. Extract `Source_Data_Manual.zip` or open your cloned repository, then open the `WhereDaBusSG` application folder.
2. Double-click [Setup.cmd](WhereDaBusSG/Setup.cmd). Setup starts Docker Desktop if needed and opens the app in your browser.

Docker Desktop for Windows with Linux containers must be installed once. The first build needs internet access.

On a fresh PC, Setup loads the included PostgreSQL and MongoDB snapshot automatically without an LTA key. **Morning crowding** opens with service **860**, stop **59059** and **18 November 2026** already selected.

If you already use the live app in this folder, Setup keeps your existing routes and adds missing observations within the retention period. If the recorded morning has expired from the live database, Setup opens the archived results automatically.

Use the menu to explore **Direct routes**, **Saved journeys**, or **Half-hour trends**. The [user manual](WhereDaBusSG/USER_MANUAL.txt) gives a trip and a chart you can reproduce from the report.

## Project files

- [WhereDaBusSG/](WhereDaBusSG/) contains the application, database setup, evidence, and 85,724 recorded arrival observations.
- [docs/](docs/) contains the report and slides.
- [START_HERE.txt](WhereDaBusSG/START_HERE.txt) explains the separate setup for collecting new LTA arrivals.

## Working together

Clone the team repository once. Run `git pull --ff-only` before editing. Use `git status` to check your changes, then `git add <file>`, `git commit -m "Describe your change"`, and `git push`. Coordinate edits to the Word and PowerPoint files so two people do not overwrite the same document.

The repository carries the recorded snapshot. Each teammate's live database and LTA credentials stay on their own computer.
