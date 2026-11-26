WhereDaBusSG - INF2003 source-code submission helper

This utility makes the SOURCE CODE deliverable from your installed final app.
It does not change your application code. It needs your running databases to
include the actual data used in your project. The application ZIP contains
synthetic test fixtures, so it is not by itself the complete data submission.

HOW TO USE

1. This utility is already included in the standalone application. After
   completing Setup.cmd, open the submission-tools folder. Do not move the
   scripts; they automatically find the parent application folder.

2. Start Docker Desktop and the application. If routes have not been imported,
   run Refresh-Data.cmd and let it finish successfully.

3. In Live data mode, open Live arrivals and select real stops. Keep the current
   stop refreshing while you export: the live cache is short-lived. Stop codes
   do not need to be entered manually; use the stop-name search.

4. In submission-tools, double-click Create-Submission.cmd and enter your actual group ID/number.
   Wait for the message 'Created source + data + user manual ZIP'.
   The file is saved in your project's submission folder, for example:
   submission/Group_009_WhereDaBusSG_Source_20261115-150000-abcdef12.zip

5. Open that ZIP and review its contents, especially data/EXPORT_METADATA.json,
   the SQL/Mongo snapshots, and USER_MANUAL.txt. Extract it to another machine
   or clean environment and follow the manual to verify your final submission.

The source ZIP contains one WhereDaBusSG folder with all files from the final
release's source manifest, current source-file edits, the exported bus dataset,
and a user manual covering Docker setup, configuration and how to run the app.
No earlier patches are required by the marker.

INCLUDED DATA

- PostgreSQL transport schema, queries, constraints and stored bus data.
- Four bus-arrival MongoDB collections in Extended JSON Lines format. Empty
  optional history collections are permitted; a completely empty Mongo export
  is refused so you know you need to look up live arrivals first.
- Data provenance/inspection notes, export timestamp and record counts.
- Original timestamps are kept. Exported arrivals are historical snapshots;
  the application still needs a valid LTA key and internet for current data.

The helper reads the databases and creates export files; it does not modify
database records or the application. It uses a temporary SQL file inside the
PostgreSQL container and removes that file when finished. A staging copy is
kept in .submission-build for inspection. Upload only the completed .zip in
submission, not a .partial file or the staging directory.

EXCLUDED

- Your real .env, LTA AccountKey and database credentials.
- Personal SQL users, favourite_routes and saved_journeys records (schema definitions remain).
- System libraries, installed dependencies, Python caches, Docker images,
  raw Docker volumes and unrelated files outside the final source manifest.

If you added new application source files after receiving the final version,
add their relative paths to FILE_MANIFEST.json before exporting. Existing source
file edits are copied as they are; the helper generates fresh hashes. Do not
put secrets in source code. Never add .env or personal data to that manifest.

IF IT STOPS

- Missing files: keep this submission-tools folder inside the complete
  standalone application and finish Setup.cmd. The project must contain
  FILE_MANIFEST.json.
- No saved arrivals: look up real stops in Live data mode and run the helper
  immediately. Try a stop with current services; keep the browser open.
- Missing static data: complete Refresh-Data.cmd, then retry.
- Docker error: check Docker Desktop and that the app is running.
- Invalid .env.example: restore that template from the final ZIP. It must have
  blank key and password placeholders. Keep your private .env unchanged.
- Any failure: no completed new ZIP is announced. Fix the reported issue and
  rerun. Existing successful ZIPs and your database are retained.

HOW TO SUBMIT THE WHOLE ASSIGNMENT

The project brief (pages 2-4) describes TWO ZIP levels:

Group_009_Final_Submission.zip
  Final_Report.pdf
  Presentation.pptx
  Presentation.mp4
  Group_009_WhereDaBusSG_Source_<timestamp>.zip

Create a folder for the four items above, select them, then right-click and
choose Compress to ZIP file (or Send to > Compressed (zipped) folder).
Upload that OUTER ZIP to your XSite group Dropbox.
The filenames above are suggestions; use your real group ID and any filename
rule supplied by your lecturer or XSite. Submit the generated source ZIP inside the overall submission ZIP.

The brief states Nov 29, 11:30 PM (2026 academic schedule), maximum 10 report
body pages excluding cover/references/appendix, and maximum 10 minutes of video.
All members must present. Its slide-description row specifies .pptx (the
submission column uses the older *.ppt shorthand). Keep the PowerPoint source.
For a submission too large for LMS (>2G), the brief allows a GitHub link with
access granted. A repository link is not otherwise required by this wording.

VALIDATION LIMIT

18 Windows PowerShell packaging/failure checks passed using simulated Docker
responses, including archive hashes, credential exclusion, missing/empty data,
failed export commands, source-file preservation and standalone installation-ID validation. The latest manual naming
was also checked in a successful packaging run. No real Docker Engine or your
LTA database was available here, so
the actual data ZIP must be generated and checked on your running installation.

Export format references:
https://www.postgresql.org/docs/16/app-pgdump.html
https://www.mongodb.com/docs/database-tools/mongoimport/

Each standalone installation has a unique WDB_PROJECT identity. This tool
reads that identity from its parent folder and exports only that installation.
It does not use older wheredabus-fresh databases.
