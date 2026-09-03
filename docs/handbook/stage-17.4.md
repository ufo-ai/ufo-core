# Office XLSX spreadsheet utilities  `stage-17.4`

This stage is behind-the-scenes support for working with Excel .xlsx files. It is not part of the main application loop by itself. Instead, other tools can call it when a spreadsheet needs to be checked or updated without opening a visible office window.

The package marker, __init__.py, is like a label on a toolbox. It tells Python that the scripts folder can be imported by other code, but it does not do any work on its own.

The _soffice.py file holds shared helpers for starting LibreOffice in “headless” mode, meaning LibreOffice runs in the background with no desktop window. It also knows where LibreOffice keeps user macros on Linux and macOS, so scripts do not have to repeat that platform-specific knowledge.

The recalc.py script is the worker. It opens an .xlsx spreadsheet in LibreOffice, forces formulas to calculate again, saves the updated file, and then looks for any formula errors that remain. Together, these files provide a small, reusable way to refresh and validate spreadsheets automatically.

## Files in this stage

### XLSX Recalculation Scripts
Package setup, LibreOffice helper utilities, and the spreadsheet recalculation entrypoint work together to refresh XLSX formulas and report errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Here, it sits inside the `office-xlsx` skill’s `scripts` directory, so script modules in that directory can be found and loaded using normal Python import rules. Think of it like a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and import style used elsewhere in the project, code might fail to recognize this folder as a package or might import its contents less reliably. Since the file is empty, it performs no setup, stores no data, and changes nothing at runtime beyond enabling package structure.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `during document script execution`

Some document tasks need LibreOffice to open or convert spreadsheet files in the background, like a worker in a back room rather than an app on screen. This file collects the common setup needed for that. Its main job is to prepare a safe environment for headless LibreOffice, find the folder where LibreOffice macros live, and run the `soffice` command-line program.

LibreOffice normally expects a graphical desktop. The helper `soffice_env` changes one environment setting so LibreOffice uses a simple off-screen display mode instead. That matters on servers or automated systems where there may be no visible desktop at all.

The `macro_dir` helper chooses the right LibreOffice macro folder for the current operating system. macOS and Linux keep this folder in different places, so the file hides that difference from the rest of the code.

Finally, `run_soffice` builds and runs a `soffice` command. It captures the program’s output and error text so callers can inspect what happened, and it can stop waiting after a timeout. Without this file, every script that needs LibreOffice would have to repeat these platform checks and process-starting details, increasing the chance of inconsistent or broken behavior.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Creates the environment settings used when launching LibreOffice in the background. It starts with the current process environment and adds the setting that tells LibreOffice to use an off-screen display mode.

**Data flow**: It reads the current environment variables from the operating system, copies them, adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, and returns the updated dictionary. It does not change the process-wide environment directly; it prepares a copy for a child LibreOffice process.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the right launch settings. The prepared environment is then handed to the external `soffice` command so it can run without needing a normal desktop window.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice stores user macros for the current operating system. This lets other code refer to the macro location without caring whether it is running on Linux or macOS.

**Data flow**: It asks the system what platform it is running on, looks up the matching macro folder path, expands the `~` home-directory shortcut into a real user path, and returns it as a `Path` object. If the platform is not recognized, it falls back to the Linux-style location.

**Call relations**: This helper stands alone as the file’s platform-aware path lookup. Code that needs to install, inspect, or use LibreOffice macros can call it and receive a ready-to-use filesystem path.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with caller-supplied arguments. It is the shared doorway for scripts that need LibreOffice to do work such as opening, converting, or processing documents.

**Data flow**: It receives a list of command arguments and an optional timeout. It adds `soffice` at the front to form the full command, asks `soffice_env` for the background-friendly environment, runs the command, captures standard output and error as text, and returns the completed process result. If a timeout is supplied and LibreOffice takes too long, the underlying process call can raise a timeout error.

**Call relations**: Higher-level document scripts call `run_soffice` when they need LibreOffice to perform an action. This function prepares the command and environment, then hands execution to Python’s process runner, which actually starts and waits for the external LibreOffice program.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand spreadsheet recalculation and validation`

Excel files can contain formulas whose saved results are stale or wrong until a spreadsheet program recalculates them. This file solves that problem by using LibreOffice in “headless” mode, meaning LibreOffice runs in the background without opening a visible window. It installs a small LibreOffice macro, like a tiny recorded command, that tells LibreOffice to calculate every formula, save the document, and close it.

Before running LibreOffice, the script takes a snapshot of table style information inside the .xlsx file. An .xlsx file is really a zip archive full of XML files, and LibreOffice can sometimes change or drop table style tags when saving. The snapshot-and-restore step protects those table styles so recalculation does not accidentally alter spreadsheet formatting.

After LibreOffice saves the workbook, the script opens the file with openpyxl, a Python library for reading Excel files. It scans cell values for common Excel error strings such as #REF! and #DIV/0!, counts how many formulas are present, and returns a JSON-friendly summary. From the command line, it prints that summary. Without this script, callers would have no simple automated way to refresh formulas and check whether the workbook still contains formula errors.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This function makes sure LibreOffice has the small macro needed to recalculate and save the spreadsheet. Someone would use it before asking LibreOffice to process a file, because the later recalculation command depends on this macro existing.

**Data flow**: It starts by asking where LibreOffice macros are stored. If the macro file already exists and contains the expected recalculation command, it returns success. If the macro folder is missing, it briefly starts LibreOffice in the background so LibreOffice creates its user folders, then writes the macro file. The output is a simple true or false: true means the macro is ready, false means setup failed.

**Call relations**: The main recalc flow calls this first, before it tries to run LibreOffice on the spreadsheet. It relies on helper code from _soffice to find the macro folder and build the right LibreOffice environment, and it uses subprocess.run to start LibreOffice once if the macro directory needs to be created.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This function saves copies of table style tags from inside the Excel file before LibreOffice touches it. It exists because recalculating through LibreOffice can change spreadsheet internals in ways that may affect table formatting.

**Data flow**: It receives the path to an .xlsx file. It opens that file as a zip archive, looks through the internal table XML files, and records any self-contained table style element it finds. It returns a dictionary where each table XML file name points to the original style bytes found there.

**Call relations**: The recalc function calls this before launching LibreOffice. Later, recalc gives the saved style information to _restore_table_styles so the file can be patched back after LibreOffice saves it.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This function inserts or replaces a table style tag inside one table XML document. It is the small repair step used when restoring formatting after LibreOffice has saved the workbook.

**Data flow**: It receives the raw XML bytes for one table file and the saved style element bytes. If the XML already has a table style element, it replaces that element with the saved one. If the style element is missing, it inserts the saved style just before the closing table tag. It returns the repaired XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not read or write files itself; it only edits the bytes handed to it.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This function puts saved table style information back into the Excel file after recalculation. It protects the workbook’s appearance from accidental changes made while LibreOffice rewrites the file.

**Data flow**: It receives the spreadsheet path and the dictionary of saved table styles. If there are no saved styles, it does nothing. Otherwise, it creates a temporary zip copy of the .xlsx file, rewrites each internal file, patches matching table XML files with _patch_table_style, then replaces the original file with the repaired copy. If something goes wrong, it removes the temporary file when possible.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. This function uses _patch_table_style for the actual XML repair, zipfile to read and write the .xlsx archive, and shutil.move to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This function looks through the recalculated workbook for common Excel error values. It helps the caller tell the difference between a clean recalculation and a spreadsheet that still contains broken formulas.

**Data flow**: It receives the spreadsheet path and opens the workbook with calculated values rather than formula text. It checks every cell in every sheet. When a cell contains text matching an Excel error such as #REF! or #DIV/0!, it records the sheet name and cell address. It returns a dictionary mapping each error type to the places where it was found.

**Call relations**: The recalc function calls this after LibreOffice has recalculated and saved the workbook. Its results are then condensed into the final success-or-errors summary returned by recalc.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This function counts how many formulas are in the workbook. The count gives useful context for the final report, for example whether a file with no errors actually had formulas to check.

**Data flow**: It receives the spreadsheet path and opens the workbook with formula text visible. It walks every cell in every sheet and counts string values that start with an equals sign, which is how Excel formulas are written. It returns the total number of formulas found.

**Call relations**: The recalc function calls this near the end, after scanning for errors. The returned count is included in the final JSON-style result.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work function: it recalculates an Excel file, saves it, checks for formula errors, and returns a structured result. Other code can call it directly instead of running the script from the command line.

**Data flow**: It receives a filename and an optional timeout. First it checks that the file exists. Then it makes sure the LibreOffice macro is installed, snapshots table styles, and runs LibreOffice in the background with the macro command. If LibreOffice times out or reports a failure, it returns an error message. If recalculation succeeds, it restores saved table styles, scans for Excel error values, counts formulas, and returns a dictionary showing success or errors_found, the total error count, the formula count, and a shortened list of error locations.

**Call relations**: main calls this when the script is run from the command line. Inside, it coordinates all helper functions in order: _ensure_macro, _snapshot_table_styles, the external _soffice.run_soffice call, _restore_table_styles, _scan_errors, and _count_formulas. It is the central pipeline that turns one spreadsheet path into a recalculation report.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This function is the command-line doorway into the script. It reads the user’s arguments, runs recalculation, and prints the result as JSON.

**Data flow**: It reads sys.argv, expecting an Excel file path and optionally a timeout in seconds. If the filename is missing, it prints a usage message and exits with an error code. Otherwise, it calls recalc with the chosen timeout, converts the returned dictionary to formatted JSON text, and prints it.

**Call relations**: This runs only when the file is executed directly as a Python script. It hands all real spreadsheet work to recalc, then uses json.dumps to make the result easy for people or other programs to read.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
