# WhereDaBus SG

Group 51's INF2003 project combines PostgreSQL route searches with MongoDB arrival and crowding history. This repository starts from the 26 November 2026 submission. The included snapshot contains 85,724 arrival observations.

## Run the recorded demo on Windows

1. Start Docker Desktop in Linux containers mode. The first build needs internet access.
2. Double-click [Start-Review.cmd](WhereDaBusSG/Start-Review.cmd) and open the localhost address it prints. The launcher restores the packaged snapshot and checks the results. The recorded demo works without an LTA key.
3. To reproduce the report, open **Half-hour trends**, choose stop **66359**, service **70M**, and set both dates to **22 November 2026**. At 10:00 a.m. Singapore time, 26 of 30 observations qualified and all 26 reported standing room available.

See the [user manual](WhereDaBusSG/USER_MANUAL.txt) for the SQL route demo, live configuration, sample transfer and shutdown instructions. The editable report, PDF and PowerPoint are in [docs](docs/).

## Work with your teammates

Once the private GitHub repository is published, teammates with access can copy its HTTPS URL from **Code** and run `git clone <repository-url>` once. Open the cloned folder for future work.

Run `git pull --ff-only` before starting work. Check `git status`, stage the files you changed with `git add <file>`, commit with `git commit -m "Describe your change"`, then run `git push`. If two people change the same report or slide deck, coordinate who edits it since Git cannot automatically merge Office documents.

Git carries the source, documents and recorded snapshot. Each computer keeps its own live database and credentials. Use the export and restore launchers in the user manual to transfer newly collected observations.
