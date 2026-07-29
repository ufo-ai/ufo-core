# Excel XLSX LibreOffice recalculation helpers  `stage-10.4.4`

This stage is behind-the-scenes support for working with Excel .xlsx spreadsheets. Its job is to use LibreOffice, a free office suite, as a quiet helper process to refresh spreadsheet formulas when the system needs trustworthy saved results.

The scripts folder is made importable by __init__.py. That file does not do work itself, but it lets other Python code find and reuse the tools in this folder.

The shared helper file, _soffice.py, is like the power switch and map for LibreOffice. It provides common code for starting LibreOffice “headlessly,” meaning without showing its normal desktop window. It also knows where LibreOffice keeps user macro files on Linux and macOS, so related scripts can find the right support locations.

The main worker is recalc.py. It opens an Excel file in LibreOffice, tells LibreOffice to recalculate every formula, saves the updated file, and checks for remaining spreadsheet error values. Together, these pieces turn LibreOffice into an automatic calculator for spreadsheets.

## Files in this stage

### LibreOffice recalculation scripts
Package setup and shared LibreOffice helpers support the entrypoint that recalculates XLSX formulas and reports spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package, like putting a label on a drawer so other parts of the program know they can look inside it. Here, it belongs to the `office-xlsx` skill area, which suggests this folder is meant to hold scripts related to Excel `.xlsx` document work. Nothing runs from this file directly, and it defines no functions or classes. Its value is structural: without it, some Python import setups may not recognize the `scripts` directory as a package, which could make nearby scripts harder or impossible to import reliably.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `script execution`

Some document scripts need LibreOffice to do work in the background, such as opening or converting spreadsheet files. This file keeps the LibreOffice-specific setup in one place so other scripts do not have to repeat it or guess the right settings.

The main job is to run the `soffice` command, which is LibreOffice’s command-line program. Before doing that, the file prepares a safe environment variable: `SAL_USE_VCLPLUGIN` is set to `svp`, which tells LibreOffice to use a headless, non-graphical display layer. In plain terms, it is like asking LibreOffice to work in the kitchen instead of coming out to the dining room: it can still cook the document, but it does not need to show a window.

The file also provides the expected folder for LibreOffice Basic macros. That location differs between macOS and Linux, so `macro_dir` checks the operating system and returns the matching user folder, falling back to the Linux path if the system is unfamiliar.

Without this file, every script that calls LibreOffice would need to remember the same environment setting, command-building pattern, and macro-folder rules. That would make the scripts more fragile and harder to keep consistent.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It copies the current process environment and adds the setting that makes LibreOffice run without needing a normal graphical window.

**Data flow**: It starts with the current operating-system environment variables. It makes a copy, adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, and returns that new dictionary. It does not change the original environment for the whole program.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the right environment. The returned settings are then passed into `subprocess.run` so the external `soffice` program starts in the intended headless mode.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice stores standard user macros for the current operating system. Scripts can use this when they need to install, find, or prepare LibreOffice macros.

**Data flow**: It asks the system what platform it is running on, such as macOS or Linux. It looks up the matching macro-folder template, expands `~` into the user’s home directory, turns the result into a `Path` object, and returns it.

**Call relations**: This helper stands on its own for code that needs the LibreOffice macro location. Inside it, `platform.system` supplies the operating-system name, and `pathlib.Path` turns the chosen text path into a path object that other file code can use safely.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program with the given arguments and returns the completed result. It is the shared doorway for scripts that need to ask LibreOffice to do background work.

**Data flow**: It receives a list of command arguments, plus an optional timeout. It puts `soffice` at the front to form the full command, prepares the headless LibreOffice environment with `soffice_env`, then starts the external process. When the process finishes, it returns a `CompletedProcess` object containing the exit status and captured text output and error output.

**Call relations**: Other scripts can call `run_soffice` instead of calling `subprocess.run` directly. During that call, `run_soffice` asks `soffice_env` for the correct environment, then hands the command to `subprocess.run`, which actually launches LibreOffice and waits for it to finish.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand command-line spreadsheet recalculation`

Excel files can contain formulas whose saved results are out of date. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without opening a visible window. It installs a small LibreOffice macro if needed, asks that macro to recalculate every formula in the workbook, saves the file, and then checks the saved values for common spreadsheet errors such as #REF! or #DIV/0!.

There is one important extra safeguard: Excel table style information can sometimes be changed or lost when LibreOffice rewrites a spreadsheet. Before recalculation, the script takes a small snapshot of those table style XML snippets from inside the .xlsx file, which is really a zipped collection of files. After LibreOffice saves the workbook, the script puts those snippets back.

After recalculation, it uses openpyxl, a Python library for reading Excel files, to scan the workbook. It counts formulas and records where error values appear, limiting the displayed locations so the output stays readable. The final result is a JSON-friendly dictionary saying whether recalculation succeeded, whether errors remain, and where they were found. The script can also be run directly from the command line.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small Basic macro needed to recalculate and save the workbook. Without this macro, the script could start LibreOffice but would not have a reliable command to tell it, “recalculate everything, save, then close.”

**Data flow**: It looks for the expected macro file in LibreOffice’s macro folder. If the file already exists and contains the recalculation routine, it returns true. If the folder is missing, it briefly starts LibreOffice in headless setup mode so the user profile folders are created, then writes the macro file. It returns true if setup worked, or false if writing the macro failed.

**Call relations**: The main `recalc` flow calls this before touching the spreadsheet in LibreOffice. It relies on `_soffice.macro_dir` to find where the macro belongs, `_soffice.soffice_env` to run LibreOffice with the right environment, and `subprocess.run` to initialize LibreOffice if its folders do not exist yet.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This saves a copy of Excel table style markers before LibreOffice rewrites the file. It exists because recalculating should update formulas, not accidentally change how tables are styled.

**Data flow**: It receives the path to an Excel file. It opens the .xlsx as a zip archive, reads table XML files under `xl/tables/`, and stores any table style element it finds in a dictionary keyed by the internal file name. It returns that dictionary of saved style snippets.

**Call relations**: `recalc` calls this just before running LibreOffice. The saved styles are later passed to `_restore_table_styles`, which uses them to repair table XML after the recalculation step.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This inserts or replaces one saved table style snippet inside a table XML file. It is the small repair tool used when restoring the workbook’s table formatting.

**Data flow**: It receives raw XML bytes from one table file and the saved style element for that table. If the XML already has a table style element, it replaces it. If it does not, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: `_restore_table_styles` calls this while rebuilding the spreadsheet zip file. It does not read or write files itself; it only transforms one piece of XML at a time.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This puts saved Excel table style information back into the recalculated spreadsheet. It protects the workbook’s appearance from unwanted changes caused by LibreOffice’s save process.

**Data flow**: It receives the Excel file path and the dictionary of style snippets captured earlier. If there are no saved styles, it does nothing. Otherwise, it creates a temporary .xlsx zip file, copies every internal file from the original, patches matching table XML files, and then replaces the original file with the repaired temporary file. If something goes wrong, it removes the temporary file when possible.

**Call relations**: `recalc` calls this after LibreOffice successfully recalculates and saves the workbook. During the rebuild, it calls `_patch_table_style` for each table file that needs its style restored, then uses file-moving and zip-writing helpers to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This looks through the recalculated workbook for common Excel error values, such as broken references or division by zero. It turns hidden spreadsheet problems into a clear list of cell locations.

**Data flow**: It receives the workbook path and opens it with formula results loaded instead of formula text. It walks through every worksheet, row, and cell. When a cell’s value is text containing a known Excel error string, it records the sheet name and cell coordinate under that error type. It returns a dictionary mapping each error type to the places where it was found.

**Call relations**: `recalc` calls this after recalculation and table-style restoration. Its output becomes the basis for the final success or errors-found report.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This counts how many formulas are present in the workbook. The count gives useful context: a clean result from a workbook with many formulas means more than a clean result from a workbook with none.

**Data flow**: It receives the workbook path and opens it with formulas visible as formulas, not just calculated values. It scans every worksheet and every cell, counts string values that start with `=`, closes the workbook, and returns the total number.

**Call relations**: `recalc` calls this near the end, after checking for errors. The formula count is included in the final report returned to the caller.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work routine: given an Excel filename, it recalculates formulas through LibreOffice, preserves table styling when possible, scans for formula errors, and returns a structured result. Other code can call it directly instead of using the command-line interface.

**Data flow**: It receives a filename and an optional timeout. It first checks that the file exists, prepares the LibreOffice macro, snapshots table styles, then runs LibreOffice headlessly with the macro command and the workbook path. If LibreOffice fails or times out, it returns an error dictionary. If recalculation succeeds, it restores table styles, scans for Excel error values, counts formulas, and returns a dictionary with status, total errors, total formulas, and a short error summary.

**Call relations**: `main` calls this when the script is run from the command line. Inside, it coordinates all helper functions: `_ensure_macro` prepares LibreOffice, `_snapshot_table_styles` and `_restore_table_styles` protect formatting, `_scan_errors` checks the final workbook, and `_count_formulas` adds context to the final JSON output. It hands the actual LibreOffice launch to `_soffice.run_soffice`.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This is the command-line front door for the script. It reads arguments from the terminal, runs recalculation, and prints the result as formatted JSON.

**Data flow**: It reads `sys.argv` for the Excel filename and optional timeout. If no filename is supplied, it prints usage instructions and exits with an error code. Otherwise, it calls `recalc`, converts the returned dictionary to readable JSON text, and prints it to standard output.

**Call relations**: This function is used only when the file is executed as a script. It is the thin wrapper around `recalc`, turning command-line input into the internal function call and turning the returned result into output that humans or other tools can read.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
