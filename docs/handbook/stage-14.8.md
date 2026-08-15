# Excel XLSX recalculation tools  `stage-14.8`

This stage is a support tool for working with Excel .xlsx files after they have been created or changed. It is not the main user-facing work loop. Instead, it runs behind the scenes when the system needs spreadsheet formulas to be up to date and checked for obvious problems.

The scripts folder is made importable by __init__.py. That file is like a label on a toolbox: it does not do work itself, but it lets other Python code find the tools inside. The _soffice.py helper knows how to start LibreOffice in “headless” mode, meaning it runs without showing a window. It also knows where LibreOffice keeps user macro files on Linux and macOS, so scripts can find the right support folders. The recalc.py script is the main worker. It opens the workbook in LibreOffice, tells it to recalculate every formula, saves the updated file, and then looks for common spreadsheet error values. Together, these files turn LibreOffice into an automated checker and refresher for Excel workbooks.

## Files in this stage

### XLSX recalculation scripts
Package setup, shared LibreOffice helpers, and the workbook recalculation script work together to refresh formulas and report spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name.

Here, the drawer is the `scripts` folder inside the Office XLSX document extension. Even though this file does not define any functions, classes, or settings, it still matters because imports and package discovery may rely on its presence. Without it, depending on the Python version and how the project loads extensions, code in this folder might not be found or imported consistently.

There is no step-by-step runtime behavior here. Its job is structural: it helps organize the codebase and supports Python’s module loading rules.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `during document processing when scripts need to call LibreOffice`

LibreOffice can be controlled from the command line through a program called `soffice`. These helpers make that safer and more consistent for scripts that need to open or convert spreadsheet files in the background. Without this file, each script would have to remember the right environment setting, build the `soffice` command itself, and guess the platform-specific macro folder.

The file does three main things. First, it prepares a copy of the current environment variables and adds `SAL_USE_VCLPLUGIN=svp`. In plain terms, this tells LibreOffice to use a simple off-screen display backend, which helps it run “headless” — without showing a normal desktop window. Second, it chooses the right macro directory for the current operating system. macOS and Linux keep LibreOffice user macros in different home-folder locations, so this file hides that difference. Third, it provides one wrapper for actually launching `soffice` with arguments, collecting its printed output, and optionally stopping it if it runs too long.

Think of it like a travel adapter for LibreOffice: callers can ask for the same thing everywhere, and this file adjusts the plug shape for the local machine.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. It adds a setting that encourages LibreOffice to run without needing a normal visible desktop interface.

**Data flow**: It starts with the process’s current environment variables, copies them so the original is not changed, then adds `SAL_USE_VCLPLUGIN` with the value `svp`. It returns the finished dictionary of environment values for use when launching LibreOffice.

**Call relations**: When `run_soffice` is about to start the external `soffice` program, it calls `soffice_env` to get the right environment. That environment is then passed into the subprocess call so LibreOffice starts in the expected headless-friendly mode.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice user macros are stored on the current operating system. Scripts can use this when they need to install, inspect, or run LibreOffice macros.

**Data flow**: It reads the current platform name, chooses the matching macro path for macOS or Linux, expands `~` into the user’s home directory, and wraps the result as a `Path` object. The output is a usable filesystem path to LibreOffice’s standard macro folder.

**Call relations**: This helper stands alone in this file. Other scripts can call it when they need the platform-specific macro location, instead of duplicating the operating-system checks themselves.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with caller-provided arguments. It captures what LibreOffice prints and can enforce a timeout so a stuck office process does not run forever.

**Data flow**: It receives a list of command arguments and an optional timeout. It builds a command beginning with `soffice`, asks `soffice_env` for the proper environment, then starts the external process while capturing standard output and error as text. It returns a completed-process object containing the exit status and captured output.

**Call relations**: This is the main helper other scripts use when they need LibreOffice to do work. It prepares the command, relies on `soffice_env` for the right launch settings, and hands the final command to Python’s subprocess machinery to actually run LibreOffice.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`orchestration` · `command execution`

Excel files can contain formulas whose saved results are out of date, especially after another tool has edited the workbook without running Excel itself. This file solves that problem by asking LibreOffice, running in “headless” mode meaning without a visible window, to open the workbook, recalculate every formula, save it, and close it. Think of it like taking a spreadsheet to a calculator station: LibreOffice presses “recalculate all,” then this script inspects the answers.

To make LibreOffice do that reliably, the script first installs a small LibreOffice Basic macro if it is missing. A macro is a tiny script LibreOffice can run inside the document. Before recalculation, it also takes a snapshot of Excel table style snippets inside the `.xlsx` zip file, because LibreOffice may sometimes disturb those style tags when saving. After LibreOffice finishes, the script patches those style snippets back in.

Finally, it opens the workbook with `openpyxl`, a Python library for reading Excel files, and scans the calculated cell values for common Excel error strings such as `#REF!` and `#DIV/0!`. It returns a JSON-friendly dictionary with the status, total formulas, total errors, and up to a small sample of error locations. If anything important fails, such as the file not existing or LibreOffice timing out, it returns an error message instead.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small macro needed to recalculate and save the spreadsheet. Without this, the later LibreOffice run would not know which internal command to execute.

**Data flow**: It reads the expected LibreOffice macro file location from `_soffice.macro_dir`. If the macro file already exists and contains the expected recalculation routine, it reports success. If the macro folder is missing, it starts LibreOffice once in headless setup mode to create the user profile area, then writes the macro text into place. It returns `True` if the macro is ready and `False` if writing it failed.

**Call relations**: The main `recalc` workflow calls this before doing any spreadsheet work. It relies on `_soffice.macro_dir` and `_soffice.soffice_env` to find and prepare LibreOffice’s macro area, and uses `subprocess.run` only for the one-time initialization step when the macro folder does not yet exist.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This saves copies of table style tags from inside the Excel file before LibreOffice edits it. It exists to protect a small piece of Excel formatting that LibreOffice may accidentally remove or rewrite.

**Data flow**: It receives the path to an `.xlsx` file. Because an `.xlsx` file is really a zip archive containing XML files, it opens the zip, looks through table definition files under `xl/tables/`, and records any matching table style XML snippet. It returns a dictionary where each table file name points to the original style bytes found there.

**Call relations**: `recalc` calls this just before asking LibreOffice to recalculate the workbook. The saved style snippets are later handed to `_restore_table_styles`, which uses them to repair the workbook after LibreOffice has saved it.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This inserts or replaces one saved table style tag in a table XML file. It is the small repair tool used when restoring Excel table formatting after LibreOffice has touched the file.

**Data flow**: It receives the raw bytes of one table XML file and the original style element bytes. If the XML already has a table style tag, it replaces that tag with the saved one. If the tag is missing, it inserts the saved style just before the closing `</table>` tag. It returns the patched XML bytes.

**Call relations**: `_restore_table_styles` calls this for each table XML file that had a saved style. It does not read files itself; it only performs the byte-level patch that the restoration step needs.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This puts the saved Excel table style tags back into the workbook after LibreOffice recalculates and saves it. It helps preserve formatting details that are not central to formulas but matter to the final spreadsheet.

**Data flow**: It receives the path to the workbook and the style snapshots collected earlier. If there are no saved styles, it does nothing. Otherwise, it creates a temporary zip file, copies every entry from the workbook into it, patches any table XML file that has a saved style, and then replaces the original workbook with the repaired version. If an error happens, it removes the temporary file if needed and leaves the function without raising that cleanup problem further.

**Call relations**: `recalc` calls this after LibreOffice has successfully saved the recalculated workbook. Inside the repair pass it delegates the actual XML change to `_patch_table_style`, then uses zip-file reading/writing and `shutil.move` to swap the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This checks the recalculated workbook for common Excel error values such as `#REF!`, `#VALUE!`, and `#DIV/0!`. It tells the caller not just whether errors exist, but where they appear.

**Data flow**: It receives a workbook path and opens it with `openpyxl` using `data_only=True`, which means it reads the saved formula results rather than the formula text. It walks every worksheet, row, and cell. When a cell’s value is a string containing a known Excel error, it records the sheet name and cell coordinate under that error type. It closes the workbook and returns a dictionary of error types to location lists.

**Call relations**: After LibreOffice recalculates the file, `recalc` calls this to inspect the outcome. Its results become the basis for the final status, total error count, and per-error summary returned to the command-line caller.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This counts how many formula cells are in the workbook. The count gives the final report useful context, for example showing that a workbook had hundreds of formulas but no remaining errors.

**Data flow**: It receives a workbook path and opens it with `openpyxl` using `data_only=False`, which means it reads the formula text rather than only the saved results. It scans all cells and increments a counter whenever a cell contains a string starting with `=`. It closes the workbook and returns the final count.

**Call relations**: `recalc` calls this near the end, after scanning for errors, so the returned report can include `total_formulas`. It is separate from `_scan_errors` because one needs formula text while the other needs calculated values.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main workflow for recalculating one Excel file and producing a structured report. It coordinates setup, LibreOffice execution, formatting preservation, error scanning, and final summary creation.

**Data flow**: It receives a filename and an optional timeout in seconds. First it checks that the file exists. Then it ensures the LibreOffice macro is installed, snapshots table styles, and runs LibreOffice headlessly with the macro command and the workbook path. If LibreOffice times out or fails, it returns an error dictionary. If recalculation succeeds, it restores saved table styles, scans for spreadsheet errors, counts formulas, and returns a dictionary showing either `success` or `errors_found` along with totals and a compact error summary.

**Call relations**: `main` calls this after reading command-line arguments. This function is the hub: it calls `_ensure_macro` for setup, `_snapshot_table_styles` and `_restore_table_styles` to protect formatting, `_soffice.run_soffice` to perform the actual recalculation, and `_scan_errors` plus `_count_formulas` to build the final report.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the script. It reads the user’s arguments, runs the recalculation workflow, and prints the result as formatted JSON.

**Data flow**: It reads `sys.argv`. If no Excel filename was provided, it prints a usage message and exits with an error code. Otherwise, it takes the filename and an optional timeout value, calls `recalc`, converts the returned dictionary to pretty-printed JSON, and writes it to standard output.

**Call relations**: This runs only when the file is executed directly as a script. It hands all real spreadsheet work to `recalc`, then uses `json.dumps` to make the result easy for people or other programs to read.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).
