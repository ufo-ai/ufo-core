# XLSX LibreOffice Recalculation  `stage-12.2.4`

This stage is a behind-the-scenes support step for working with Excel files. It is used when the system needs the values in a workbook to be fresh, not just whatever old results were stored in the file. LibreOffice is used as the spreadsheet engine, but it is run “headless,” meaning it starts without showing a normal desktop window.

The `_soffice.py` helper is the toolbelt for this. It knows how to launch LibreOffice from scripts in that quiet mode, and it also knows where LibreOffice keeps user macro files on Linux and macOS. That path knowledge matters when spreadsheet automation needs macro support.

The `recalc.py` script is the main worker. It opens the Excel workbook in LibreOffice, tells LibreOffice to recalculate all formulas, saves the workbook, and then checks for spreadsheet error values that are still present. Together, these files act like a workshop: one sets up the machinery, and the other runs the recalculation job and reports what still looks broken.

## Files in this stage

### LibreOffice Recalculation Scripts
Shared headless LibreOffice helpers support the workbook recalculation entrypoint that refreshes formulas and reports remaining spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `during document script execution`

Some spreadsheet-related scripts need to ask LibreOffice to do work in the background, such as opening or converting office files. This file is the common toolbox for that job. Without it, each script would have to repeat the same operating-system checks and command setup, which would make behavior less consistent and harder to fix.

The main idea is simple: prepare LibreOffice so it can run “headless,” meaning without showing a visible app window, then call the `soffice` command-line program. The file also points scripts to the folder where LibreOffice stores Basic macros. That folder is different on macOS and Linux, so the code chooses the right path based on the current operating system and expands `~` into the user’s home directory.

One important detail is the environment variable `SAL_USE_VCLPLUGIN` being set to `svp`. In plain terms, this tells LibreOffice to use a non-graphical display backend, like asking it to work at a service counter instead of opening a full storefront. This helps automated scripts run reliably on servers or background workers where no normal screen is available.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It copies the current process environment, then adds the setting that encourages LibreOffice to run without a normal graphical interface.

**Data flow**: It starts with the existing operating-system environment variables. It makes a copy, adds `SAL_USE_VCLPLUGIN` with the value `svp`, and returns that modified dictionary for another function to use when launching LibreOffice.

**Call relations**: When `run_soffice` is about to start the `soffice` command, it calls `soffice_env` to get the right background-friendly environment. The result is handed directly to the process launcher so LibreOffice starts with those settings.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice keeps its standard user macros on the current operating system. Scripts can use this when they need to install, read, or refer to LibreOffice macros.

**Data flow**: It checks the operating system name, chooses the matching macro-folder template for macOS or Linux, expands the `~` home-directory shortcut into a real path, and returns it as a `Path` object, which is Python’s convenient way to work with file paths.

**Call relations**: This helper stands on its own for scripts that need to locate LibreOffice’s macro storage. Internally it asks the platform library what system it is running on, then wraps the chosen location in a path object so callers can use it for file operations.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with the given arguments. It captures the command’s text output and can stop waiting if a timeout is supplied.

**Data flow**: It receives a list of command arguments and an optional timeout. It puts `soffice` at the front to form the full command, asks `soffice_env` for the background-friendly environment, runs the command, captures standard output and error as text, and returns the completed-process result, including exit status and captured output.

**Call relations**: Other document scripts can call `run_soffice` whenever they need LibreOffice to do work. This function gathers the command, the safe environment from `soffice_env`, and Python’s process runner into one reusable step, then hands the caller the result of that external LibreOffice run.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on demand during Excel workbook recalculation and validation`

Excel files can contain formulas whose displayed results are stored as cached values. If a file was edited outside Excel, those cached results may be old or missing. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without showing a window, to recalculate and save the workbook.

The script first makes sure LibreOffice has a small Basic macro installed. That macro tells the open spreadsheet to calculate all formulas, save itself, and close. Before running LibreOffice, the script also takes a snapshot of Excel table style XML. This is a protective step: saving through LibreOffice can sometimes disturb table styling details, so the script keeps those pieces and puts them back afterward, like taking a photo of a neatly set table before moving it.

After recalculation, the script reads the workbook with openpyxl, a Python library for Excel files. It looks for common Excel error strings such as #REF! and #DIV/0!, records where they appear, counts all formulas, and returns a JSON-friendly summary. When run from the command line, it prints that summary. Without this file, an automated document workflow could think formulas are correct when they have not actually been recalculated, or miss broken formulas hidden inside the spreadsheet.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small recalculation macro that this script needs. Without this macro, LibreOffice would open the file but would not know to calculate everything, save, and close in the intended way.

**Data flow**: It finds the LibreOffice macro folder, checks whether the macro file already exists and contains the expected macro name, and returns true if it is ready. If the folder is missing, it starts LibreOffice once in headless setup mode to create the needed user folders, then writes the macro file. It returns true when the macro is available and false if writing it fails.

**Call relations**: The main recalc flow calls this before touching the spreadsheet. It relies on the helper functions from _soffice to locate LibreOffice’s macro folder and build the right environment, and it may call subprocess.run to initialize LibreOffice’s user profile.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the table styling snippets from inside an Excel file before LibreOffice saves it. This protects visual table formatting that LibreOffice may otherwise change or remove.

**Data flow**: It receives the path to an .xlsx file, opens it as a zip archive because Excel files are zip packages internally, and looks through the XML files for table definitions. For each table that has a tableStyleInfo element, it stores that exact XML snippet in a dictionary keyed by the file name inside the archive. The result is a small backup map of table style data.

**Call relations**: recalc calls this before running LibreOffice. Later, recalc passes the saved style snippets to _restore_table_styles so the workbook can regain those exact table style pieces after recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Puts one saved table style snippet back into a table XML document. It is a small helper used while rebuilding the Excel file after LibreOffice has saved it.

**Data flow**: It receives raw XML bytes for one table and the saved table style XML bytes. If the table already has a tableStyleInfo element, it replaces that element with the saved one. If the element is missing, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not read or write files itself; it only transforms one piece of XML data.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Reapplies saved Excel table styling after LibreOffice has recalculated and saved the workbook. This keeps the script from fixing formulas while accidentally damaging table appearance metadata.

**Data flow**: It receives the workbook path and the dictionary of saved style snippets. If there are no saved styles, it does nothing. Otherwise, it creates a temporary zip copy of the workbook, reads every file inside the original, patches table XML files that need their style restored, writes everything to the temporary file, and then replaces the original workbook with that repaired copy. If something goes wrong, it removes the temporary file if it exists.

**Call relations**: recalc calls this after LibreOffice finishes successfully. During the repair, it delegates the actual XML replacement to _patch_table_style and uses zipfile and shutil to rebuild and swap the workbook package.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for common Excel error values, such as #REF! or #DIV/0!. This tells the caller whether formulas still produced broken results after recalculation.

**Data flow**: It opens the workbook with calculated values enabled, so it reads what formulas currently evaluate to rather than the formula text itself. It walks through every worksheet, row, and cell. When a cell’s value is text containing one of the known Excel error strings, it records the sheet name and cell address under that error type. It closes the workbook and returns a dictionary of error types to locations.

**Call relations**: recalc calls this after LibreOffice has saved the recalculated file. The error list it returns is turned into the final status and summary that the command-line user or calling system receives.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formula cells are in the workbook. This gives useful context for the final report, for example showing whether a file had many formulas or none at all.

**Data flow**: It opens the workbook in a mode that reads the actual cell contents, not just calculated results. It checks every cell in every worksheet and counts strings that begin with =, which is how Excel formulas are written. It closes the workbook and returns the final count.

**Call relations**: recalc calls this near the end, after scanning for errors. Its count is included in the JSON-style result alongside the number of formula errors found.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full workbook recalculation and validation process for one Excel file. This is the main reusable function for callers that want a structured success, error, or warning result.

**Data flow**: It receives a file name and an optional timeout. First it checks that the file exists, prepares the LibreOffice macro, and snapshots table styles. It then starts LibreOffice headlessly with the macro so the workbook recalculates, saves, and closes. If LibreOffice times out or exits with an error, it returns an error dictionary. If recalculation succeeds, it restores table styles, scans for Excel error values, counts formulas, and returns a dictionary containing the status, total errors, total formulas, and a short error summary with limited locations.

**Call relations**: main calls this when the script is run from the command line. Inside, it coordinates all helpers in order: _ensure_macro for setup, _snapshot_table_styles for protection, _soffice.run_soffice for the actual LibreOffice run, _restore_table_styles for cleanup, then _scan_errors and _count_formulas for the final report.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It lets a user or automation run recalculation by typing the script name, an Excel file path, and optionally a timeout.

**Data flow**: It reads command-line arguments from sys.argv. If no file path is provided, it prints usage instructions and exits with an error code. Otherwise, it parses the file name and timeout, calls recalc, converts the returned dictionary to pretty-printed JSON text, and prints it to standard output.

**Call relations**: This is called only when the file is executed directly as a script. It hands the real work to recalc and uses json.dumps so other tools can easily read the result.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
