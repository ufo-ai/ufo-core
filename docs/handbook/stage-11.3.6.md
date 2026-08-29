# Spreadsheet recalculation helpers  `stage-11.3.6`

This stage is a behind-the-scenes support step for working with Excel spreadsheets. It is used when the system needs a workbook’s formulas to be up to date before another tool reads the file. Instead of asking a person to open the spreadsheet and press “recalculate,” it uses LibreOffice, a free office program, to do that work automatically.

The shared helper file, _soffice.py, is like the launcher and map. It starts LibreOffice in a quiet mode, without showing the normal desktop window, so scripts can use it as a background worker. It also knows where LibreOffice keeps user macros on Linux and macOS, which helps scripts find the right support files.

The recalc.py script is the main tool in this stage. It opens an Excel workbook, tells LibreOffice to refresh every formula, saves the updated workbook, and checks for remaining spreadsheet error values such as broken formulas. Together, these files make spreadsheet recalculation repeatable and machine-driven.

## Files in this stage

### Workbook recalculation
Shared LibreOffice launch helpers support the workbook recalculation script that refreshes formulas and reports remaining spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `called by document-processing scripts when they need to locate LibreOffice macros or run LibreOffice commands`

Some spreadsheet tasks need LibreOffice to be started from code, often on a server or automated worker where there is no visible desktop. This file is the common toolbox for doing that safely and consistently. It solves two practical problems: making LibreOffice run in a headless-friendly mode, and finding the folder where LibreOffice Basic macros live.

The `soffice_env` helper copies the current process environment and adds one important setting: `SAL_USE_VCLPLUGIN=svp`. In plain terms, this asks LibreOffice to use a simple non-graphical display backend, which helps it run without trying to attach to a real desktop. The `macro_dir` helper chooses the right macro folder path depending on the operating system, using the macOS location on Darwin and the Linux location otherwise. It expands `~` into the user’s home directory and returns a `Path`, which is Python’s safer way to represent filesystem paths.

Finally, `run_soffice` builds a command beginning with `soffice`, the LibreOffice command-line program, adds the caller’s arguments, and runs it. It captures the command’s output and returns the completed result, so other scripts can inspect whether LibreOffice succeeded, what it printed, and whether it timed out.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when launching LibreOffice. Its main job is to add a setting that makes LibreOffice use a lightweight, non-windowed display mode suitable for automation.

**Data flow**: It starts with a copy of the current process environment, so normal settings like paths and locale are preserved. It then adds or overwrites `SAL_USE_VCLPLUGIN` with `svp`. The result is a dictionary of environment variables ready to pass into a LibreOffice subprocess.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to prepare the launch environment. This keeps the special LibreOffice setting in one place instead of repeating it wherever LibreOffice is run.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice stores its standard user macros for the current operating system. Scripts can use this to install or find macros without hard-coding separate paths themselves.

**Data flow**: It checks the operating system name, chooses the matching LibreOffice macro directory template, and falls back to the Linux path if the system is not explicitly known. It expands the home-directory shortcut `~` into a real user path, then wraps the final string as a `Path` object for easier filesystem use.

**Call relations**: This helper stands on its own for any script that needs the LibreOffice macro location. It uses the platform check to decide which path to produce, then hands back a filesystem path for the caller to read from or write to.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with caller-provided arguments. It is the shared way for scripts to launch LibreOffice and capture what happened.

**Data flow**: It receives a list of command-line arguments and an optional timeout. It builds a full command starting with `soffice`, prepares the special environment through `soffice_env`, and runs the command while capturing text output and errors. It returns Python’s completed-process result, which includes the exit status, standard output, and standard error.

**Call relations**: Higher-level scripts call `run_soffice` when they need LibreOffice to do work, such as converting or manipulating spreadsheet files. Inside, it delegates environment preparation to `soffice_env` and hands the actual process launch to Python’s subprocess runner.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on demand during spreadsheet recalculation`

Excel files often contain formulas whose displayed results may be stale until a spreadsheet program recalculates them. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without showing a window. It installs a small LibreOffice macro if needed, then asks LibreOffice to open the workbook, calculate every formula, save it, and close it.

After that, the script checks the saved workbook for common Excel error strings such as #REF!, #DIV/0!, and #VALUE!. It returns a JSON-friendly report with the number of errors, where some of them are located, and how many formulas the workbook contains.

There is one important protective step: LibreOffice can sometimes disturb table style information inside .xlsx files. An .xlsx file is really a zip package full of XML files, like a folder squeezed into one file. Before recalculation, this script snapshots table style XML fragments. After LibreOffice saves the workbook, it patches those fragments back in where needed. Without this, recalculating formulas could accidentally change spreadsheet table formatting.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small Basic macro needed to recalculate and save the workbook. Without this macro, the script could start LibreOffice but would not have a reliable command for “calculate everything, save, and close.”

**Data flow**: It finds LibreOffice’s macro folder, checks whether the macro file already exists and contains the expected recalculation routine, and returns true if it is ready. If the folder does not exist yet, it briefly starts LibreOffice headlessly so LibreOffice can create its user profile, then writes the macro file. The output is a yes-or-no result showing whether the macro setup succeeded.

**Call relations**: The main recalc flow calls this before touching the workbook. To do its job, it asks the _soffice helper for the macro directory and environment, and it may launch LibreOffice through subprocess.run to initialize the profile.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This reads the Excel file before LibreOffice changes it and saves copies of table style XML snippets. It exists because LibreOffice may alter or drop these style snippets while saving the workbook.

**Data flow**: It receives a workbook path, opens the .xlsx file as a zip archive, and looks through the XML files that describe Excel tables. For each table file with a table style element, it stores the file name and the exact bytes of that style element. It returns a dictionary of saved style fragments.

**Call relations**: The recalc function calls this just before running LibreOffice. Its saved result is later passed to _restore_table_styles so formatting-related table metadata can be repaired after recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This inserts or replaces one table style snippet inside a table XML file. It is the small repair tool used when restoring table styles after LibreOffice has saved the workbook.

**Data flow**: It receives the raw XML bytes for one table file and the style element that should be present. If a table style element is already there, it replaces it. If none is present, it inserts the saved style just before the closing table tag. It returns the patched XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not open files itself; it only transforms one piece of XML data handed to it.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This repairs table style information in the workbook after LibreOffice recalculates and saves it. It helps preserve the original look and table metadata of the spreadsheet.

**Data flow**: It receives the workbook path and the saved style fragments. If there are no saved styles, it does nothing. Otherwise, it creates a temporary zip copy of the workbook, rewrites each file inside it, patches matching table XML files with _patch_table_style, and then replaces the original workbook with the repaired copy. If something goes wrong, it removes the temporary file if it exists.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. It relies on _patch_table_style for the actual XML edit, zipfile for reading and writing the .xlsx package, and shutil.move to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This checks the recalculated workbook for visible Excel error values, such as #DIV/0! or #REF!. It tells callers not just that errors exist, but where they were found.

**Data flow**: It receives a workbook path and opens it with openpyxl, reading calculated cell values rather than formulas. It walks through every sheet, row, and cell. When a cell contains one of the known error strings, it records the sheet name and cell address under that error type. It closes the workbook and returns a dictionary from error type to locations.

**Call relations**: The recalc function calls this after LibreOffice has recalculated and saved the file. The result becomes the basis for the final JSON report returned to the command-line user or any caller.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This counts how many formula cells are in the workbook. The count gives useful context: a file with many formulas and zero errors is different from a file with no formulas at all.

**Data flow**: It receives a workbook path and opens it with openpyxl in a mode that reads the formulas themselves. It scans every cell in every worksheet and counts strings that start with =, which is how Excel formulas are stored. It closes the workbook and returns the total count.

**Call relations**: The recalc function calls this while building the final success or error report. It complements _scan_errors: one reports what went wrong, while this reports how much formula content was checked.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work function for recalculating one Excel file and summarizing the result. Other code can call it directly instead of using the command line.

**Data flow**: It receives a filename and an optional timeout. First it checks that the file exists. Then it ensures the LibreOffice macro is installed, snapshots table styles, and runs LibreOffice headlessly with the macro command. If LibreOffice times out or fails, it returns an error dictionary. If recalculation succeeds, it restores table styles, scans for spreadsheet errors, counts formulas, and returns a dictionary describing success, total errors, formula count, and a limited list of error locations.

**Call relations**: main calls this after reading command-line arguments. Inside, recalc is the coordinator: it calls _ensure_macro for setup, _snapshot_table_styles and _restore_table_styles to protect table formatting, run_soffice to make LibreOffice do the recalculation, then _scan_errors and _count_formulas to build the final report.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This is the command-line entry point. It lets someone run the file as a script by passing an Excel filename and, optionally, a timeout in seconds.

**Data flow**: It reads command-line arguments from sys.argv. If no filename is provided, it prints a usage message and exits with an error code. Otherwise, it chooses the requested timeout or the default, calls recalc, converts the returned dictionary to formatted JSON text, and prints it.

**Call relations**: When this file is run directly, Python calls main through the final __main__ check. main is thin on purpose: it only handles command-line input and output, while recalc does the actual spreadsheet work.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
